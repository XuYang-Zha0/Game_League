from __future__ import annotations

import os
import re
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

import pymysql
import requests
from fastapi import APIRouter, FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def load_local_env() -> None:
    env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as env_file:
        for line in env_file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


load_local_env()


DB_CONFIG = {
    "host": os.getenv("CS_DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("CS_DB_PORT", "3306")),
    "user": os.getenv("CS_DB_USER", "root"),
    "password": os.getenv("CS_DB_PASSWORD", ""),
    "database": os.getenv("CS_DB_NAME", "esports"),
    "charset": "utf8mb4",
    "autocommit": True,
    "cursorclass": pymysql.cursors.DictCursor,
}

TEAM_RANK_FETCH_LIMIT = max(0, int(os.getenv("CS_TEAM_RANK_LIMIT", "500")))
MATCH_RESULT_FETCH_LIMIT = max(0, int(os.getenv("CS_MATCH_RESULT_LIMIT", "500")))
MATCH_FIXTURE_FETCH_LIMIT = max(0, int(os.getenv("CS_MATCH_FIXTURE_LIMIT", "500")))
TOURNAMENT_FETCH_LIMIT = max(0, int(os.getenv("CS_TOURNAMENT_LIMIT", "0")))
LIVE_SYNC_ENABLED = os.getenv("CS_LIVE_SYNC_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}
LIVE_SYNC_INTERVAL_SECONDS = max(15, int(os.getenv("CS_LIVE_SYNC_INTERVAL_SECONDS", "20")))
LIVE_SYNC_SCHEDULE_PAGES = max(1, int(os.getenv("CS_LIVE_SYNC_SCHEDULE_PAGES", "2")))
LIVE_SYNC_RESULT_PAGES = max(1, int(os.getenv("CS_LIVE_SYNC_RESULT_PAGES", "2")))
LIVE_SYNC_RECENT_HOURS = max(1, int(os.getenv("CS_LIVE_SYNC_RECENT_HOURS", "12")))
LIVE_SYNC_START_DATE_TEXT = os.getenv("CS_LIVE_SYNC_START_DATE", "").strip()
LIVE_SYNC_LOOKBACK_DAYS = max(1, int(os.getenv("CS_LIVE_SYNC_LOOKBACK_DAYS", "10")))
LIVE_SYNC_FUTURE_DAYS = max(1, int(os.getenv("CS_LIVE_SYNC_FUTURE_DAYS", "14")))
LIVE_SYNC_TIMEOUT_SECONDS = max(5, int(os.getenv("CS_LIVE_SYNC_TIMEOUT_SECONDS", "25")))
LIVE_SYNC_MIN_GAP_SECONDS = max(5, int(os.getenv("CS_LIVE_SYNC_MIN_GAP_SECONDS", "8")))
LIVE_SYNC_PAGE_SIZE = 20
LIVE_API_MATCH_LIMIT = max(20, int(os.getenv("CS_LIVE_API_MATCH_LIMIT", "240")))
SCHEDULE_API_MATCH_LIMIT = max(100, int(os.getenv("CS_SCHEDULE_API_MATCH_LIMIT", "3000")))
SCHEDULE_STALE_LIVE_HOURS = max(1, int(os.getenv("CS_SCHEDULE_STALE_LIVE_HOURS", "8")))
LIVE_API_LOOKBACK_HOURS = max(1, int(os.getenv("CS_LIVE_API_LOOKBACK_HOURS", "36")))
LIVE_API_LOOKAHEAD_HOURS = max(2, int(os.getenv("CS_LIVE_API_LOOKAHEAD_HOURS", "72")))
LIVE_SCHEDULE_URL = "https://app.5eplay.com/api/tournament/session_list"
LIVE_RESULT_URL = "https://app.5eplay.com/api/tournament/session_result_list"
LIVE_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://event.5eplay.com",
    "Referer": "https://event.5eplay.com/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/141.0.0.0 Safari/537.36"
    ),
}

LIVE_SYNC_STATE: Dict[str, Any] = {
    "enabled": LIVE_SYNC_ENABLED,
    "startedAt": "",
    "lastRunAt": "",
    "lastError": "",
    "scheduleUpserted": 0,
    "resultUpserted": 0,
}
LIVE_SYNC_STOP_EVENT = threading.Event()
LIVE_SYNC_THREAD: Optional[threading.Thread] = None
LIVE_SYNC_LOCK = threading.Lock()
LIVE_SYNC_LAST_TRIGGER_TS = 0.0


app = FastAPI(title="Game League API", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

router = APIRouter()


def get_conn() -> pymysql.Connection:
    return pymysql.connect(**DB_CONFIG)


def safe_date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    return ""


def safe_datetime(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    return ""


def json_scalar(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    return value


def json_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {k: json_scalar(v) for k, v in row.items()}


def table_exists(
    cur: pymysql.cursors.DictCursor, table_name: str, cache: Set[str]
) -> bool:
    if table_name in cache:
        return True
    cur.execute("SHOW TABLES LIKE %s", (table_name,))
    exists = cur.fetchone() is not None
    if exists:
        cache.add(table_name)
    return exists


def table_columns(cur: pymysql.cursors.DictCursor, table_name: str) -> Set[str]:
    cur.execute(f"SHOW COLUMNS FROM `{table_name}`")
    rows = cur.fetchall()
    cols: Set[str] = set()
    for row in rows:
        if isinstance(row, dict):
            cols.add(str(row.get("Field") or ""))
        elif isinstance(row, (list, tuple)) and row:
            cols.add(str(row[0]))
    return {c for c in cols if c}


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def format_trend(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "0"
    try:
        iv = int(float(text))
        if iv > 0:
            return f"+{iv}"
        return str(iv)
    except ValueError:
        return text


def format_metric(value: Any, digits: int = 2) -> str:
    text = str(value or "").strip()
    if not text:
        return "-"
    if text.endswith("%"):
        return text
    try:
        return f"{float(text):.{digits}f}"
    except (TypeError, ValueError):
        return text


def format_rank(value: Any) -> Any:
    text = str(value or "").strip()
    if not text:
        return "-"
    try:
        rank_value = int(float(text))
    except (TypeError, ValueError):
        return "-"
    return rank_value if rank_value > 0 else "-"


def metric_percent_to_raw(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    if text.endswith("%"):
        text = text[:-1]
    try:
        num = float(text)
    except (TypeError, ValueError):
        return 0.0
    if num > 1:
        num = num / 100.0
    return max(0.0, min(num, 1.0))


def metric_number(value: Any) -> Optional[float]:
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("%", "").replace("+-", "-")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except (TypeError, ValueError):
        return None


def is_truthy_flag(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def clamp_float(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def infer_cs_impact(
    *,
    rating: Any = None,
    kpr: Any = None,
    dpr: Any = None,
    kast: Any = None,
    adr: Any = None,
) -> Optional[float]:
    rating_value = metric_number(rating)
    kpr_value = metric_number(kpr)
    dpr_value = metric_number(dpr)
    kast_value = metric_number(kast)
    adr_value = metric_number(adr)

    if (
        rating_value is not None
        and kpr_value is not None
        and dpr_value is not None
        and kast_value is not None
        and adr_value is not None
    ):
        impact = (
            rating_value
            - (
                0.0073 * kast_value
                + 0.3591 * kpr_value
                - 0.5329 * dpr_value
                + 0.0032 * adr_value
                + 0.1587
            )
        ) / 0.2372
        return clamp_float(impact, 0.0, 3.0)

    if kpr_value is not None and adr_value is not None:
        dpr_penalty = 0.0 if dpr_value is None else 0.35 * (dpr_value - 0.67)
        impact = (2.13 * kpr_value) + (0.0032 * adr_value) - dpr_penalty - 0.63
        if impact <= 0 and rating_value is not None:
            return clamp_float(rating_value, 0.0, 3.0)
        return clamp_float(impact, 0.0, 3.0)

    if rating_value is not None:
        return clamp_float(rating_value, 0.0, 3.0)

    return None


def metric_row_value(rows_by_metric: Dict[str, Dict[str, Any]], metric: str, field: str) -> Any:
    return (rows_by_metric.get(metric) or {}).get(field)


def midpoint_metric(row: Dict[str, Any], *fields: str) -> Optional[float]:
    values = [metric_number(row.get(field)) for field in fields]
    values = [value for value in values if value is not None]
    if not values:
        return None
    return sum(values) / len(values)


def normalize_cs_performance_metrics(
    rows: List[Dict[str, Any]],
    basic: Dict[str, Any],
) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    rows_by_metric: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        metric = str(item.get("metric") or "").strip().lower()
        if not metric:
            continue
        item["metric"] = metric
        item["lower_better"] = "1" if is_truthy_flag(item.get("lower_better")) else "0"
        normalized.append(item)
        rows_by_metric.setdefault(metric, item)

    impact_row = rows_by_metric.get("impact")
    if not impact_row:
        impact_row = {
            "metric": "impact",
            "value": "",
            "avg_value": "",
            "lower_better": "0",
            "bad_start": "0.96",
            "bad_end": "0.80",
            "middle_start": "1.13",
            "middle_end": "0.97",
            "good_start": "1.30",
            "good_end": "1.14",
        }
        normalized.append(impact_row)
        rows_by_metric["impact"] = impact_row
    else:
        impact_row["lower_better"] = "0"

    impact_value = metric_number(impact_row.get("value"))
    if impact_value is None or impact_value <= 0.05:
        inferred = infer_cs_impact(
            rating=metric_row_value(rows_by_metric, "rating", "value") or basic.get("rating"),
            kpr=metric_row_value(rows_by_metric, "kpr", "value") or basic.get("kpr"),
            dpr=metric_row_value(rows_by_metric, "dpr", "value") or basic.get("dpr"),
            kast=metric_row_value(rows_by_metric, "kast", "value") or basic.get("kast"),
            adr=metric_row_value(rows_by_metric, "adr", "value") or basic.get("adr"),
        )
        if inferred is not None and inferred > 0:
            impact_row["value"] = f"{inferred:.2f}"

    impact_avg = metric_number(impact_row.get("avg_value"))
    if impact_avg is None or impact_avg <= 0.05:
        inferred_avg = infer_cs_impact(
            rating=metric_row_value(rows_by_metric, "rating", "avg_value"),
            kpr=metric_row_value(rows_by_metric, "kpr", "avg_value"),
            dpr=metric_row_value(rows_by_metric, "dpr", "avg_value"),
            kast=metric_row_value(rows_by_metric, "kast", "avg_value"),
            adr=metric_row_value(rows_by_metric, "adr", "avg_value"),
        )
        if inferred_avg is None:
            inferred_avg = midpoint_metric(impact_row, "middle_start", "middle_end")
        if inferred_avg is not None and inferred_avg > 0:
            impact_row["avg_value"] = f"{inferred_avg:.2f}"

    if basic and (metric_number(basic.get("impact")) is None or metric_number(basic.get("impact")) <= 0.05):
        basic_impact = metric_number(impact_row.get("value"))
        if basic_impact is not None and basic_impact > 0:
            basic["impact"] = f"{basic_impact:.2f}"

    return normalized


def build_live_http_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1.1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


LIVE_HTTP_SESSION = build_live_http_session()


def parse_unix_timestamp(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    try:
        ts = int(value)
    except (TypeError, ValueError):
        return None
    try:
        return datetime.fromtimestamp(ts)
    except (OverflowError, OSError, ValueError):
        return None


def parse_datetime_text(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text, fmt)
            if fmt == "%Y-%m-%d":
                return dt.replace(hour=0, minute=0, second=0, microsecond=0)
            return dt
        except ValueError:
            continue
    return None


def now_page_token() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def next_page_token(last_ts: Any) -> Optional[str]:
    dt = parse_unix_timestamp(last_ts)
    if not isinstance(dt, datetime):
        return None
    return (dt - timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S")


def live_get_json(url: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        resp = LIVE_HTTP_SESSION.get(
            url,
            headers=LIVE_HEADERS,
            params=params,
            timeout=(10, LIVE_SYNC_TIMEOUT_SECONDS),
        )
        if resp.status_code != 200:
            return None
        payload = resp.json()
        if isinstance(payload, dict):
            return payload
        return None
    except (requests.RequestException, ValueError):
        return None


def build_schedule_row(match: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    mc = match.get("mc_info", {}) or {}
    state = match.get("state", {}) or {}
    tt = match.get("tt_info", {}) or {}
    match_id = str(mc.get("id") or "").strip()
    if not match_id:
        return None

    t1 = mc.get("t1_info", {}) or {}
    t2 = mc.get("t2_info", {}) or {}
    return {
        "match_id": match_id,
        "match_time": parse_unix_timestamp(mc.get("plan_ts")),
        "bo": mc.get("format"),
        "team1_id": str(t1.get("id") or "").strip() or None,
        "team1": str(t1.get("disp_name") or "").strip() or None,
        "team2_id": str(t2.get("id") or "").strip() or None,
        "team2": str(t2.get("disp_name") or "").strip() or None,
        "event_id": str(tt.get("id") or "").strip() or None,
        "event_name": str(tt.get("disp_name") or "").strip() or None,
        "event_logo": str(tt.get("logo") or "").strip() or None,
        "event_start_time": parse_datetime_text(tt.get("start_time")),
        "event_end_time": parse_datetime_text(tt.get("end_time")),
        "score1": state.get("t1_score"),
        "score2": state.get("t2_score"),
        "status": state.get("status"),
    }


def build_result_row(match: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    base = build_schedule_row(match)
    if not base:
        return None

    state = match.get("state", {}) or {}
    bout_states = state.get("bout_states", []) or []
    if not isinstance(bout_states, list):
        bout_states = []
    bout_summary = []
    for bout in bout_states:
        bout_summary.append(
            f"{bout.get('map_name', '')}:{bout.get('t1_score', '')}-{bout.get('t2_score', '')}(winner:{bout.get('result', '')})"
        )

    return {
        **base,
        "bout_count": len(bout_states),
        "bout_details": " | ".join(bout_summary) if bout_summary else None,
    }


def dedupe_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    bucket: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        match_id = str(row.get("match_id") or "").strip()
        if not match_id:
            continue
        bucket[match_id] = row
    return list(bucket.values())


def resolve_live_sync_start_dt() -> datetime:
    if LIVE_SYNC_START_DATE_TEXT:
        parsed = parse_datetime_text(LIVE_SYNC_START_DATE_TEXT)
        if isinstance(parsed, datetime):
            return parsed
    return datetime.now() - timedelta(days=LIVE_SYNC_LOOKBACK_DAYS)


def resolve_live_sync_end_dt() -> datetime:
    return datetime.now() + timedelta(days=LIVE_SYNC_FUTURE_DAYS)


def fetch_live_schedule_rows() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    start_dt = resolve_live_sync_start_dt()
    end_dt = resolve_live_sync_end_dt()
    for page in range(1, LIVE_SYNC_SCHEDULE_PAGES + 1):
        payload = live_get_json(
            LIVE_SCHEDULE_URL,
            {
                "game_status": 1,
                "game_type": 1,
                "grades": "",
                "page": page,
                "limit": LIVE_SYNC_PAGE_SIZE,
            },
        )
        if not payload:
            break

        matches = payload.get("data", {}).get("matches", []) or []
        if not isinstance(matches, list) or not matches:
            break

        for match in matches:
            row = build_schedule_row(match)
            if not row:
                continue
            status_code = safe_int(row.get("status"), -1)
            match_time = row.get("match_time")
            if status_code == 1:
                rows.append(row)
                continue
            if isinstance(match_time, datetime) and start_dt <= match_time <= end_dt:
                rows.append(row)
    return dedupe_rows(rows)


def fetch_live_result_rows() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    start_dt = resolve_live_sync_start_dt()
    now_dt = datetime.now()
    page_token = now_page_token()
    for _ in range(LIVE_SYNC_RESULT_PAGES):
        payload = live_get_json(
            LIVE_RESULT_URL,
            {
                "game_type": 1,
                "order_by": "asc",
                "grades": "",
                "page_size": LIVE_SYNC_PAGE_SIZE,
                "page_token": page_token,
            },
        )
        if not payload:
            break

        matches = payload.get("data", {}).get("matches", []) or []
        if not isinstance(matches, list) or not matches:
            break

        should_stop = False
        for match in matches:
            row = build_result_row(match)
            if not row:
                continue
            match_time = row.get("match_time")
            if isinstance(match_time, datetime) and start_dt <= match_time <= now_dt:
                rows.append(row)
            elif isinstance(match_time, datetime) and match_time < start_dt:
                should_stop = True

        last_ts = matches[-1].get("mc_info", {}).get("plan_ts")
        next_token = next_page_token(last_ts)
        if not next_token:
            break
        page_token = next_token

        if should_stop:
            break

    return dedupe_rows(rows)


def upsert_event_basic(cur: pymysql.cursors.DictCursor, rows: List[Dict[str, Any]]) -> int:
    event_map: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        event_id = str(row.get("event_id") or "").strip()
        if not event_id:
            continue
        event_name = str(row.get("event_name") or "").strip()
        event_logo = str(row.get("event_logo") or "").strip()
        event_start_time = row.get("event_start_time")
        event_end_time = row.get("event_end_time")
        if event_id not in event_map:
            event_map[event_id] = {
                "event_name": event_name or event_id,
                "event_logo": event_logo,
                "start_time": event_start_time,
                "end_time": event_end_time,
            }
            continue

        if event_name:
            event_map[event_id]["event_name"] = event_name
        if event_logo:
            event_map[event_id]["event_logo"] = event_logo
        if isinstance(event_start_time, datetime):
            old_start = event_map[event_id].get("start_time")
            if not isinstance(old_start, datetime) or event_start_time < old_start:
                event_map[event_id]["start_time"] = event_start_time
        if isinstance(event_end_time, datetime):
            old_end = event_map[event_id].get("end_time")
            if not isinstance(old_end, datetime) or event_end_time > old_end:
                event_map[event_id]["end_time"] = event_end_time
    if not event_map:
        return 0

    available_columns = table_columns(cur, "event_basic")
    has_logo = "event_logo" in available_columns
    has_start = "start_time" in available_columns
    has_end = "end_time" in available_columns

    column_sql = ["event_id", "event_name"]
    if has_logo:
        column_sql.append("event_logo")
    if has_start:
        column_sql.append("start_time")
    if has_end:
        column_sql.append("end_time")

    values = []
    for eid, row in event_map.items():
        item: List[Any] = [eid, row.get("event_name")]
        if has_logo:
            item.append(row.get("event_logo"))
        if has_start:
            item.append(row.get("start_time"))
        if has_end:
            item.append(row.get("end_time"))
        values.append(tuple(item))

    update_parts = [
        "event_name = COALESCE(NULLIF(VALUES(event_name), ''), event_basic.event_name)",
    ]
    if has_logo:
        update_parts.append(
            "event_logo = COALESCE(NULLIF(VALUES(event_logo), ''), event_basic.event_logo)"
        )
    if has_start:
        update_parts.append(
            "start_time = CASE "
            "WHEN VALUES(start_time) IS NULL THEN event_basic.start_time "
            "WHEN event_basic.start_time IS NULL THEN VALUES(start_time) "
            "WHEN VALUES(start_time) < event_basic.start_time THEN VALUES(start_time) "
            "ELSE event_basic.start_time END"
        )
    if has_end:
        update_parts.append(
            "end_time = CASE "
            "WHEN VALUES(end_time) IS NULL THEN event_basic.end_time "
            "WHEN event_basic.end_time IS NULL THEN VALUES(end_time) "
            "WHEN VALUES(end_time) > event_basic.end_time THEN VALUES(end_time) "
            "ELSE event_basic.end_time END"
        )

    col_list = ", ".join(column_sql)
    placeholders = ", ".join(["%s"] * len(column_sql))
    update_sql = ",\n                ".join(update_parts)
    cur.executemany(
        f"""
        INSERT INTO event_basic ({col_list})
        VALUES ({placeholders})
        ON DUPLICATE KEY UPDATE
                {update_sql}
        """,
        values,
    )
    return len(values)


def upsert_team_basic(cur: pymysql.cursors.DictCursor, rows: List[Dict[str, Any]]) -> int:
    team_map: Dict[str, str] = {}
    for row in rows:
        for id_key, name_key in (("team1_id", "team1"), ("team2_id", "team2")):
            team_id = str(row.get(id_key) or "").strip()
            if not team_id:
                continue
            team_name = str(row.get(name_key) or "").strip() or team_id
            team_map[team_id] = team_name
    if not team_map:
        return 0

    now = datetime.now()
    values = [(tid, tname, now) for tid, tname in team_map.items()]
    cur.executemany(
        """
        INSERT INTO team_basic (team_id, team_name, crawl_time)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE
            team_name = COALESCE(NULLIF(VALUES(team_name), ''), team_basic.team_name),
            crawl_time = COALESCE(VALUES(crawl_time), team_basic.crawl_time)
        """,
        values,
    )
    return len(values)


def upsert_match_schedule(cur: pymysql.cursors.DictCursor, rows: List[Dict[str, Any]]) -> int:
    if not rows:
        return 0
    values = [
        (
            row.get("match_id"),
            row.get("match_time"),
            row.get("bo"),
            row.get("team1_id"),
            row.get("team1"),
            row.get("team2_id"),
            row.get("team2"),
            row.get("event_id"),
            row.get("event_name"),
            row.get("score1"),
            row.get("score2"),
            row.get("status"),
        )
        for row in rows
    ]
    cur.executemany(
        """
        INSERT INTO match_schedule (
            match_id, match_time, bo, team1_id, team1, team2_id, team2,
            event_id, event_name, score1, score2, status
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            match_time = VALUES(match_time),
            bo = VALUES(bo),
            team1_id = COALESCE(NULLIF(VALUES(team1_id), ''), match_schedule.team1_id),
            team1 = CASE
                WHEN UPPER(TRIM(COALESCE(VALUES(team1), ''))) IN ('', '-', 'TBD', 'UNKNOWN')
                THEN match_schedule.team1
                ELSE VALUES(team1)
            END,
            team2_id = COALESCE(NULLIF(VALUES(team2_id), ''), match_schedule.team2_id),
            team2 = CASE
                WHEN UPPER(TRIM(COALESCE(VALUES(team2), ''))) IN ('', '-', 'TBD', 'UNKNOWN')
                THEN match_schedule.team2
                ELSE VALUES(team2)
            END,
            event_id = COALESCE(NULLIF(VALUES(event_id), ''), match_schedule.event_id),
            event_name = COALESCE(NULLIF(VALUES(event_name), ''), match_schedule.event_name),
            score1 = COALESCE(VALUES(score1), match_schedule.score1),
            score2 = COALESCE(VALUES(score2), match_schedule.score2),
            status = CASE
                WHEN match_schedule.status = 2 AND VALUES(status) = 1 THEN match_schedule.status
                ELSE COALESCE(VALUES(status), match_schedule.status)
            END
        """,
        values,
    )
    return len(values)


def upsert_match_result(cur: pymysql.cursors.DictCursor, rows: List[Dict[str, Any]]) -> int:
    if not rows:
        return 0
    values = [
        (
            row.get("match_id"),
            row.get("match_time"),
            row.get("bo"),
            row.get("team1_id"),
            row.get("team1"),
            row.get("team2_id"),
            row.get("team2"),
            row.get("event_id"),
            row.get("event_name"),
            row.get("score1"),
            row.get("score2"),
            row.get("status"),
            row.get("bout_count"),
            row.get("bout_details"),
        )
        for row in rows
    ]
    cur.executemany(
        """
        INSERT INTO match_result (
            match_id, match_time, bo, team1_id, team1, team2_id, team2,
            event_id, event_name, score1, score2, status, bout_count, bout_details
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            match_time = VALUES(match_time),
            bo = VALUES(bo),
            team1_id = COALESCE(NULLIF(VALUES(team1_id), ''), match_result.team1_id),
            team1 = CASE
                WHEN UPPER(TRIM(COALESCE(VALUES(team1), ''))) IN ('', '-', 'TBD', 'UNKNOWN')
                THEN match_result.team1
                ELSE VALUES(team1)
            END,
            team2_id = COALESCE(NULLIF(VALUES(team2_id), ''), match_result.team2_id),
            team2 = CASE
                WHEN UPPER(TRIM(COALESCE(VALUES(team2), ''))) IN ('', '-', 'TBD', 'UNKNOWN')
                THEN match_result.team2
                ELSE VALUES(team2)
            END,
            event_id = COALESCE(NULLIF(VALUES(event_id), ''), match_result.event_id),
            event_name = COALESCE(NULLIF(VALUES(event_name), ''), match_result.event_name),
            score1 = COALESCE(VALUES(score1), match_result.score1),
            score2 = COALESCE(VALUES(score2), match_result.score2),
            status = CASE
                WHEN match_result.status = 2 AND VALUES(status) = 1 THEN match_result.status
                ELSE COALESCE(VALUES(status), match_result.status)
            END,
            bout_count = COALESCE(VALUES(bout_count), match_result.bout_count),
            bout_details = COALESCE(NULLIF(VALUES(bout_details), ''), match_result.bout_details)
        """,
        values,
    )
    return len(values)


def sync_live_matches_once() -> Dict[str, int]:
    schedule_rows = fetch_live_schedule_rows()
    result_rows = fetch_live_result_rows()
    combined_rows = schedule_rows + result_rows
    if not combined_rows:
        return {"schedule": 0, "result": 0}

    with get_conn() as conn:
        with conn.cursor() as cur:
            upsert_event_basic(cur, combined_rows)
            upsert_team_basic(cur, combined_rows)
            schedule_count = upsert_match_schedule(cur, schedule_rows)
            result_count = upsert_match_result(cur, result_rows)

    return {"schedule": schedule_count, "result": result_count}


def run_live_sync_once() -> Dict[str, int]:
    counts = sync_live_matches_once()
    LIVE_SYNC_STATE["lastRunAt"] = safe_datetime(datetime.now())
    LIVE_SYNC_STATE["lastError"] = ""
    LIVE_SYNC_STATE["scheduleUpserted"] = counts.get("schedule", 0)
    LIVE_SYNC_STATE["resultUpserted"] = counts.get("result", 0)
    return counts


def maybe_trigger_live_sync(force: bool = False) -> bool:
    global LIVE_SYNC_LAST_TRIGGER_TS
    if not LIVE_SYNC_ENABLED:
        return False

    now_ts = time.time()
    if not force and now_ts - LIVE_SYNC_LAST_TRIGGER_TS < LIVE_SYNC_MIN_GAP_SECONDS:
        return False

    acquired = LIVE_SYNC_LOCK.acquire(blocking=False)
    if not acquired:
        return False

    try:
        now_ts = time.time()
        if not force and now_ts - LIVE_SYNC_LAST_TRIGGER_TS < LIVE_SYNC_MIN_GAP_SECONDS:
            return False
        run_live_sync_once()
        LIVE_SYNC_LAST_TRIGGER_TS = time.time()
        return True
    except Exception as exc:
        LIVE_SYNC_STATE["lastRunAt"] = safe_datetime(datetime.now())
        LIVE_SYNC_STATE["lastError"] = str(exc)
        LIVE_SYNC_LAST_TRIGGER_TS = time.time()
        return False
    finally:
        LIVE_SYNC_LOCK.release()


def live_sync_loop() -> None:
    while not LIVE_SYNC_STOP_EVENT.is_set():
        maybe_trigger_live_sync(force=True)
        LIVE_SYNC_STOP_EVENT.wait(LIVE_SYNC_INTERVAL_SECONDS)


def start_live_sync_worker() -> None:
    global LIVE_SYNC_THREAD
    if not LIVE_SYNC_ENABLED:
        return
    if LIVE_SYNC_THREAD and LIVE_SYNC_THREAD.is_alive():
        return

    LIVE_SYNC_STOP_EVENT.clear()
    LIVE_SYNC_STATE["enabled"] = True
    LIVE_SYNC_STATE["startedAt"] = safe_datetime(datetime.now())
    LIVE_SYNC_THREAD = threading.Thread(target=live_sync_loop, name="cs2-live-sync", daemon=True)
    LIVE_SYNC_THREAD.start()


def stop_live_sync_worker() -> None:
    global LIVE_SYNC_THREAD
    LIVE_SYNC_STOP_EVENT.set()
    if LIVE_SYNC_THREAD and LIVE_SYNC_THREAD.is_alive():
        LIVE_SYNC_THREAD.join(timeout=3.0)
    LIVE_SYNC_THREAD = None


def latest_rank_rows(cur: pymysql.cursors.DictCursor, limit: int = TEAM_RANK_FETCH_LIMIT) -> List[Dict[str, Any]]:
    sql = """
        WITH ranked AS (
            SELECT
                trs.*,
                ROW_NUMBER() OVER (PARTITION BY trs.team_id ORDER BY trs.id DESC) AS rn
            FROM team_rank_snapshot trs
        )
        SELECT
            ranked.team_id,
            COALESCE(ranked.team_name, tb.team_name) AS team_name,
            COALESCE(ranked.team_logo, tb.team_logo) AS team_logo,
            COALESCE(ranked.country_logo, tb.country_logo) AS country_logo,
            COALESCE(tb.region_name, 'Global') AS region_name,
            ranked.global_rank,
            ranked.valve_rank,
            ranked.score,
            ranked.point,
            ranked.rank_change
        FROM ranked
        LEFT JOIN team_basic tb ON tb.team_id = ranked.team_id
        WHERE ranked.rn = 1
        ORDER BY
            CASE
                WHEN ranked.global_rank IS NULL
                  OR CAST(ranked.global_rank AS CHAR) = ''
                  OR CAST(ranked.global_rank AS SIGNED) <= 0
                THEN 1 ELSE 0
            END ASC,
            CAST(ranked.global_rank AS SIGNED) ASC,
            ranked.id DESC
    """
    if limit > 0:
        sql += "\nLIMIT %s"
        cur.execute(sql, (limit,))
    else:
        cur.execute(sql)
    return list(cur.fetchall())


def latest_stat_rows(cur: pymysql.cursors.DictCursor) -> Dict[str, Dict[str, Any]]:
    cur.execute(
        """
        WITH ranked AS (
            SELECT
                tss.*,
                ROW_NUMBER() OVER (PARTITION BY tss.team_id ORDER BY tss.id DESC) AS rn
            FROM team_stat_snapshot tss
        )
        SELECT
            team_id,
            map_num,
            kd,
            rating,
            win_rate,
            map_win_rate,
            avg_kill,
            avg_death,
            first_kill_rate
        FROM ranked
        WHERE rn = 1
        """
    )
    rows = list(cur.fetchall())
    return {str(row["team_id"]): row for row in rows}


def build_leaderboard(
    rank_rows: List[Dict[str, Any]], stat_map: Dict[str, Dict[str, Any]]
) -> List[Dict[str, Any]]:
    leaderboard = []
    for idx, row in enumerate(rank_rows, start=1):
        team_id = str(row.get("team_id") or "")
        stat = stat_map.get(team_id, {})
        global_rank_value = format_rank(row.get("global_rank"))
        valve_rank_value = format_rank(row.get("valve_rank"))
        map_num_value = stat.get("map_num")
        kd_value = stat.get("kd")
        rating_value = stat.get("rating")
        win_rate_value = stat.get("win_rate")
        map_win_rate_value = stat.get("map_win_rate")
        avg_kill_value = stat.get("avg_kill")
        avg_death_value = stat.get("avg_death")
        first_kill_rate_value = stat.get("first_kill_rate")
        win_rate_text = format_metric(win_rate_value, 1)
        win_rate_raw = metric_percent_to_raw(win_rate_value)
        leaderboard.append(
            {
                "teamId": row.get("team_id") or "",
                "rank": global_rank_value if global_rank_value != "-" else idx,
                "hltvRank": global_rank_value,
                "valveRank": valve_rank_value,
                "name": row.get("team_name") or row.get("team_id") or "-",
                "logo": str(row.get("team_logo") or "").strip(),
                "region": row.get("region_name") or "Global",
                "points": str(safe_int(row.get("score"))),
                "winRate": win_rate_text,
                "winRateRaw": win_rate_raw,
                "trend": format_trend(row.get("rank_change")),
                "mapNum": "-" if map_num_value is None else safe_int(map_num_value),
                "kd": "-" if kd_value is None else f"{safe_float(kd_value):.2f}",
                "rating": "-" if rating_value is None else f"{safe_float(rating_value):.2f}",
                "mapWinRate": format_metric(map_win_rate_value, 1),
                "avgKill": format_metric(avg_kill_value, 1),
                "avgDeath": format_metric(avg_death_value, 1),
                "firstKillRate": format_metric(first_kill_rate_value, 1),
            }
        )
    return leaderboard


def build_teams(rank_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    teams = []
    for row in rank_rows:
        teams.append(
            {
                "teamId": row.get("team_id") or "",
                "hltvRank": format_rank(row.get("global_rank")),
                "valveRank": format_rank(row.get("valve_rank")),
                "name": row.get("team_name") or row.get("team_id") or "-",
                "logo": str(row.get("team_logo") or "").strip(),
                "region": row.get("region_name") or "Global",
            }
        )
    return teams


def build_players(
    cur: pymysql.cursors.DictCursor, rank_rows: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    rank_map_by_id = {
        str(row.get("team_id") or ""): safe_int(row.get("global_rank"), 999)
        for row in rank_rows
    }
    rank_map_by_name = {
        str(row.get("team_name") or ""): safe_int(row.get("global_rank"), 999)
        for row in rank_rows
    }

    table_cache: Set[str] = set()
    base_by_id: Dict[str, Dict[str, Any]] = {}

    def merge_player_row(row: Dict[str, Any], source_priority: int) -> None:
        player_id = str(row.get("player_id") or "").strip()
        if not player_id:
            return
        row = dict(row)
        row["player_id"] = player_id
        row["source_priority"] = source_priority
        existing = base_by_id.get(player_id)
        if not existing:
            base_by_id[player_id] = row
            return
        if source_priority < safe_int(existing.get("source_priority"), 99):
            merged = {**row}
            for key, value in existing.items():
                if key not in merged or merged.get(key) in (None, ""):
                    merged[key] = value
            base_by_id[player_id] = merged
            return
        for key, value in row.items():
            existing_empty = existing.get(key) in (None, "", 0)
            if key == "impact":
                existing_impact = metric_number(existing.get(key))
                incoming_impact = metric_number(value)
                existing_empty = existing_empty or existing_impact is None or existing_impact <= 0.05
                if incoming_impact is None or incoming_impact <= 0.05:
                    continue
            if existing_empty and value not in (None, ""):
                existing[key] = value

    if table_exists(cur, "player_basic", table_cache):
        cur.execute(
            """
            SELECT
                pb.player_id,
                pb.name AS player_name,
                pb.team_id,
                COALESCE(pb.team_name, tb.team_name) AS team_name,
                COALESCE(NULLIF(pb.position, ''), pb.positions) AS position,
                pb.rating,
                pb.impact,
                pb.adr,
                pb.kpr,
                pb.dpr,
                pb.kast,
                pb.maps_played,
                COALESCE(NULLIF(pb.portrait, ''), NULLIF(pb.half_portrait, '')) AS avatar
            FROM player_basic pb
            LEFT JOIN team_basic tb ON tb.team_id = pb.team_id
            WHERE pb.player_id IS NOT NULL AND pb.player_id <> ''
            """
        )
        for row in cur.fetchall():
            merge_player_row(row, 0)

    if table_exists(cur, "team_player_relation", table_cache):
        cur.execute(
            """
            SELECT
                tpr.player_id,
                MAX(NULLIF(tpr.player_name, '')) AS player_name,
                MAX(NULLIF(tpr.team_id, '')) AS team_id,
                COALESCE(MAX(NULLIF(tpr.team_name, '')), MAX(NULLIF(tb.team_name, ''))) AS team_name,
                NULL AS position,
                NULL AS rating,
                NULL AS impact,
                NULL AS adr,
                NULL AS kpr,
                NULL AS dpr,
                NULL AS kast,
                0 AS maps_played,
                MAX(NULLIF(tpr.player_portrait, '')) AS avatar
            FROM team_player_relation tpr
            LEFT JOIN team_basic tb ON tb.team_id = tpr.team_id
            WHERE tpr.player_id IS NOT NULL AND tpr.player_id <> ''
            GROUP BY tpr.player_id
            """
        )
        for row in cur.fetchall():
            merge_player_row(row, 1)

    if table_exists(cur, "match_result_player_stats", table_cache):
        cur.execute(
            """
            SELECT
                mrps.player_id,
                MAX(NULLIF(mrps.player_name, '')) AS player_name,
                MAX(NULLIF(mrps.team_id, '')) AS team_id,
                COALESCE(MAX(NULLIF(mrps.team_name, '')), MAX(NULLIF(tb.team_name, ''))) AS team_name,
                NULL AS position,
                AVG(NULLIF(mrps.rating, '')) AS rating,
                AVG(NULLIF(mrps.impact, '')) AS impact,
                AVG(NULLIF(mrps.adr, '')) AS adr,
                AVG(NULLIF(mrps.kpr, '')) AS kpr,
                NULL AS dpr,
                AVG(NULLIF(mrps.kast, '')) AS kast,
                COUNT(*) AS maps_played,
                MAX(NULLIF(mrps.country_logo, '')) AS avatar
            FROM match_result_player_stats mrps
            LEFT JOIN team_basic tb ON tb.team_id = mrps.team_id
            WHERE mrps.player_id IS NOT NULL AND mrps.player_id <> ''
            GROUP BY mrps.player_id
            """
        )
        for row in cur.fetchall():
            merge_player_row(row, 2)

    if table_exists(cur, "match_result_map_player_stats", table_cache):
        cur.execute(
            """
            SELECT
                mrmp.player_id,
                MAX(NULLIF(mrmp.player_name, '')) AS player_name,
                MAX(NULLIF(mrmp.team_id, '')) AS team_id,
                COALESCE(MAX(NULLIF(mrmp.team_name, '')), MAX(NULLIF(tb.team_name, ''))) AS team_name,
                NULL AS position,
                AVG(NULLIF(mrmp.rating, '')) AS rating,
                NULL AS impact,
                AVG(NULLIF(mrmp.adr, '')) AS adr,
                AVG(NULLIF(mrmp.kpr, '')) AS kpr,
                NULL AS dpr,
                AVG(NULLIF(mrmp.kast, '')) AS kast,
                COUNT(*) AS maps_played,
                MAX(NULLIF(mrmp.country_logo, '')) AS avatar
            FROM match_result_map_player_stats mrmp
            LEFT JOIN team_basic tb ON tb.team_id = mrmp.team_id
            WHERE mrmp.player_id IS NOT NULL AND mrmp.player_id <> ''
            GROUP BY mrmp.player_id
            """
        )
        for row in cur.fetchall():
            merge_player_row(row, 3)

    base_rows = list(base_by_id.values())

    def resolve_team_rank(row: Dict[str, Any]) -> int:
        team_id = str(row.get("team_id") or "")
        team_name = str(row.get("team_name") or "")
        if team_id and team_id in rank_map_by_id:
            return rank_map_by_id[team_id]
        return rank_map_by_name.get(team_name, 999)

    def sample_weight(map_count: int) -> float:
        # Reduce score inflation for players with too few maps.
        if map_count <= 0:
            return 0.62
        raw = (map_count / (map_count + 30.0)) ** 0.5
        return max(0.55, min(raw, 1.0))

    def team_weight(team_rank: int) -> float:
        # Slightly prefer stronger teams so unknown/low-ranked teams do not
        # dominate solely by a few high-rating matches.
        if team_rank >= 999:
            return 0.74
        rank_value = max(1, team_rank)
        raw = 0.72 + 0.28 / (1.0 + (rank_value - 1) / 40.0)
        return max(0.72, min(raw, 1.0))

    def rank_score(row: Dict[str, Any]) -> float:
        rating_value = safe_float(row.get("rating"), 0.0)
        if rating_value <= 0:
            return 0.0
        maps_played = max(0, safe_int(row.get("maps_played"), 0))
        t_rank = resolve_team_rank(row)
        return rating_value * sample_weight(maps_played) * team_weight(t_rank)

    base_rows.sort(
        key=lambda row: (
            -rank_score(row),
            -safe_float(row.get("rating"), 0.0),
            resolve_team_rank(row),
            -safe_int(row.get("maps_played"), 0),
            str(row.get("player_name") or row.get("player_id") or ""),
        )
    )

    # Return all available players from DB; frontend handles display slicing where needed.
    selected_rows = base_rows

    players = []
    for idx, row in enumerate(selected_rows, start=1):
        team_name = row.get("team_name") or "-"
        team_rank = resolve_team_rank(row)
        maps_played = max(0, safe_int(row.get("maps_played"), 0))
        score_value = rank_score(row)
        s_weight = sample_weight(maps_played)
        t_weight = team_weight(team_rank)

        rating_value = safe_float(row.get("rating"), 0.0)
        impact_value = safe_float(row.get("impact"), 0.0)
        if impact_value <= 0.05:
            inferred_impact = infer_cs_impact(
                rating=row.get("rating"),
                kpr=row.get("kpr"),
                dpr=row.get("dpr"),
                kast=row.get("kast"),
                adr=row.get("adr"),
            )
            if inferred_impact is not None:
                impact_value = inferred_impact
        rating_text = f"{rating_value:.2f}" if rating_value > 0 else "-"
        impact_text = f"{impact_value:.2f}" if impact_value > 0 else "-"

        players.append(
            {
                "playerId": row.get("player_id") or "",
                "name": row.get("player_name") or row.get("player_id") or "-",
                "team": team_name,
                "role": (str(row.get("position") or "").strip() or "-"),
                "rating": rating_text,
                "impact": impact_text,
                "rankScore": round(score_value, 4),
                "mapsPlayed": maps_played,
                "teamRank": team_rank if team_rank < 999 else "-",
                "sampleWeight": round(s_weight, 3),
                "teamWeight": round(t_weight, 3),
                "highlight": (
                    f"Score {score_value:.3f} 路 Maps {maps_played} 路 "
                    f"Team Rank #{team_rank if team_rank < 999 else '-'}"
                ),
                "avatar": str(row.get("avatar") or "").strip(),
            }
        )
    return players


def classify_tournament(name: str) -> Tuple[str, str]:
    n = (name or "").lower()
    tier = "A"
    if any(token in n for token in ["blast", "iem", "pgl", "major"]):
        tier = "S"

    region = "Global"
    if any(token in n for token in ["europe", "eu"]):
        region = "EU"
    elif any(token in n for token in ["china", "cn"]):
        region = "CN"
    elif any(token in n for token in ["na", "america", "americas"]):
        region = "NA"
    return tier, region


def build_tournaments(cur: pymysql.cursors.DictCursor) -> List[Dict[str, Any]]:
    def row_to_tournament(row: Dict[str, Any], now: datetime) -> Dict[str, Any]:
        name = row.get("event_name") or row.get("event_id") or "-"
        tier, region = classify_tournament(name)
        start_time = row.get("start_time")
        end_time = row.get("end_time")
        if not isinstance(start_time, datetime):
            start_time = parse_datetime_text(start_time)
        if not isinstance(end_time, datetime):
            end_time = parse_datetime_text(end_time)
        live_count = safe_int(row.get("live_count"))
        is_live = live_count > 0 or (
            isinstance(start_time, datetime)
            and isinstance(end_time, datetime)
            and start_time <= now <= end_time
        )
        if is_live:
            status = "进行中"
        elif isinstance(start_time, datetime) and start_time > now:
            status = "即将开始"
        elif isinstance(end_time, datetime) and end_time <= now:
            status = "已结束"
        else:
            status = "未知"

        return {
            "name": name,
            "tier": tier,
            "region": region,
            "start": safe_date(start_time),
            "end": safe_date(end_time),
            "status": status,
            "isLive": is_live,
            "prize": "-",
        }

    cache: Set[str] = set()
    limit_sql = "LIMIT %s" if TOURNAMENT_FETCH_LIMIT > 0 else ""
    limit_args: Tuple[Any, ...] = (TOURNAMENT_FETCH_LIMIT,) if TOURNAMENT_FETCH_LIMIT > 0 else ()
    if not table_exists(cur, "event_basic", cache):
        cur.execute(
            f"""
            WITH merged AS (
                SELECT event_id, match_time, status FROM match_schedule
                UNION ALL
                SELECT event_id, match_time, status FROM match_result
            )
            SELECT
                event_id,
                event_id AS event_name,
                MIN(match_time) AS start_time,
                MAX(match_time) AS end_time,
                SUM(CASE WHEN status = 1 THEN 1 ELSE 0 END) AS live_count,
                COUNT(*) AS total_matches
            FROM merged
            WHERE event_id IS NOT NULL AND event_id <> ''
            GROUP BY event_id
            ORDER BY live_count DESC, start_time DESC
            {limit_sql}
            """,
            limit_args,
        )
        rows = list(cur.fetchall())
        now = datetime.now()
        return [row_to_tournament(row, now) for row in rows]

    event_cols = table_columns(cur, "event_basic")
    has_event_start = "start_time" in event_cols
    has_event_end = "end_time" in event_cols
    event_start_expr = "eb.start_time" if has_event_start else "NULL"
    event_end_expr = "eb.end_time" if has_event_end else "NULL"

    cur.execute(
        f"""
        WITH merged AS (
            SELECT event_id, match_time, status FROM match_schedule
            UNION ALL
            SELECT event_id, match_time, status FROM match_result
        ),
        match_stats AS (
            SELECT
                event_id,
                MIN(match_time) AS match_start_time,
                MAX(match_time) AS match_end_time,
                SUM(CASE WHEN status = 1 THEN 1 ELSE 0 END) AS live_count,
                COUNT(*) AS total_matches
            FROM merged
            WHERE event_id IS NOT NULL AND event_id <> ''
            GROUP BY event_id
        )
        SELECT
            COALESCE(eb.event_id, ms.event_id) AS event_id,
            COALESCE(NULLIF(eb.event_name, ''), COALESCE(eb.event_id, ms.event_id), '-') AS event_name,
            COALESCE({event_start_expr}, ms.match_start_time) AS start_time,
            COALESCE({event_end_expr}, ms.match_end_time) AS end_time,
            COALESCE(ms.live_count, 0) AS live_count,
            COALESCE(ms.total_matches, 0) AS total_matches
        FROM event_basic eb
        LEFT JOIN match_stats ms ON ms.event_id = eb.event_id

        UNION ALL

        SELECT
            ms.event_id AS event_id,
            ms.event_id AS event_name,
            ms.match_start_time AS start_time,
            ms.match_end_time AS end_time,
            ms.live_count AS live_count,
            ms.total_matches AS total_matches
        FROM match_stats ms
        LEFT JOIN event_basic eb ON eb.event_id = ms.event_id
        WHERE eb.event_id IS NULL

        ORDER BY live_count DESC, start_time DESC
        {limit_sql}
        """,
        limit_args,
    )
    rows = list(cur.fetchall())
    now = datetime.now()
    return [row_to_tournament(row, now) for row in rows]

def build_match_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    def derive_from_bout_details(note_text: str) -> Tuple[Optional[int], Optional[int]]:
        text = str(note_text or "").strip()
        if not text:
            return None, None
        t1_wins = 0
        t2_wins = 0
        for part in [x.strip() for x in text.split("|") if str(x or "").strip()]:
            lower = part.lower()
            if "winner:t1" in lower or "winner:team1" in lower:
                t1_wins += 1
                continue
            if "winner:t2" in lower or "winner:team2" in lower:
                t2_wins += 1
                continue
            m = re.search(r":\s*(\d+)\s*-\s*(\d+)", part)
            if not m:
                continue
            s1 = safe_int(m.group(1))
            s2 = safe_int(m.group(2))
            if s1 > s2:
                t1_wins += 1
            elif s2 > s1:
                t2_wins += 1
        if t1_wins == 0 and t2_wins == 0:
            return None, None
        return t1_wins, t2_wins

    s1 = row.get("score1")
    s2 = row.get("score2")
    score_known = s1 is not None and s2 is not None
    bout_details_text = str(row.get("bout_details") or "").strip()
    if score_known and safe_int(s1) == 0 and safe_int(s2) == 0 and bout_details_text:
        d1, d2 = derive_from_bout_details(bout_details_text)
        if d1 is not None and d2 is not None:
            s1, s2 = d1, d2
    elif not score_known and bout_details_text:
        d1, d2 = derive_from_bout_details(bout_details_text)
        if d1 is not None and d2 is not None:
            s1, s2 = d1, d2
            score_known = True

    if score_known:
        score_text = f"{safe_int(s1)}-{safe_int(s2)}"
        if safe_int(s1) > safe_int(s2):
            winner = row.get("team1") or "-"
        elif safe_int(s2) > safe_int(s1):
            winner = row.get("team2") or "-"
        else:
            winner = "-"
    else:
        score_text = "-"
        winner = "-"

    note = (row.get("bout_details") or "").strip()
    if len(note) > 100:
        note = note[:97] + "..."

    return {
        "matchId": str(row.get("match_id") or "").strip(),
        "date": safe_date(row.get("match_time")),
        "matchTime": safe_datetime(row.get("match_time")),
        "tournament": row.get("event_name") or "-",
        "tier": str(row.get("tier") or "").strip() or "-",
        "stage": f"BO{safe_int(row.get('bo'), 0)}" if row.get("bo") is not None else "-",
        "teamA": row.get("team1_name") or row.get("team1") or "-",
        "teamB": row.get("team2_name") or row.get("team2") or "-",
        "teamALogo": str(row.get("team1_logo") or "").strip(),
        "teamBLogo": str(row.get("team2_logo") or "").strip(),
        "score": score_text,
        "winner": winner,
        "statusCode": safe_int(row.get("status"), -1),
        "note": note or "-",
    }


def build_matches(cur: pymysql.cursors.DictCursor) -> List[Dict[str, Any]]:
    cur.execute(
        f"""
        WITH merged AS (
            SELECT
                mr.match_id,
                mr.event_id,
                mr.match_time,
                mr.bo,
                mr.team1_id,
                mr.team1,
                mr.team2_id,
                mr.team2,
                mr.score1,
                mr.score2,
                mr.status,
                mr.bout_details,
                2 AS source_order
            FROM match_result mr
            UNION ALL
            SELECT
                ms.match_id,
                ms.event_id,
                ms.match_time,
                ms.bo,
                ms.team1_id,
                ms.team1,
                ms.team2_id,
                ms.team2,
                ms.score1,
                ms.score2,
                ms.status,
                NULL AS bout_details,
                1 AS source_order
            FROM match_schedule ms
        ),
        dedup AS (
            SELECT
                merged.*,
                ROW_NUMBER() OVER (PARTITION BY merged.match_id ORDER BY merged.source_order DESC, merged.match_time DESC) AS rn
            FROM merged
        ),
        classified AS (
            SELECT
                dedup.*,
                CASE
                    WHEN dedup.status = 2 THEN 1
                    WHEN dedup.status = 1
                         AND dedup.match_time IS NOT NULL
                         AND dedup.match_time <= DATE_SUB(NOW(), INTERVAL {SCHEDULE_STALE_LIVE_HOURS} HOUR) THEN 1
                    WHEN dedup.status = 1 THEN 0
                    WHEN dedup.status = 0
                         AND dedup.score1 IS NOT NULL
                         AND dedup.score2 IS NOT NULL
                         AND (dedup.score1 <> 0 OR dedup.score2 <> 0) THEN 1
                    WHEN dedup.status = 0
                         AND NULLIF(TRIM(COALESCE(dedup.bout_details, '')), '') IS NOT NULL THEN 1
                    WHEN dedup.status = 0
                         AND dedup.match_time IS NOT NULL
                         AND dedup.match_time <= DATE_SUB(NOW(), INTERVAL 12 HOUR) THEN 1
                    WHEN dedup.status = 0 THEN 0
                    WHEN dedup.score1 IS NOT NULL AND dedup.score2 IS NOT NULL AND (dedup.score1 <> 0 OR dedup.score2 <> 0) THEN 1
                    WHEN NULLIF(TRIM(COALESCE(dedup.bout_details, '')), '') IS NOT NULL THEN 1
                    ELSE 0
                END AS is_finished
            FROM dedup
            WHERE dedup.rn = 1
        ),
        result_rows AS (
            SELECT *
            FROM classified
            WHERE is_finished = 1
            ORDER BY match_time DESC
            LIMIT %s
        ),
        fixture_rows AS (
            SELECT *
            FROM classified
            WHERE is_finished = 0
            ORDER BY match_time ASC
            LIMIT %s
        ),
        picked AS (
            SELECT * FROM result_rows
            UNION ALL
            SELECT * FROM fixture_rows
        ),
        team_name_ranked AS (
            SELECT
                team_name,
                team_logo,
                ROW_NUMBER() OVER (
                    PARTITION BY LOWER(TRIM(team_name))
                    ORDER BY
                        CASE
                            WHEN team_id IS NULL OR TRIM(team_id) = '' OR LOWER(TRIM(team_id)) = 'none' THEN 1
                            ELSE 0
                        END ASC,
                        crawl_time DESC,
                        team_id ASC
                ) AS rn
            FROM team_basic
            WHERE team_name IS NOT NULL
              AND TRIM(team_name) <> ''
        ),
        team_name_best AS (
            SELECT team_name, team_logo
            FROM team_name_ranked
            WHERE rn = 1
        )
        SELECT
            picked.match_id,
            picked.match_time,
            picked.bo,
            picked.team1_id,
            COALESCE(
                NULLIF(TRIM(picked.team1), ''),
                NULLIF(TRIM(tb1_id.team_name), ''),
                NULLIF(TRIM(tb1_name.team_name), ''),
                '-'
            ) AS team1_name,
            picked.team1,
            picked.team2_id,
            COALESCE(
                NULLIF(TRIM(picked.team2), ''),
                NULLIF(TRIM(tb2_id.team_name), ''),
                NULLIF(TRIM(tb2_name.team_name), ''),
                '-'
            ) AS team2_name,
            picked.team2,
            picked.score1,
            picked.score2,
            picked.status,
            picked.bout_details,
            COALESCE(eb.event_name, picked.event_id, '-') AS event_name,
            COALESCE(tb1_id.team_logo, tb1_name.team_logo, '') AS team1_logo,
            COALESCE(tb2_id.team_logo, tb2_name.team_logo, '') AS team2_logo
        FROM picked
        LEFT JOIN event_basic eb ON eb.event_id = picked.event_id
        LEFT JOIN team_basic tb1_id ON tb1_id.team_id = picked.team1_id
        LEFT JOIN team_basic tb2_id ON tb2_id.team_id = picked.team2_id
        LEFT JOIN team_name_best tb1_name ON LOWER(TRIM(tb1_name.team_name)) = LOWER(TRIM(picked.team1))
        LEFT JOIN team_name_best tb2_name ON LOWER(TRIM(tb2_name.team_name)) = LOWER(TRIM(picked.team2))
        ORDER BY picked.match_time DESC
        """,
        (MATCH_RESULT_FETCH_LIMIT, MATCH_FIXTURE_FETCH_LIMIT),
    )
    rows = list(cur.fetchall())
    return [build_match_payload(row) for row in rows]


def normalize_schedule_view(value: str) -> str:
    view = str(value or "").strip().lower()
    if view in {"fixture", "result", "all"}:
        return view
    return "fixture"


def normalize_schedule_tier(value: str) -> str:
    tier = str(value or "").strip().lower()
    if tier in {"b_or_above", "a_or_above", "s_or_above", "all"}:
        return tier
    return "b_or_above"


def tier_filter_min_rank(value: str) -> int:
    tier = normalize_schedule_tier(value)
    if tier == "s_or_above":
        return 4
    if tier == "a_or_above":
        return 3
    if tier == "b_or_above":
        return 2
    return 0


def normalize_schedule_date(value: str) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None
    return dt.strftime("%Y-%m-%d")


def build_matches_filtered(
    cur: pymysql.cursors.DictCursor,
    *,
    view: str = "fixture",
    date_filter: Optional[str] = None,
    tier_filter: str = "b_or_above",
    limit: int = SCHEDULE_API_MATCH_LIMIT,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    normalized_view = normalize_schedule_view(view)
    normalized_date = normalize_schedule_date(date_filter or "")
    min_tier_rank = tier_filter_min_rank(tier_filter)
    safe_limit = max(1, min(int(limit), SCHEDULE_API_MATCH_LIMIT))
    safe_offset = max(0, int(offset))

    where_clauses: List[str] = []
    params: List[Any] = []

    if normalized_view == "result":
        where_clauses.append("q.is_finished = 1")
    elif normalized_view == "fixture":
        where_clauses.append("q.is_finished = 0")

    if normalized_date:
        where_clauses.append("DATE(q.match_time) = %s")
        params.append(normalized_date)

    if min_tier_rank > 0:
        where_clauses.append("q.tier_rank >= %s")
        params.append(min_tier_rank)

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    order_sql = "q.match_time ASC"
    if normalized_view == "result":
        order_sql = "q.match_time DESC"
    elif normalized_view == "all":
        order_sql = "q.match_time DESC"

    params.append(safe_limit)
    params.append(safe_offset)

    cur.execute(
        f"""
        WITH merged AS (
            SELECT
                mr.match_id,
                mr.event_id,
                mr.match_time,
                mr.bo,
                mr.team1_id,
                mr.team1,
                mr.team2_id,
                mr.team2,
                mr.score1,
                mr.score2,
                mr.status,
                mr.bout_details,
                2 AS source_order
            FROM match_result mr
            UNION ALL
            SELECT
                ms.match_id,
                ms.event_id,
                ms.match_time,
                ms.bo,
                ms.team1_id,
                ms.team1,
                ms.team2_id,
                ms.team2,
                ms.score1,
                ms.score2,
                ms.status,
                NULL AS bout_details,
                1 AS source_order
            FROM match_schedule ms
        ),
        dedup AS (
            SELECT
                merged.*,
                ROW_NUMBER() OVER (
                    PARTITION BY merged.match_id
                    ORDER BY merged.source_order DESC, merged.match_time DESC
                ) AS rn
            FROM merged
        ),
        classified AS (
            SELECT
                dedup.*,
                CASE
                    WHEN dedup.status = 2 THEN 1
                    WHEN dedup.status = 1
                         AND dedup.match_time IS NOT NULL
                         AND dedup.match_time <= DATE_SUB(NOW(), INTERVAL {SCHEDULE_STALE_LIVE_HOURS} HOUR) THEN 1
                    WHEN dedup.status = 1 THEN 0
                    WHEN dedup.status = 0
                         AND dedup.score1 IS NOT NULL
                         AND dedup.score2 IS NOT NULL
                         AND (dedup.score1 <> 0 OR dedup.score2 <> 0) THEN 1
                    WHEN dedup.status = 0
                         AND NULLIF(TRIM(COALESCE(dedup.bout_details, '')), '') IS NOT NULL THEN 1
                    WHEN dedup.status = 0
                         AND dedup.match_time IS NOT NULL
                         AND dedup.match_time <= DATE_SUB(NOW(), INTERVAL 12 HOUR) THEN 1
                    WHEN dedup.status = 0 THEN 0
                    WHEN dedup.score1 IS NOT NULL
                         AND dedup.score2 IS NOT NULL
                         AND (dedup.score1 <> 0 OR dedup.score2 <> 0) THEN 1
                    WHEN NULLIF(TRIM(COALESCE(dedup.bout_details, '')), '') IS NOT NULL THEN 1
                    ELSE 0
                END AS is_finished
            FROM dedup
            WHERE dedup.rn = 1
        ),
        team_name_ranked AS (
            SELECT
                team_name,
                team_logo,
                ROW_NUMBER() OVER (
                    PARTITION BY LOWER(TRIM(team_name))
                    ORDER BY
                        CASE
                            WHEN team_id IS NULL
                                 OR TRIM(team_id) = ''
                                 OR LOWER(TRIM(team_id)) = 'none' THEN 1
                            ELSE 0
                        END ASC,
                        crawl_time DESC,
                        team_id ASC
                ) AS rn
            FROM team_basic
            WHERE team_name IS NOT NULL
              AND TRIM(team_name) <> ''
        ),
        team_name_best AS (
            SELECT team_name, team_logo
            FROM team_name_ranked
            WHERE rn = 1
        )
        SELECT *
        FROM (
            SELECT
                classified.match_id,
                classified.match_time,
                classified.bo,
                classified.team1_id,
                COALESCE(
                    NULLIF(TRIM(classified.team1), ''),
                    NULLIF(TRIM(tb1_id.team_name), ''),
                    NULLIF(TRIM(tb1_name.team_name), ''),
                    '-'
                ) AS team1_name,
                classified.team1,
                classified.team2_id,
                COALESCE(
                    NULLIF(TRIM(classified.team2), ''),
                    NULLIF(TRIM(tb2_id.team_name), ''),
                    NULLIF(TRIM(tb2_name.team_name), ''),
                    '-'
                ) AS team2_name,
                classified.team2,
                classified.score1,
                classified.score2,
                classified.status,
                classified.bout_details,
                classified.is_finished,
                COALESCE(eb.event_name, classified.event_id, '-') AS event_name,
                COALESCE(tb1_id.team_logo, tb1_name.team_logo, '') AS team1_logo,
                COALESCE(tb2_id.team_logo, tb2_name.team_logo, '') AS team2_logo,
                CASE
                    WHEN LOWER(COALESCE(eb.event_name, classified.event_id, '')) LIKE '%%major%%'
                      OR LOWER(COALESCE(eb.event_name, classified.event_id, '')) LIKE '%%blast%%'
                      OR LOWER(COALESCE(eb.event_name, classified.event_id, '')) LIKE '%%iem%%'
                      OR LOWER(COALESCE(eb.event_name, classified.event_id, '')) LIKE '%%pgl%%'
                      OR LOWER(COALESCE(eb.event_name, classified.event_id, '')) LIKE '%%s+%%'
                      OR LOWER(COALESCE(eb.event_name, classified.event_id, '')) LIKE '%%s级%%'
                    THEN 4
                    WHEN LOWER(COALESCE(eb.event_name, classified.event_id, '')) LIKE '%%b级%%'
                    THEN 2
                    WHEN LOWER(COALESCE(eb.event_name, classified.event_id, '')) LIKE '%%a级%%'
                    THEN 3
                    ELSE 3
                END AS tier_rank,
                CASE
                    WHEN LOWER(COALESCE(eb.event_name, classified.event_id, '')) LIKE '%%major%%'
                      OR LOWER(COALESCE(eb.event_name, classified.event_id, '')) LIKE '%%blast%%'
                      OR LOWER(COALESCE(eb.event_name, classified.event_id, '')) LIKE '%%iem%%'
                      OR LOWER(COALESCE(eb.event_name, classified.event_id, '')) LIKE '%%pgl%%'
                      OR LOWER(COALESCE(eb.event_name, classified.event_id, '')) LIKE '%%s+%%'
                      OR LOWER(COALESCE(eb.event_name, classified.event_id, '')) LIKE '%%s级%%'
                    THEN 'S'
                    WHEN LOWER(COALESCE(eb.event_name, classified.event_id, '')) LIKE '%%b级%%'
                    THEN 'B'
                    WHEN LOWER(COALESCE(eb.event_name, classified.event_id, '')) LIKE '%%a级%%'
                    THEN 'A'
                    ELSE 'A'
                END AS tier
            FROM classified
            LEFT JOIN event_basic eb ON eb.event_id = classified.event_id
            LEFT JOIN team_basic tb1_id ON tb1_id.team_id = classified.team1_id
            LEFT JOIN team_basic tb2_id ON tb2_id.team_id = classified.team2_id
            LEFT JOIN team_name_best tb1_name ON LOWER(TRIM(tb1_name.team_name)) = LOWER(TRIM(classified.team1))
            LEFT JOIN team_name_best tb2_name ON LOWER(TRIM(tb2_name.team_name)) = LOWER(TRIM(classified.team2))
        ) AS q
        {where_sql}
        ORDER BY {order_sql}
        LIMIT %s
        OFFSET %s
        """,
        tuple(params),
    )
    rows = list(cur.fetchall())
    return [build_match_payload(row) for row in rows]


def parse_bout_details_text(note: str) -> List[Dict[str, Any]]:
    text = str(note or "").strip()
    if not text:
        return []

    maps: List[Dict[str, Any]] = []
    chunks = [x.strip() for x in text.split("|") if str(x or "").strip()]
    pattern = re.compile(
        r"^(?P<map>[^:]+):\s*(?P<t1>\d+)-(?P<t2>\d+)\s*\(winner:(?P<winner>[^)]+)\)\s*$",
        re.IGNORECASE,
    )
    for idx, chunk in enumerate(chunks, start=1):
        m = pattern.match(chunk)
        if not m:
            maps.append(
                {
                    "index": idx,
                    "map": chunk,
                    "team1Score": None,
                    "team2Score": None,
                    "winner": "-",
                }
            )
            continue
        winner_raw = str(m.group("winner") or "").strip().lower()
        winner = "-"
        if winner_raw in {"t1", "team1", "1"}:
            winner = "team1"
        elif winner_raw in {"t2", "team2", "2"}:
            winner = "team2"
        elif winner_raw in {"draw", "tie"}:
            winner = "draw"
        maps.append(
            {
                "index": idx,
                "map": str(m.group("map") or "").strip(),
                "team1Score": safe_int(m.group("t1"), 0),
                "team2Score": safe_int(m.group("t2"), 0),
                "winner": winner,
            }
        )
    return maps


def calc_match_is_finished(
    status_code: Any, match_time: Any, score1: Any, score2: Any
) -> bool:
    code = safe_int(status_code, -1)
    if code == 2:
        return True
    if code == 1:
        return False
    if code == 0:
        if score1 is not None and score2 is not None and (safe_int(score1) != 0 or safe_int(score2) != 0):
            return True
        if isinstance(match_time, datetime) and match_time <= datetime.now() - timedelta(hours=2):
            return True
        return False
    if score1 is not None and score2 is not None and (safe_int(score1) != 0 or safe_int(score2) != 0):
        return True
    if isinstance(match_time, datetime) and match_time <= datetime.now() - timedelta(hours=2):
        return True
    return False


def calc_match_status_text(
    status_code: Any, match_time: Any, score1: Any, score2: Any
) -> str:
    if calc_match_is_finished(status_code, match_time, score1, score2):
        return "已完赛"
    if safe_int(status_code, -1) == 1:
        return "进行中"
    return "未开赛"


def normalize_winner_side(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"t1", "team1", "a", "1"}:
        return "team1"
    if text in {"t2", "team2", "b", "2"}:
        return "team2"
    if text in {"draw", "tie"}:
        return "draw"
    return "-"


def normalize_team_side(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"t1", "team1", "a", "1"}:
        return "teamA"
    if text in {"t2", "team2", "b", "2"}:
        return "teamB"
    return ""


ACTIVE_POOL_MAP_NAMES: Dict[str, str] = {
    "dust2": "Dust2",
    "nuke": "Nuke",
    "inferno": "Inferno",
    "anubis": "Anubis",
    "overpass": "Overpass",
    "mirage": "Mirage",
    "ancient": "Ancient",
}


def normalize_map_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    compact = re.sub(r"[^a-z0-9]+", "", text)
    if not compact:
        return ""
    if "dust2" in compact or "dustii" in compact:
        return "dust2"
    for key in ("nuke", "inferno", "anubis", "overpass", "mirage", "ancient"):
        if key in compact:
            return key
    return ""


def enrich_match_player_avatars(
    cur: pymysql.cursors.DictCursor,
    table_cache: Set[str],
    player_stats: Dict[str, List[Dict[str, Any]]],
    map_player_stats: List[Dict[str, Any]],
) -> None:
    all_players: List[Dict[str, Any]] = []
    for side in ("teamA", "teamB"):
        all_players.extend(player_stats.get(side) or [])
    for block in map_player_stats:
        if not isinstance(block, dict):
            continue
        all_players.extend(block.get("teamA") or [])
        all_players.extend(block.get("teamB") or [])

    if not all_players:
        return

    for player in all_players:
        if "avatar" not in player:
            player["avatar"] = ""

    player_ids = sorted(
        {
            str(player.get("playerId") or "").strip()
            for player in all_players
            if str(player.get("playerId") or "").strip()
        }
    )
    player_names = sorted(
        {
            str(player.get("name") or "").strip()
            for player in all_players
            if str(player.get("name") or "").strip() and str(player.get("name") or "").strip() != "-"
        }
    )
    if not player_ids and not player_names:
        return

    avatar_by_id: Dict[str, str] = {}
    avatar_by_name: Dict[str, str] = {}

    def merge_avatar_rows(rows: List[Dict[str, Any]]) -> None:
        for row in rows:
            avatar = str(row.get("avatar") or "").strip()
            if not avatar:
                continue
            player_id = str(row.get("player_id") or "").strip()
            player_name = str(row.get("player_name") or "").strip()
            if player_id and player_id not in avatar_by_id:
                avatar_by_id[player_id] = avatar
            if player_name and player_name not in avatar_by_name:
                avatar_by_name[player_name] = avatar

    def build_where_clause(id_col: str, name_col: str) -> Tuple[str, List[Any]]:
        where_clauses: List[str] = []
        params: List[Any] = []
        if player_ids:
            id_placeholders = ", ".join(["%s"] * len(player_ids))
            where_clauses.append(f"{id_col} IN ({id_placeholders})")
            params.extend(player_ids)
        if player_names:
            name_placeholders = ", ".join(["%s"] * len(player_names))
            where_clauses.append(f"{name_col} IN ({name_placeholders})")
            params.extend(player_names)
        return " OR ".join(where_clauses), params

    if table_exists(cur, "player_basic", table_cache):
        pb_cols = table_columns(cur, "player_basic")
        pb_id_col = "player_id" if "player_id" in pb_cols else ("id" if "id" in pb_cols else "")
        pb_name_col = "name" if "name" in pb_cols else ("player_name" if "player_name" in pb_cols else "")
        if pb_id_col and pb_name_col:
            where_sql, params = build_where_clause(pb_id_col, pb_name_col)
        else:
            where_sql, params = "", []
        if where_sql:
            cur.execute(
                f"""
                SELECT
                    {pb_id_col} AS player_id,
                    {pb_name_col} AS player_name,
                    MAX(COALESCE(NULLIF(portrait, ''), NULLIF(half_portrait, ''))) AS avatar
                FROM player_basic
                WHERE {where_sql}
                GROUP BY {pb_id_col}, {pb_name_col}
                """,
                tuple(params),
            )
            merge_avatar_rows(list(cur.fetchall() or []))

    if table_exists(cur, "team_player_relation", table_cache):
        where_sql, params = build_where_clause("player_id", "player_name")
        if where_sql:
            cur.execute(
                f"""
                SELECT
                    player_id,
                    player_name,
                    MAX(NULLIF(player_portrait, '')) AS avatar
                FROM team_player_relation
                WHERE {where_sql}
                GROUP BY player_id, player_name
                """,
                tuple(params),
            )
            merge_avatar_rows(list(cur.fetchall() or []))

    for player in all_players:
        if str(player.get("avatar") or "").strip():
            continue
        player_id = str(player.get("playerId") or "").strip()
        player_name = str(player.get("name") or "").strip()
        avatar = avatar_by_id.get(player_id) or avatar_by_name.get(player_name) or ""
        if avatar:
            player["avatar"] = avatar


def build_match_detail(cur: pymysql.cursors.DictCursor, match_id: str) -> Dict[str, Any]:
    table_cache: Set[str] = set()
    has_detail_table = table_exists(cur, "match_result_detail", table_cache)
    has_player_stats_table = table_exists(cur, "match_result_player_stats", table_cache)
    has_map_stats_table = table_exists(cur, "match_result_map_stats", table_cache)
    has_map_player_stats_table = table_exists(cur, "match_result_map_player_stats", table_cache)

    merged_sources: List[str] = []
    params: List[Any] = []

    if has_detail_table:
        merged_sources.append(
            """
            SELECT
                mrd.match_id,
                mrd.event_id,
                mrd.match_time,
                mrd.bo,
                mrd.team1_id,
                mrd.team1,
                mrd.team2_id,
                mrd.team2,
                mrd.score1,
                mrd.score2,
                mrd.status,
                mrd.bout_details,
                mrd.event_name,
                mrd.event_logo,
                mrd.event_log_count,
                mrd.event_log_map_count,
                mrd.team1_form_rating,
                mrd.team2_form_rating,
                mrd.team1_form_win_rate,
                mrd.team2_form_win_rate,
                mrd.analysis_success,
                mrd.data_success,
                mrd.event_log_success,
                3 AS source_order
            FROM match_result_detail mrd
            WHERE mrd.match_id = %s
            """
        )
        params.append(match_id)

    merged_sources.append(
        """
        SELECT
            mr.match_id,
            mr.event_id,
            mr.match_time,
            mr.bo,
            mr.team1_id,
            mr.team1,
            mr.team2_id,
            mr.team2,
            mr.score1,
            mr.score2,
            mr.status,
            mr.bout_details,
            mr.event_name,
            mr.event_logo,
            NULL AS event_log_count,
            NULL AS event_log_map_count,
            NULL AS team1_form_rating,
            NULL AS team2_form_rating,
            NULL AS team1_form_win_rate,
            NULL AS team2_form_win_rate,
            NULL AS analysis_success,
            NULL AS data_success,
            NULL AS event_log_success,
            2 AS source_order
        FROM match_result mr
        WHERE mr.match_id = %s
        """
    )
    params.append(match_id)

    merged_sources.append(
        """
        SELECT
            ms.match_id,
            ms.event_id,
            ms.match_time,
            ms.bo,
            ms.team1_id,
            ms.team1,
            ms.team2_id,
            ms.team2,
            ms.score1,
            ms.score2,
            ms.status,
            NULL AS bout_details,
            ms.event_name,
            ms.event_logo,
            NULL AS event_log_count,
            NULL AS event_log_map_count,
            NULL AS team1_form_rating,
            NULL AS team2_form_rating,
            NULL AS team1_form_win_rate,
            NULL AS team2_form_win_rate,
            NULL AS analysis_success,
            NULL AS data_success,
            NULL AS event_log_success,
            1 AS source_order
        FROM match_schedule ms
        WHERE ms.match_id = %s
        """
    )
    params.append(match_id)

    merged_sql = "\nUNION ALL\n".join(merged_sources)
    detail_sql = f"""
        WITH merged AS (
            {merged_sql}
        ),
        dedup AS (
            SELECT
                merged.*,
                ROW_NUMBER() OVER (
                    PARTITION BY merged.match_id
                    ORDER BY merged.source_order DESC, merged.match_time DESC
                ) AS rn
            FROM merged
        ),
        team_name_ranked AS (
            SELECT
                team_name,
                team_logo,
                ROW_NUMBER() OVER (
                    PARTITION BY LOWER(TRIM(team_name))
                    ORDER BY
                        CASE
                            WHEN team_id IS NULL
                                 OR TRIM(team_id) = ''
                                 OR LOWER(TRIM(team_id)) = 'none' THEN 1
                            ELSE 0
                        END ASC,
                        crawl_time DESC,
                        team_id ASC
                ) AS rn
            FROM team_basic
            WHERE team_name IS NOT NULL
              AND TRIM(team_name) <> ''
        ),
        team_name_best AS (
            SELECT team_name, team_logo
            FROM team_name_ranked
            WHERE rn = 1
        )
        SELECT
            dedup.match_id,
            dedup.match_time,
            dedup.bo,
            dedup.team1_id,
            COALESCE(
                NULLIF(TRIM(dedup.team1), ''),
                NULLIF(TRIM(tb1_id.team_name), ''),
                NULLIF(TRIM(tb1_name.team_name), ''),
                '-'
            ) AS team1_name,
            dedup.team1,
            dedup.team2_id,
            COALESCE(
                NULLIF(TRIM(dedup.team2), ''),
                NULLIF(TRIM(tb2_id.team_name), ''),
                NULLIF(TRIM(tb2_name.team_name), ''),
                '-'
            ) AS team2_name,
            dedup.team2,
            dedup.score1,
            dedup.score2,
            dedup.status,
            dedup.bout_details,
            COALESCE(
                NULLIF(TRIM(eb.event_name), ''),
                NULLIF(TRIM(dedup.event_name), ''),
                dedup.event_id,
                '-'
            ) AS event_name,
            COALESCE(
                NULLIF(TRIM(eb.event_logo), ''),
                NULLIF(TRIM(dedup.event_logo), ''),
                ''
            ) AS event_logo,
            COALESCE(tb1_id.team_logo, tb1_name.team_logo, '') AS team1_logo,
            COALESCE(tb2_id.team_logo, tb2_name.team_logo, '') AS team2_logo,
            dedup.event_log_count,
            dedup.event_log_map_count,
            dedup.team1_form_rating,
            dedup.team2_form_rating,
            dedup.team1_form_win_rate,
            dedup.team2_form_win_rate,
            dedup.analysis_success,
            dedup.data_success,
            dedup.event_log_success
        FROM dedup
        LEFT JOIN event_basic eb ON eb.event_id = dedup.event_id
        LEFT JOIN team_basic tb1_id ON tb1_id.team_id = dedup.team1_id
        LEFT JOIN team_basic tb2_id ON tb2_id.team_id = dedup.team2_id
        LEFT JOIN team_name_best tb1_name ON LOWER(TRIM(tb1_name.team_name)) = LOWER(TRIM(dedup.team1))
        LEFT JOIN team_name_best tb2_name ON LOWER(TRIM(tb2_name.team_name)) = LOWER(TRIM(dedup.team2))
        WHERE dedup.rn = 1
        LIMIT 1
    """
    cur.execute(detail_sql, tuple(params))
    row = cur.fetchone()
    if not row:
        return {"matchId": match_id, "exists": False}

    s1 = row.get("score1")
    s2 = row.get("score2")
    score_known = s1 is not None and s2 is not None
    score = f"{safe_int(s1)}-{safe_int(s2)}" if score_known else "-"

    winner = "-"
    if score_known:
        if safe_int(s1) > safe_int(s2):
            winner = row.get("team1_name") or row.get("team1") or "-"
        elif safe_int(s2) > safe_int(s1):
            winner = row.get("team2_name") or row.get("team2") or "-"

    event_name = row.get("event_name") or "-"
    tier, region = classify_tournament(event_name)
    status_text = calc_match_status_text(row.get("status"), row.get("match_time"), s1, s2)
    maps = parse_bout_details_text(str(row.get("bout_details") or ""))

    if has_map_stats_table:
        cur.execute(
            """
            SELECT
                map_index,
                map_name,
                team1_score,
                team2_score,
                winner_side,
                winner_team_id,
                winner_team_name
            FROM match_result_map_stats
            WHERE match_id = %s
            ORDER BY map_index ASC, id ASC
            """,
            (match_id,),
        )
        map_rows = list(cur.fetchall())
        if map_rows:
            rebuilt_maps: List[Dict[str, Any]] = []
            for idx, map_row in enumerate(map_rows, start=1):
                t1_score_raw = map_row.get("team1_score")
                t2_score_raw = map_row.get("team2_score")
                rebuilt_maps.append(
                    {
                        "index": safe_int(map_row.get("map_index"), idx),
                        "map": str(map_row.get("map_name") or "").strip() or "-",
                        "team1Score": safe_int(t1_score_raw) if t1_score_raw is not None else None,
                        "team2Score": safe_int(t2_score_raw) if t2_score_raw is not None else None,
                        "winner": normalize_winner_side(map_row.get("winner_side")),
                        "winnerTeamId": str(map_row.get("winner_team_id") or "").strip(),
                        "winnerTeamName": str(map_row.get("winner_team_name") or "").strip(),
                    }
                )
            maps = rebuilt_maps

    player_stats: Dict[str, List[Dict[str, Any]]] = {"teamA": [], "teamB": []}
    if has_player_stats_table:
        cur.execute(
            """
            SELECT
                team_side,
                team_id,
                team_name,
                player_id,
                player_name,
                country_name,
                country_logo,
                rating,
                adr,
                kast,
                kd,
                kpr,
                mk_rating,
                impact,
                swing,
                stat_index
            FROM match_result_player_stats
            WHERE match_id = %s
            ORDER BY
                CASE
                    WHEN LOWER(TRIM(team_side)) IN ('t1', 'team1', 'a', '1') THEN 0
                    WHEN LOWER(TRIM(team_side)) IN ('t2', 'team2', 'b', '2') THEN 1
                    ELSE 2
                END ASC,
                stat_index ASC,
                id ASC
            """,
            (match_id,),
        )
        player_rows = list(cur.fetchall())
        for player in player_rows:
            side = normalize_team_side(player.get("team_side"))
            if not side:
                team_id = str(player.get("team_id") or "").strip()
                if team_id and team_id == str(row.get("team1_id") or "").strip():
                    side = "teamA"
                elif team_id and team_id == str(row.get("team2_id") or "").strip():
                    side = "teamB"
                else:
                    side = "teamA"

            player_stats[side].append(
                {
                    "teamId": str(player.get("team_id") or "").strip(),
                    "teamName": str(player.get("team_name") or "").strip(),
                    "playerId": str(player.get("player_id") or "").strip(),
                    "name": str(player.get("player_name") or "").strip() or "-",
                    "countryName": str(player.get("country_name") or "").strip(),
                    "countryLogo": str(player.get("country_logo") or "").strip(),
                    "rating": format_metric(player.get("rating")),
                    "adr": format_metric(player.get("adr"), digits=1),
                    "kast": str(player.get("kast") or "").strip() or "-",
                    "kd": format_metric(player.get("kd")),
                    "kpr": format_metric(player.get("kpr"), digits=2),
                    "mkRating": format_metric(player.get("mk_rating")),
                    "impact": format_metric(player.get("impact")),
                    "swing": str(player.get("swing") or "").strip() or "-",
                    "order": safe_int(player.get("stat_index"), 0),
                }
            )

    map_player_stats_by_index: Dict[int, Dict[str, Any]] = {}
    if has_map_player_stats_table:
        cur.execute(
            """
            SELECT
                map_index,
                map_name,
                team_side,
                team_id,
                team_name,
                player_id,
                player_name,
                country_name,
                country_logo,
                rating,
                mk_rating,
                adr,
                kast,
                kpr,
                `kill` AS kill_count,
                `death` AS death_count,
                `assist` AS assist_count,
                kd_rate,
                kd_diff,
                stat_index,
                bout_status
            FROM match_result_map_player_stats
            WHERE match_id = %s
            ORDER BY
                map_index ASC,
                CASE
                    WHEN LOWER(TRIM(team_side)) IN ('t1', 'team1', 'a', '1') THEN 0
                    WHEN LOWER(TRIM(team_side)) IN ('t2', 'team2', 'b', '2') THEN 1
                    ELSE 2
                END ASC,
                stat_index ASC,
                id ASC
            """,
            (match_id,),
        )
        map_player_rows = list(cur.fetchall())
        for player in map_player_rows:
            map_index = safe_int(player.get("map_index"), 0)
            if map_index <= 0:
                continue

            block = map_player_stats_by_index.get(map_index)
            if not block:
                block = {
                    "mapIndex": map_index,
                    "mapName": str(player.get("map_name") or "").strip() or "-",
                    "teamA": [],
                    "teamB": [],
                }
                map_player_stats_by_index[map_index] = block
            elif (not block.get("mapName")) and str(player.get("map_name") or "").strip():
                block["mapName"] = str(player.get("map_name") or "").strip()

            side = normalize_team_side(player.get("team_side"))
            if not side:
                team_id = str(player.get("team_id") or "").strip()
                if team_id and team_id == str(row.get("team1_id") or "").strip():
                    side = "teamA"
                elif team_id and team_id == str(row.get("team2_id") or "").strip():
                    side = "teamB"
                else:
                    side = "teamA"

            block[side].append(
                {
                    "teamId": str(player.get("team_id") or "").strip(),
                    "teamName": str(player.get("team_name") or "").strip(),
                    "playerId": str(player.get("player_id") or "").strip(),
                    "name": str(player.get("player_name") or "").strip() or "-",
                    "countryName": str(player.get("country_name") or "").strip(),
                    "countryLogo": str(player.get("country_logo") or "").strip(),
                    "rating": format_metric(player.get("rating")),
                    "mkRating": format_metric(player.get("mk_rating")),
                    "adr": format_metric(player.get("adr"), digits=1),
                    "kast": str(player.get("kast") or "").strip() or "-",
                    "kpr": format_metric(player.get("kpr"), digits=2),
                    "kd": format_metric(player.get("kd_rate")),
                    "kdDiff": str(player.get("kd_diff") or "").strip() or "-",
                    "kill": safe_int(player.get("kill_count"), 0),
                    "death": safe_int(player.get("death_count"), 0),
                    "assist": safe_int(player.get("assist_count"), 0),
                    "order": safe_int(player.get("stat_index"), 0),
                    "boutStatus": safe_int(player.get("bout_status"), -1),
                }
            )

    for map_row in maps:
        map_index = safe_int(map_row.get("index"), 0)
        if map_index <= 0 or map_index in map_player_stats_by_index:
            continue
        map_player_stats_by_index[map_index] = {
            "mapIndex": map_index,
            "mapName": str(map_row.get("map") or "").strip() or "-",
            "teamA": [],
            "teamB": [],
        }

    map_player_stats = [
        map_player_stats_by_index[idx]
        for idx in sorted(map_player_stats_by_index.keys())
    ]
    enrich_match_player_avatars(cur, table_cache, player_stats, map_player_stats)
    map_indexes_with_players = {
        safe_int(block.get("mapIndex"), 0)
        for block in map_player_stats
        if len(block.get("teamA") or []) + len(block.get("teamB") or []) > 0
    }
    maps = [
        map_row
        for map_row in maps
        if map_row.get("team1Score") is not None
        or map_row.get("team2Score") is not None
        or safe_int(map_row.get("index"), 0) in map_indexes_with_players
    ]
    visible_map_indexes = {safe_int(map_row.get("index"), 0) for map_row in maps}
    map_player_stats = [
        block
        for block in map_player_stats
        if safe_int(block.get("mapIndex"), 0) in visible_map_indexes
    ]

    return {
        "matchId": str(row.get("match_id") or match_id),
        "exists": True,
        "date": safe_date(row.get("match_time")),
        "matchTime": safe_datetime(row.get("match_time")),
        "statusCode": safe_int(row.get("status"), -1),
        "statusText": status_text,
        "bo": safe_int(row.get("bo"), 0),
        "score": score,
        "winner": winner,
        "tournament": {
            "name": event_name,
            "logo": str(row.get("event_logo") or "").strip(),
            "tier": tier,
            "region": region,
        },
        "teamA": {
            "id": str(row.get("team1_id") or "").strip(),
            "name": row.get("team1_name") or row.get("team1") or "-",
            "logo": str(row.get("team1_logo") or "").strip(),
            "score": safe_int(s1) if s1 is not None else None,
        },
        "teamB": {
            "id": str(row.get("team2_id") or "").strip(),
            "name": row.get("team2_name") or row.get("team2") or "-",
            "logo": str(row.get("team2_logo") or "").strip(),
            "score": safe_int(s2) if s2 is not None else None,
        },
        "maps": maps,
        "rawNote": str(row.get("bout_details") or "").strip(),
        "playerStats": player_stats,
        "mapPlayerStats": map_player_stats,
        "detailMetrics": {
            "analysisSuccess": safe_int(row.get("analysis_success"), 0),
            "dataSuccess": safe_int(row.get("data_success"), 0),
            "eventLogSuccess": safe_int(row.get("event_log_success"), 0),
            "eventLogCount": safe_int(row.get("event_log_count"), 0),
            "eventLogMapCount": safe_int(row.get("event_log_map_count"), 0),
            "teamAFormRating": str(row.get("team1_form_rating") or "").strip(),
            "teamBFormRating": str(row.get("team2_form_rating") or "").strip(),
            "teamAFormWinRate": str(row.get("team1_form_win_rate") or "").strip(),
            "teamBFormWinRate": str(row.get("team2_form_win_rate") or "").strip(),
        },
    }


def build_live_matches(cur: pymysql.cursors.DictCursor) -> List[Dict[str, Any]]:
    cur.execute(
        """
        WITH merged AS (
            SELECT
                mr.match_id,
                mr.event_id,
                mr.match_time,
                mr.bo,
                mr.team1_id,
                mr.team1,
                mr.team2_id,
                mr.team2,
                mr.score1,
                mr.score2,
                mr.status,
                mr.bout_details,
                2 AS source_order
            FROM match_result mr
            UNION ALL
            SELECT
                ms.match_id,
                ms.event_id,
                ms.match_time,
                ms.bo,
                ms.team1_id,
                ms.team1,
                ms.team2_id,
                ms.team2,
                ms.score1,
                ms.score2,
                ms.status,
                NULL AS bout_details,
                1 AS source_order
            FROM match_schedule ms
        ),
        dedup AS (
            SELECT
                merged.*,
                ROW_NUMBER() OVER (PARTITION BY merged.match_id ORDER BY merged.source_order DESC, merged.match_time DESC) AS rn
            FROM merged
        ),
        team_name_ranked AS (
            SELECT
                team_name,
                team_logo,
                ROW_NUMBER() OVER (
                    PARTITION BY LOWER(TRIM(team_name))
                    ORDER BY
                        CASE
                            WHEN team_id IS NULL OR TRIM(team_id) = '' OR LOWER(TRIM(team_id)) = 'none' THEN 1
                            ELSE 0
                        END ASC,
                        crawl_time DESC,
                        team_id ASC
                ) AS rn
            FROM team_basic
            WHERE team_name IS NOT NULL
              AND TRIM(team_name) <> ''
        ),
        team_name_best AS (
            SELECT team_name, team_logo
            FROM team_name_ranked
            WHERE rn = 1
        )
        SELECT
            dedup.match_id,
            dedup.match_time,
            dedup.bo,
            dedup.team1_id,
            COALESCE(
                NULLIF(TRIM(dedup.team1), ''),
                NULLIF(TRIM(tb1_id.team_name), ''),
                NULLIF(TRIM(tb1_name.team_name), ''),
                '-'
            ) AS team1_name,
            dedup.team1,
            dedup.team2_id,
            COALESCE(
                NULLIF(TRIM(dedup.team2), ''),
                NULLIF(TRIM(tb2_id.team_name), ''),
                NULLIF(TRIM(tb2_name.team_name), ''),
                '-'
            ) AS team2_name,
            dedup.team2,
            dedup.score1,
            dedup.score2,
            dedup.status,
            dedup.bout_details,
            COALESCE(eb.event_name, dedup.event_id, '-') AS event_name,
            COALESCE(tb1_id.team_logo, tb1_name.team_logo, '') AS team1_logo,
            COALESCE(tb2_id.team_logo, tb2_name.team_logo, '') AS team2_logo
        FROM dedup
        LEFT JOIN event_basic eb ON eb.event_id = dedup.event_id
        LEFT JOIN team_basic tb1_id ON tb1_id.team_id = dedup.team1_id
        LEFT JOIN team_basic tb2_id ON tb2_id.team_id = dedup.team2_id
        LEFT JOIN team_name_best tb1_name ON LOWER(TRIM(tb1_name.team_name)) = LOWER(TRIM(dedup.team1))
        LEFT JOIN team_name_best tb2_name ON LOWER(TRIM(tb2_name.team_name)) = LOWER(TRIM(dedup.team2))
        WHERE dedup.rn = 1
          AND (
              dedup.status = 1
              OR (
                  dedup.match_time IS NOT NULL
                  AND dedup.match_time >= DATE_SUB(NOW(), INTERVAL %s HOUR)
                  AND dedup.match_time <= DATE_ADD(NOW(), INTERVAL %s HOUR)
              )
          )
        ORDER BY
            CASE WHEN dedup.status = 1 THEN 0 ELSE 1 END ASC,
            dedup.match_time ASC
        LIMIT %s
        """,
        (LIVE_API_LOOKBACK_HOURS, LIVE_API_LOOKAHEAD_HOURS, LIVE_API_MATCH_LIMIT),
    )
    rows = list(cur.fetchall())
    return [build_match_payload(row) for row in rows]


def build_player_detail(cur: pymysql.cursors.DictCursor, player_id: str) -> Dict[str, Any]:
    table_cache: Set[str] = set()
    detail: Dict[str, Any] = {
        "playerId": player_id,
        "basic": {},
        "summary": {},
        "mouseConfig": {},
        "monitorConfig": {},
        "equipment": [],
        "maps": [],
        "performanceMetrics": [],
        "ratingChart": [],
        "honors": [],
        "milestones": [],
        "recentMatches": [],
        "teammates": [],
    }

    if table_exists(cur, "player_basic", table_cache):
        cur.execute(
            """
            SELECT
                player_id AS playerId,
                name,
                birthday,
                country_zh AS countryZh,
                country_en AS countryEn,
                team_id AS teamId,
                team_name AS teamName,
                bonus,
                position,
                positions,
                portrait AS avatar,
                half_portrait AS halfPortrait,
                team_logo AS teamLogo,
                country_logo AS countryLogo,
                top20_num,
                maps_played,
                rounds_played,
                kills,
                deaths,
                rating,
                kd,
                adr,
                kpr,
                dpr,
                kast,
                head_shot AS headShot,
                impact
            FROM player_basic
            WHERE player_id = %s
            LIMIT 1
            """,
            (player_id,),
        )
        row = cur.fetchone()
        if row:
            detail["basic"] = json_row(row)

    if not detail["basic"] and table_exists(cur, "team_player_relation", table_cache):
        cur.execute(
            """
            SELECT
                player_id AS playerId,
                player_name AS name,
                team_id AS teamId,
                team_name AS teamName,
                player_portrait AS avatar
            FROM team_player_relation
            WHERE player_id = %s
            LIMIT 1
            """,
            (player_id,),
        )
        row = cur.fetchone()
        if row:
            detail["basic"] = json_row(row)

    if not detail["basic"] and table_exists(cur, "match_result_player_stats", table_cache):
        cur.execute(
            """
            SELECT
                player_id AS playerId,
                MAX(NULLIF(player_name, '')) AS name,
                MAX(NULLIF(team_id, '')) AS teamId,
                MAX(NULLIF(team_name, '')) AS teamName,
                MAX(NULLIF(country_logo, '')) AS countryLogo,
                AVG(NULLIF(rating, '')) AS rating,
                AVG(NULLIF(kd, '')) AS kd,
                AVG(NULLIF(adr, '')) AS adr,
                AVG(NULLIF(kpr, '')) AS kpr,
                AVG(NULLIF(kast, '')) AS kast,
                AVG(NULLIF(impact, '')) AS impact,
                COUNT(*) AS maps_played
            FROM match_result_player_stats
            WHERE player_id = %s
            GROUP BY player_id
            LIMIT 1
            """,
            (player_id,),
        )
        row = cur.fetchone()
        if row:
            item = json_row(row)
            for key in ("rating", "kd", "adr", "kpr", "kast", "impact"):
                if item.get(key) not in (None, ""):
                    item[key] = format_metric(item.get(key), 2)
            item["avatar"] = ""
            detail["basic"] = item

    if not detail["basic"] and table_exists(cur, "match_result_map_player_stats", table_cache):
        cur.execute(
            """
            SELECT
                player_id AS playerId,
                MAX(NULLIF(player_name, '')) AS name,
                MAX(NULLIF(team_id, '')) AS teamId,
                MAX(NULLIF(team_name, '')) AS teamName,
                MAX(NULLIF(country_logo, '')) AS countryLogo,
                AVG(NULLIF(rating, '')) AS rating,
                AVG(NULLIF(kd_rate, '')) AS kd,
                AVG(NULLIF(adr, '')) AS adr,
                AVG(NULLIF(kpr, '')) AS kpr,
                AVG(NULLIF(kast, '')) AS kast,
                COUNT(*) AS maps_played
            FROM match_result_map_player_stats
            WHERE player_id = %s
            GROUP BY player_id
            LIMIT 1
            """,
            (player_id,),
        )
        row = cur.fetchone()
        if row:
            item = json_row(row)
            for key in ("rating", "kd", "adr", "kpr", "kast"):
                if item.get(key) not in (None, ""):
                    item[key] = format_metric(item.get(key), 2)
            item["avatar"] = ""
            detail["basic"] = item

    if table_exists(cur, "player_stats_summary", table_cache):
        cur.execute(
            "SELECT * FROM player_stats_summary WHERE player_id = %s LIMIT 1",
            (player_id,),
        )
        row = cur.fetchone()
        if row:
            detail["summary"] = json_row(row)

    if not detail["summary"] and table_exists(cur, "match_result_map_player_stats", table_cache):
        cur.execute(
            """
            SELECT
                COUNT(*) AS maps_played,
                SUM(`kill`) AS kills,
                SUM(`death`) AS deaths,
                SUM(`assist`) AS assists,
                AVG(NULLIF(rating, '')) AS rating,
                AVG(NULLIF(adr, '')) AS adr,
                AVG(NULLIF(kd_rate, '')) AS kd
            FROM match_result_map_player_stats
            WHERE player_id = %s
            """,
            (player_id,),
        )
        row = json_row(cur.fetchone() or {})
        if safe_int(row.get("maps_played")) > 0:
            detail["summary"] = {
                "player_id": player_id,
                "maps_played": safe_int(row.get("maps_played")),
                "kills": safe_int(row.get("kills")),
                "deaths": safe_int(row.get("deaths")),
                "assists": safe_int(row.get("assists")),
                "rating": format_metric(row.get("rating")),
                "adr": format_metric(row.get("adr"), 1),
                "kd": format_metric(row.get("kd")),
                "source": "match_result_map_player_stats",
            }

    if table_exists(cur, "player_mouse_config", table_cache):
        cur.execute(
            "SELECT * FROM player_mouse_config WHERE player_id = %s LIMIT 1",
            (player_id,),
        )
        row = cur.fetchone()
        if row:
            detail["mouseConfig"] = json_row(row)

    if table_exists(cur, "player_monitor_config", table_cache):
        cur.execute(
            "SELECT * FROM player_monitor_config WHERE player_id = %s LIMIT 1",
            (player_id,),
        )
        row = cur.fetchone()
        if row:
            detail["monitorConfig"] = json_row(row)

    if table_exists(cur, "player_equipment", table_cache):
        cols = table_columns(cur, "player_equipment")
        # Compatible with old row-based schema: player_id/category/name/logo
        if {"category", "name"}.issubset(cols):
            cur.execute(
                """
                SELECT category, name, logo
                FROM player_equipment
                WHERE player_id = %s
                ORDER BY category ASC
                """,
                (player_id,),
            )
            detail["equipment"] = [json_row(row) for row in cur.fetchall()]
        else:
            # New wide schema: one player per row, columns by equipment category.
            cur.execute(
                "SELECT * FROM player_equipment WHERE player_id = %s LIMIT 1",
                (player_id,),
            )
            row = cur.fetchone()
            if row:
                wide = json_row(row)
                detail["equipmentWide"] = wide
                categories = [
                    "mouse",
                    "headset",
                    "monitor",
                    "keyboard",
                    "mousepad",
                    "processor",
                    "graphics_card",
                    "chair",
                ]
                items = []
                for cat in categories:
                    name = str(wide.get(cat) or "").strip()
                    logo = str(wide.get(f"{cat}_logo") or "").strip()
                    if name or logo:
                        items.append({"category": cat, "name": name, "logo": logo})
                detail["equipment"] = items

    if table_exists(cur, "player_maps", table_cache):
        cur.execute(
            """
            SELECT map_name, map_kd, map_rating, use_num
            FROM player_maps
            WHERE player_id = %s
            ORDER BY use_num DESC, map_name ASC
            """,
            (player_id,),
        )
        raw_maps = [json_row(row) for row in cur.fetchall()]
        kept_by_key: Dict[str, Dict[str, Any]] = {}
        for row in raw_maps:
            key = normalize_map_key(row.get("map_name"))
            if key not in ACTIVE_POOL_MAP_NAMES:
                continue
            if key in kept_by_key:
                continue
            row["map_name"] = ACTIVE_POOL_MAP_NAMES[key]
            kept_by_key[key] = row
        detail["maps"] = list(kept_by_key.values())

    if not detail["maps"] and table_exists(cur, "match_result_map_player_stats", table_cache):
        cur.execute(
            """
            SELECT
                map_name,
                AVG(NULLIF(kd_rate, '')) AS map_kd,
                AVG(NULLIF(rating, '')) AS map_rating,
                COUNT(*) AS use_num
            FROM match_result_map_player_stats
            WHERE player_id = %s
              AND map_name IS NOT NULL
              AND map_name <> ''
            GROUP BY map_name
            ORDER BY use_num DESC, map_name ASC
            """,
            (player_id,),
        )
        fallback_maps = []
        kept_by_key: Dict[str, Dict[str, Any]] = {}
        for row in cur.fetchall():
            item = json_row(row)
            key = normalize_map_key(item.get("map_name"))
            if key not in ACTIVE_POOL_MAP_NAMES or key in kept_by_key:
                continue
            item["map_name"] = ACTIVE_POOL_MAP_NAMES[key]
            item["map_kd"] = format_metric(item.get("map_kd"))
            item["map_rating"] = format_metric(item.get("map_rating"))
            kept_by_key[key] = item
            fallback_maps.append(item)
        detail["maps"] = fallback_maps

    if table_exists(cur, "player_performance_metrics", table_cache):
        cur.execute(
            """
            SELECT metric, value, avg_value, lower_better, bad_start, bad_end,
                   middle_start, middle_end, good_start, good_end
            FROM player_performance_metrics
            WHERE player_id = %s
            ORDER BY metric ASC
            """,
            (player_id,),
        )
        detail["performanceMetrics"] = [json_row(row) for row in cur.fetchall()]

    if detail.get("basic") or detail.get("performanceMetrics"):
        detail["performanceMetrics"] = normalize_cs_performance_metrics(
            detail.get("performanceMetrics") or [],
            detail.get("basic") or {},
        )

    if table_exists(cur, "player_rating_chart", table_cache):
        cur.execute(
            """
            SELECT `date`, rate
            FROM player_rating_chart
            WHERE player_id = %s
            ORDER BY `date` ASC
            LIMIT 240
            """,
            (player_id,),
        )
        detail["ratingChart"] = [json_row(row) for row in cur.fetchall()]

    if table_exists(cur, "player_history_honor", table_cache):
        cur.execute(
            """
            SELECT tt_id, tt_name, start_time, bonus, grade, team_name, `rank` AS `rank`, rank_desc, team_ranking
            FROM player_history_honor
            WHERE player_id = %s
            ORDER BY start_time DESC
            LIMIT 80
            """,
            (player_id,),
        )
        detail["honors"] = [json_row(row) for row in cur.fetchall()]

    if table_exists(cur, "player_milestones", table_cache):
        cur.execute(
            """
            SELECT milestone_id, achieve_time, created_at, honor_text, detail, dimension, dimension_text,
                   `values`, match_id, tt_id, tt_name, team_id, team_name
            FROM player_milestones
            WHERE player_id = %s
            ORDER BY created_at DESC
            LIMIT 80
            """,
            (player_id,),
        )
        detail["milestones"] = [json_row(row) for row in cur.fetchall()]

    if table_exists(cur, "player_recent_matches", table_cache):
        cur.execute(
            """
            SELECT
                match_id, format, match_status, status, result,
                home_team_id, home_team_name,
                opponent_team_id, opponent_team_name,
                home_score, opponent_score,
                ts, tt_stage, tt_stage_desc,
                tournament_id, tournament_name,
                tournament_start_time, tournament_end_time,
                tournament_grade_label
            FROM player_recent_matches
            WHERE player_id = %s
            ORDER BY ts DESC
            LIMIT 30
            """,
            (player_id,),
        )
        matches = []
        for row in cur.fetchall():
            item = json_row(row)
            item["ts_text"] = (
                datetime.fromtimestamp(int(row["ts"])).strftime("%Y-%m-%d %H:%M")
                if row.get("ts")
                else ""
            )
            matches.append(item)
        detail["recentMatches"] = matches

    if not detail["recentMatches"] and table_exists(cur, "match_result_player_stats", table_cache):
        cur.execute(
            """
            SELECT
                mr.match_id,
                mr.match_time,
                mr.bo,
                mr.team1_id,
                mr.team1,
                mr.team2_id,
                mr.team2,
                mr.score1,
                mr.score2,
                mr.event_name,
                mrps.team_id,
                mrps.team_name,
                mrps.rating,
                mrps.adr,
                mrps.kd
            FROM match_result_player_stats mrps
            LEFT JOIN match_result mr ON mr.match_id = mrps.match_id
            WHERE mrps.player_id = %s
            ORDER BY mr.match_time DESC, mrps.match_id DESC
            LIMIT 30
            """,
            (player_id,),
        )
        matches = []
        for row in cur.fetchall():
            item = json_row(row)
            team_id = str(item.get("team_id") or "").strip()
            is_left = team_id and team_id == str(item.get("team1_id") or "").strip()
            if not is_left and team_id != str(item.get("team2_id") or "").strip():
                is_left = str(item.get("team_name") or "").strip() == str(item.get("team1") or "").strip()
            own_score = item.get("score1") if is_left else item.get("score2")
            opp_score = item.get("score2") if is_left else item.get("score1")
            result = "-"
            if own_score is not None and opp_score is not None:
                result = "胜" if safe_int(own_score) > safe_int(opp_score) else ("负" if safe_int(own_score) < safe_int(opp_score) else "平")
            matches.append(
                {
                    "match_id": item.get("match_id"),
                    "ts_text": item.get("match_time") or "",
                    "tournament_name": item.get("event_name") or "-",
                    "tt_stage_desc": f"BO{safe_int(item.get('bo'), 0)}" if item.get("bo") is not None else "-",
                    "home_team_name": item.get("team_name") or "-",
                    "opponent_team_name": item.get("team2") if is_left else item.get("team1"),
                    "home_score": own_score,
                    "opponent_score": opp_score,
                    "result": result,
                    "rating": format_metric(item.get("rating")),
                    "adr": format_metric(item.get("adr"), 1),
                    "kd": format_metric(item.get("kd")),
                }
            )
        detail["recentMatches"] = matches

    # Prefer current roster teammates (same team_id), fallback to cached teammate table.
    current_teammates: List[Dict[str, Any]] = []
    team_id = str((detail.get("basic") or {}).get("teamId") or "").strip()
    if team_id and table_exists(cur, "player_basic", table_cache):
        cur.execute(
            """
            SELECT
                player_id AS teammate_id,
                COALESCE(name, player_id) AS teammate_name,
                birthday,
                country_logo,
                portrait,
                half_portrait,
                rating
            FROM player_basic
            WHERE team_id = %s
              AND player_id IS NOT NULL
              AND player_id <> ''
              AND player_id <> %s
            ORDER BY name ASC
            """,
            (team_id, player_id),
        )
        current_teammates = [json_row(row) for row in cur.fetchall()]

    if not current_teammates and team_id and table_exists(cur, "team_player_relation", table_cache):
        cur.execute(
            """
            SELECT
                tpr.player_id AS teammate_id,
                COALESCE(tpr.player_name, tpr.player_id) AS teammate_name,
                NULL AS birthday,
                NULL AS country_logo,
                MAX(NULLIF(tpr.player_portrait, '')) AS portrait,
                NULL AS half_portrait,
                NULL AS rating
            FROM team_player_relation tpr
            WHERE tpr.team_id = %s
              AND tpr.player_id IS NOT NULL
              AND tpr.player_id <> ''
              AND tpr.player_id <> %s
            GROUP BY tpr.player_id, COALESCE(tpr.player_name, tpr.player_id)
            ORDER BY teammate_name ASC
            """,
            (team_id, player_id),
        )
        current_teammates = [json_row(row) for row in cur.fetchall()]

    if current_teammates:
        detail["teammates"] = current_teammates
    elif table_exists(cur, "player_teammates", table_cache):
        cur.execute(
            """
            SELECT teammate_id, teammate_name, birthday, country_logo, portrait, half_portrait, rating
            FROM player_teammates
            WHERE player_id = %s
            ORDER BY teammate_name ASC
            """,
            (player_id,),
        )
        detail["teammates"] = [json_row(row) for row in cur.fetchall()]

    return detail


def build_team_detail(cur: pymysql.cursors.DictCursor, team_key: str) -> Dict[str, Any]:
    table_cache: Set[str] = set()
    key = str(team_key or "").strip()
    detail: Dict[str, Any] = {
        "teamKey": key,
        "basic": {},
        "rank": {},
        "stats": {},
        "members": [],
        "recentMatches": [],
    }
    if not key:
        return detail

    if table_exists(cur, "team_basic", table_cache):
        cur.execute(
            """
            SELECT
                team_id AS teamId,
                team_name AS teamName,
                team_logo AS teamLogo,
                country_logo AS countryLogo,
                region_name AS region
            FROM team_basic
            WHERE team_id = %s OR team_name = %s
            LIMIT 1
            """,
            (key, key),
        )
        row = cur.fetchone()
        if row:
            detail["basic"] = json_row(row)

    if not detail["basic"] and table_exists(cur, "team_rank_snapshot", table_cache):
        cur.execute(
            """
            WITH ranked AS (
                SELECT
                    trs.*,
                    ROW_NUMBER() OVER (PARTITION BY trs.team_id ORDER BY trs.id DESC) AS rn
                FROM team_rank_snapshot trs
            )
            SELECT
                team_id AS teamId,
                team_name AS teamName,
                team_logo AS teamLogo,
                country_logo AS countryLogo,
                NULL AS region
            FROM ranked
            WHERE rn = 1 AND (team_id = %s OR team_name = %s)
            LIMIT 1
            """,
            (key, key),
        )
        row = cur.fetchone()
        if row:
            detail["basic"] = json_row(row)

    team_id = str((detail.get("basic") or {}).get("teamId") or "").strip()
    team_name = str((detail.get("basic") or {}).get("teamName") or key).strip()

    if table_exists(cur, "team_rank_snapshot", table_cache):
        cur.execute(
            """
            WITH ranked AS (
                SELECT
                    trs.*,
                    ROW_NUMBER() OVER (PARTITION BY trs.team_id ORDER BY trs.id DESC) AS rn
                FROM team_rank_snapshot trs
            )
            SELECT
                team_id AS teamId,
                team_name AS teamName,
                team_logo AS teamLogo,
                country_logo AS countryLogo,
                global_rank AS globalRank,
                valve_rank AS valveRank,
                valve_point AS valvePoint,
                score,
                point,
                rank_change AS rankChange,
                rank_diff AS rankDiff
            FROM ranked
            WHERE rn = 1
              AND (team_id = %s OR team_name = %s)
            LIMIT 1
            """,
            (team_id or key, team_name),
        )
        row = cur.fetchone()
        if row:
            rank_row = json_row(row)
            detail["rank"] = rank_row
            basic = detail["basic"] or {}
            basic["teamId"] = basic.get("teamId") or rank_row.get("teamId") or ""
            basic["teamName"] = basic.get("teamName") or rank_row.get("teamName") or team_name
            basic["teamLogo"] = basic.get("teamLogo") or rank_row.get("teamLogo") or ""
            basic["countryLogo"] = basic.get("countryLogo") or rank_row.get("countryLogo") or ""
            detail["basic"] = basic
            team_id = str(basic.get("teamId") or "").strip()
            team_name = str(basic.get("teamName") or team_name).strip()

    if table_exists(cur, "team_stat_snapshot", table_cache):
        cur.execute(
            """
            WITH ranked AS (
                SELECT
                    tss.*,
                    ROW_NUMBER() OVER (PARTITION BY tss.team_id ORDER BY tss.id DESC) AS rn
                FROM team_stat_snapshot tss
            )
            SELECT
                team_id AS teamId,
                team_name AS teamName,
                team_logo AS teamLogo,
                region_name AS region,
                rating,
                map_num AS mapNum,
                map_win_rate AS mapWinRate,
                win_rate AS winRate,
                kd,
                avg_kill AS avgKill,
                avg_death AS avgDeath,
                avg_assist AS avgAssist,
                first_kill_rate AS firstKillRate,
                ct_win_rate AS ctWinRate,
                t_win_rate AS tWinRate,
                global_rank AS globalRank,
                valve_rank AS valveRank,
                valve_point AS valvePoint,
                point,
                score,
                global_bonus AS globalBonus,
                rank_change AS rankChange
            FROM ranked
            WHERE rn = 1
              AND (team_id = %s OR team_name = %s)
            LIMIT 1
            """,
            (team_id or key, team_name),
        )
        row = cur.fetchone()
        if row:
            stat_row = json_row(row)
            detail["stats"] = stat_row
            basic = detail["basic"] or {}
            basic["teamId"] = basic.get("teamId") or stat_row.get("teamId") or ""
            basic["teamName"] = basic.get("teamName") or stat_row.get("teamName") or team_name
            basic["teamLogo"] = basic.get("teamLogo") or stat_row.get("teamLogo") or ""
            basic["region"] = basic.get("region") or stat_row.get("region") or "Global"
            detail["basic"] = basic
            team_id = str(basic.get("teamId") or "").strip()
            team_name = str(basic.get("teamName") or team_name).strip()

    members: List[Dict[str, Any]] = []
    seen_player_ids: Set[str] = set()
    if table_exists(cur, "player_basic", table_cache):
        if team_id:
            cur.execute(
                """
                SELECT
                    player_id AS playerId,
                    COALESCE(name, player_id) AS name,
                    COALESCE(NULLIF(portrait, ''), NULLIF(half_portrait, '')) AS avatar,
                    country_logo AS countryLogo,
                    COALESCE(NULLIF(position, ''), positions) AS position,
                    rating
                FROM player_basic
                WHERE team_id = %s
                  AND player_id IS NOT NULL
                  AND player_id <> ''
                ORDER BY rating DESC, name ASC
                LIMIT 5
                """,
                (team_id,),
            )
        else:
            cur.execute(
                """
                SELECT
                    player_id AS playerId,
                    COALESCE(name, player_id) AS name,
                    COALESCE(NULLIF(portrait, ''), NULLIF(half_portrait, '')) AS avatar,
                    country_logo AS countryLogo,
                    COALESCE(NULLIF(position, ''), positions) AS position,
                    rating
                FROM player_basic
                WHERE team_name = %s
                  AND player_id IS NOT NULL
                  AND player_id <> ''
                ORDER BY rating DESC, name ASC
                LIMIT 5
                """,
                (team_name,),
            )
        for row in cur.fetchall():
            item = json_row(row)
            pid = str(item.get("playerId") or "").strip()
            if not pid or pid in seen_player_ids:
                continue
            seen_player_ids.add(pid)
            members.append(item)

    if len(members) < 5 and table_exists(cur, "team_player_relation", table_cache):
        if team_id:
            cur.execute(
                """
                SELECT
                    tpr.player_id AS playerId,
                    COALESCE(tpr.player_name, tpr.player_id) AS name,
                    MAX(NULLIF(tpr.player_portrait, '')) AS avatar,
                    MAX(NULLIF(tpr.player_country_logo, '')) AS countryLogo,
                    NULL AS position,
                    NULL AS rating
                FROM team_player_relation tpr
                WHERE tpr.team_id = %s
                  AND tpr.player_id IS NOT NULL
                  AND tpr.player_id <> ''
                GROUP BY tpr.player_id, COALESCE(tpr.player_name, tpr.player_id)
                ORDER BY name ASC
                """,
                (team_id,),
            )
        else:
            cur.execute(
                """
                SELECT
                    tpr.player_id AS playerId,
                    COALESCE(tpr.player_name, tpr.player_id) AS name,
                    MAX(NULLIF(tpr.player_portrait, '')) AS avatar,
                    MAX(NULLIF(tpr.player_country_logo, '')) AS countryLogo,
                    NULL AS position,
                    NULL AS rating
                FROM team_player_relation tpr
                WHERE tpr.team_name = %s
                  AND tpr.player_id IS NOT NULL
                  AND tpr.player_id <> ''
                GROUP BY tpr.player_id, COALESCE(tpr.player_name, tpr.player_id)
                ORDER BY name ASC
                """,
                (team_name,),
            )
        for row in cur.fetchall():
            if len(members) >= 5:
                break
            item = json_row(row)
            pid = str(item.get("playerId") or "").strip()
            if not pid or pid in seen_player_ids:
                continue
            seen_player_ids.add(pid)
            members.append(item)

    detail["members"] = members[:5]

    merged_sources: List[str] = []
    if table_exists(cur, "match_result", table_cache):
        merged_sources.append(
            """
            SELECT
                mr.match_id,
                mr.event_id,
                mr.match_time,
                mr.bo,
                mr.team1_id,
                mr.team1,
                mr.team2_id,
                mr.team2,
                mr.score1,
                mr.score2,
                2 AS source_order
            FROM match_result mr
            """
        )
    if table_exists(cur, "match_schedule", table_cache):
        merged_sources.append(
            """
            SELECT
                ms.match_id,
                ms.event_id,
                ms.match_time,
                ms.bo,
                ms.team1_id,
                ms.team1,
                ms.team2_id,
                ms.team2,
                ms.score1,
                ms.score2,
                1 AS source_order
            FROM match_schedule ms
            """
        )

    if merged_sources:
        where_parts: List[str] = []
        params: List[Any] = []
        if team_id:
            where_parts.append("(dedup.team1_id = %s OR dedup.team2_id = %s)")
            params.extend([team_id, team_id])
        if team_name:
            where_parts.append("(dedup.team1 = %s OR dedup.team2 = %s)")
            params.extend([team_name, team_name])
        if where_parts:
            union_sql = "\nUNION ALL\n".join(merged_sources)
            sql = f"""
            WITH merged AS (
                {union_sql}
            ),
            dedup AS (
                SELECT
                    merged.*,
                    ROW_NUMBER() OVER (PARTITION BY merged.match_id ORDER BY merged.source_order DESC, merged.match_time DESC) AS rn
                FROM merged
            ),
            team_name_ranked AS (
                SELECT
                    team_name,
                    team_logo,
                    ROW_NUMBER() OVER (
                        PARTITION BY LOWER(TRIM(team_name))
                        ORDER BY
                            CASE
                                WHEN team_id IS NULL OR TRIM(team_id) = '' OR LOWER(TRIM(team_id)) = 'none' THEN 1
                                ELSE 0
                            END ASC,
                            crawl_time DESC,
                            team_id ASC
                    ) AS rn
                FROM team_basic
                WHERE team_name IS NOT NULL
                  AND TRIM(team_name) <> ''
            ),
            team_name_best AS (
                SELECT team_name, team_logo
                FROM team_name_ranked
                WHERE rn = 1
            )
            SELECT
                dedup.match_time,
                dedup.bo,
                dedup.team1_id,
                dedup.team1,
                dedup.team2_id,
                dedup.team2,
                dedup.score1,
                dedup.score2,
                COALESCE(eb.event_name, dedup.event_id, '-') AS event_name,
                COALESCE(tb1_id.team_logo, tb1_name.team_logo, '') AS team1_logo,
                COALESCE(tb2_id.team_logo, tb2_name.team_logo, '') AS team2_logo
            FROM dedup
            LEFT JOIN event_basic eb ON eb.event_id = dedup.event_id
            LEFT JOIN team_basic tb1_id ON tb1_id.team_id = dedup.team1_id
            LEFT JOIN team_basic tb2_id ON tb2_id.team_id = dedup.team2_id
            LEFT JOIN team_name_best tb1_name ON LOWER(TRIM(tb1_name.team_name)) = LOWER(TRIM(dedup.team1))
            LEFT JOIN team_name_best tb2_name ON LOWER(TRIM(tb2_name.team_name)) = LOWER(TRIM(dedup.team2))
            WHERE dedup.rn = 1
              AND ({' OR '.join(where_parts)})
            ORDER BY dedup.match_time DESC
            LIMIT 30
            """
            cur.execute(sql, tuple(params))
            matches: List[Dict[str, Any]] = []
            for row in cur.fetchall():
                row_data = json_row(row)
                team1_id = str(row_data.get("team1_id") or "").strip()
                team2_id = str(row_data.get("team2_id") or "").strip()
                team1_name = str(row_data.get("team1") or "").strip()
                team2_name = str(row_data.get("team2") or "").strip()
                is_left = False
                if team_id:
                    is_left = team1_id == team_id
                    if not (team1_id == team_id or team2_id == team_id):
                        is_left = team1_name == team_name
                else:
                    is_left = team1_name == team_name

                my_name = team1_name if is_left else team2_name
                opp_name = team2_name if is_left else team1_name
                my_logo = str(row_data.get("team1_logo") or "") if is_left else str(row_data.get("team2_logo") or "")
                opp_logo = str(row_data.get("team2_logo") or "") if is_left else str(row_data.get("team1_logo") or "")
                s1 = row.get("score1")
                s2 = row.get("score2")
                if s1 is not None and s2 is not None:
                    my_score = safe_int(s1) if is_left else safe_int(s2)
                    opp_score = safe_int(s2) if is_left else safe_int(s1)
                    score_text = f"{my_score}-{opp_score}"
                    if my_score > opp_score:
                        result = "胜"
                    elif my_score < opp_score:
                        result = "负"
                    else:
                        result = "平"
                else:
                    score_text = "-"
                    result = "-"

                matches.append(
                    {
                        "date": safe_datetime(row.get("match_time")),
                        "tournament": row_data.get("event_name") or "-",
                        "stage": f"BO{safe_int(row.get('bo'), 0)}" if row.get("bo") is not None else "-",
                        "teamName": my_name or team_name or "-",
                        "teamLogo": my_logo.strip(),
                        "opponent": opp_name or "-",
                        "opponentLogo": opp_logo.strip(),
                        "score": score_text,
                        "result": result,
                    }
                )
            detail["recentMatches"] = matches

    return detail


def build_dataset() -> Dict[str, Any]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            rank_rows = latest_rank_rows(cur, limit=TEAM_RANK_FETCH_LIMIT)
            stat_map = latest_stat_rows(cur)
            tournaments = build_tournaments(cur)
            matches = build_matches(cur)
            players = build_players(cur, rank_rows)

    leaderboard = build_leaderboard(rank_rows, stat_map)
    teams = build_teams(rank_rows)

    updated_at = safe_datetime(datetime.now())
    top_team = leaderboard[0]["name"] if leaderboard else "-"
    today = datetime.now().strftime("%Y-%m-%d")
    today_count = sum(1 for m in matches if m.get("date") == today)
    live_count = sum(1 for t in tournaments if bool(t.get("isLive")))

    turning_points = []
    for row in rank_rows[:2]:
        name = row.get("team_name") or row.get("team_id") or "-"
        stat = stat_map.get(str(row.get("team_id")), {})
        map_num = safe_int(stat.get("map_num"))
        kd = stat.get("kd")
        turning_points.append(f"{name}: map_num={map_num}, kd={kd if kd is not None else '-'}")

    if not turning_points:
        turning_points = ["No turning points generated from DB yet."]

    metrics = [
        {"label": "鏀跺綍璧涗簨", "value": str(len(tournaments)), "detail": "From esports.event_basic + match tables"},
        {"label": "鏀跺綍姣旇禌", "value": str(len(matches)), "detail": "Merged schedule and results"},
        {"label": "鎴橀槦瑙勬ā", "value": str(len(teams)), "detail": "Latest snapshot by team_id"},
        {"label": "閫夋墜鏍锋湰", "value": str(len(players)), "detail": "Grouped by player_id"},
    ]

    filters = {
        "regions": sorted({item["region"] for item in tournaments if item.get("region")}),
        "tiers": sorted({item["tier"] for item in tournaments if item.get("tier")}),
    }

    analysis_output = [
        {"key": "褰撳墠椤圭洰", "value": "CS2"},
        {"key": "最新同步", "value": updated_at},
        {"key": "璧涗簨鎬婚噺", "value": f"{len(tournaments)}"},
        {"key": "姣旇禌鎬婚噺", "value": f"{len(matches)}"},
        {"key": "鎴橀槦鎬婚噺", "value": f"{len(teams)}"},
        {"key": "閫夋墜鎬婚噺", "value": f"{len(players)}"},
    ]

    return {
        "gameId": "cs2",
        "gameName": "CS2",
        "gameSubtitle": "Counter-Strike 2",
        "color": "#1f6feb",
        "updatedAt": updated_at,
        "leaderboard": leaderboard,
        "tournaments": tournaments,
        "matches": matches,
        "teams": teams,
        "players": players,
        "analysis": {
            "summary": f"CS2 home dataset generated from MySQL. top_team={top_team}, today_matches={today_count}, live_tournaments={live_count}.",
            "turningPoints": turning_points,
            "teamInsight": "Team metadata and ranking are joined from team_rank_snapshot + team_basic.",
            "playerInsight": "Player list is grouped from team_player_relation and ordered by team rank.",
        },
        "mappingNotes": [
            {"title": "Data source", "desc": "All homepage blocks are read from esports tables."},
            {"title": "Match merge", "desc": "match_result is preferred over match_schedule for duplicate match_id."},
            {"title": "Fallback values", "desc": "Unavailable columns are filled with '-' for UI stability."},
        ],
        "metrics": metrics,
        "filters": filters,
        "analysisOutput": analysis_output,
    }




@router.get("/api/cs2/dataset")
def cs2_dataset() -> Dict[str, Any]:
    maybe_trigger_live_sync(force=False)
    return {"success": True, "data": build_dataset()}


@router.get("/api/cs2/matches")
def cs2_matches(
    view: str = Query("fixture"),
    date: str = Query(""),
    tier: str = Query("b_or_above"),
    limit: int = Query(SCHEDULE_API_MATCH_LIMIT, ge=1, le=10000),
    offset: int = Query(0, ge=0, le=1000000),
) -> Dict[str, Any]:
    maybe_trigger_live_sync(force=False)
    with get_conn() as conn:
        with conn.cursor() as cur:
            rows = build_matches_filtered(
                cur,
                view=view,
                date_filter=date,
                tier_filter=tier,
                limit=limit,
                offset=offset,
            )
    return {
        "success": True,
        "data": {
            "updatedAt": safe_datetime(datetime.now()),
            "matches": rows,
            "filters": {
                "view": normalize_schedule_view(view),
                "date": normalize_schedule_date(date or "") or "",
                "tier": normalize_schedule_tier(tier),
                "limit": max(1, min(int(limit), SCHEDULE_API_MATCH_LIMIT)),
                "offset": max(0, int(offset)),
            },
        },
    }


@router.get("/api/cs2/live")
def cs2_live_dataset() -> Dict[str, Any]:
    maybe_trigger_live_sync(force=False)
    with get_conn() as conn:
        with conn.cursor() as cur:
            matches = build_live_matches(cur)
    return {
        "success": True,
        "data": {
            "updatedAt": safe_datetime(datetime.now()),
            "matches": matches,
            "sync": {
                "lastRunAt": LIVE_SYNC_STATE.get("lastRunAt", ""),
                "lastError": LIVE_SYNC_STATE.get("lastError", ""),
            },
        },
    }


@router.get("/api/cs2/player/{player_id}")
def cs2_player_detail(player_id: str) -> Dict[str, Any]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            detail = build_player_detail(cur, player_id)
    return {"success": True, "data": detail}


@router.get("/api/cs2/team/{team_key}")
def cs2_team_detail(team_key: str) -> Dict[str, Any]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            detail = build_team_detail(cur, team_key)
    return {"success": True, "data": detail}


@router.get("/api/cs2/match/{match_id}")
def cs2_match_detail(match_id: str) -> Dict[str, Any]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            detail = build_match_detail(cur, match_id)
    return {"success": True, "data": detail}




app.include_router(router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("cs_api_server:app", host="127.0.0.1", port=8000, reload=True)
