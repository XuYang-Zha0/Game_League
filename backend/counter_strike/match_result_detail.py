from __future__ import annotations

import csv
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pymysql
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_DIR = Path(__file__).resolve().parent
INPUT_FILE = BASE_DIR / "cs_data" / "cs2_results_5eplay.csv"
OUTPUT_DETAIL_FILE = BASE_DIR / "cs_data" / "cs2_result_details_5eplay.csv"
OUTPUT_PLAYER_FILE = BASE_DIR / "cs_data" / "cs2_result_player_stats_5eplay.csv"
OUTPUT_MAP_FILE = BASE_DIR / "cs_data" / "cs2_result_map_stats_5eplay.csv"
OUTPUT_MAP_PLAYER_FILE = BASE_DIR / "cs_data" / "cs2_result_map_player_stats_5eplay.csv"

SCHEMA_VERSION = "v2_compact"

DEFAULT_MAX_MATCHES = int(os.getenv("CS_DETAIL_MAX_MATCHES", "0"))
DEFAULT_MAX_WORKERS = max(1, int(os.getenv("CS_DETAIL_MAX_WORKERS", "64")))
DEFAULT_BATCH_SIZE = max(1, int(os.getenv("CS_DETAIL_BATCH_SIZE", "1000")))
DEFAULT_BASE_SOURCE = os.getenv("CS_DETAIL_SOURCE", "mysql").strip().lower()
TARGET_MATCH_IDS = {
    part.strip()
    for part in os.getenv("CS_DETAIL_MATCH_IDS", "").split(",")
    if part.strip()
}
SLEEP_SECONDS = float(os.getenv("CS_DETAIL_SLEEP_SECONDS", "0"))
FETCH_EVENT_LOG = os.getenv("CS_DETAIL_FETCH_EVENT_LOG", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
INCREMENTAL_ENABLED = os.getenv("CS_DETAIL_INCREMENTAL", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
FORCE_REFRESH = os.getenv("CS_DETAIL_FORCE_REFRESH", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
HTTP_POOL_SIZE = max(32, int(os.getenv("CS_DETAIL_HTTP_POOL_SIZE", str(max(64, DEFAULT_MAX_WORKERS * 2)))))
DB_CONFIG = {
    "host": os.getenv("CS_DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("CS_DB_PORT", "3306")),
    "user": os.getenv("CS_DB_USER", "root"),
    "password": os.getenv("CS_DB_PASSWORD", ""),
    "database": os.getenv("CS_DB_NAME", "cs_esports"),
    "charset": "utf8mb4",
    "autocommit": True,
    "cursorclass": pymysql.cursors.DictCursor,
}

ANALYSIS_URL = "https://esports-data.5eplaycdn.com/v1/api/csgo/matches/{match_id}/analysis_v1"
DATA_URL = "https://esports-data.5eplaycdn.com/v1/api/csgo/matches/{match_id}/data"
EVENT_LOG_URL = (
    "https://esports-data.5eplaycdn.com/v1/api/csgo/match/{match_id}/event/log"
    "?update_version=0&limit=500"
)

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://event.5eplay.com",
    "Referer": "https://event.5eplay.com/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/141.0.0.0 Safari/537.36"
    ),
}

DETAIL_COLUMNS = [
    "match_id",
    "match_time",
    "bo",
    "team1_id",
    "team1",
    "team2_id",
    "team2",
    "event_id",
    "event_name",
    "event_logo",
    "event_start_time",
    "event_end_time",
    "score1",
    "score2",
    "status",
    "bout_count",
    "bout_details",
    "analysis_success",
    "analysis_state_ver",
    "data_success",
    "data_state_ver",
    "event_log_success",
    "event_log_to_ver",
    "event_log_count",
    "event_log_map_count",
    "team1_form_rating",
    "team2_form_rating",
    "team1_form_win_rate",
    "team2_form_win_rate",
    "fetch_error",
    "fetched_at",
    "schema_version",
]

PLAYER_COLUMNS = [
    "match_id",
    "team_side",
    "team_id",
    "team_name",
    "player_id",
    "player_name",
    "country_name",
    "country_logo",
    "rating",
    "adr",
    "kast",
    "kd",
    "kpr",
    "mk_rating",
    "impact",
    "swing",
    "stat_index",
    "fetched_at",
]

MAP_COLUMNS = [
    "match_id",
    "map_index",
    "map_name",
    "team1_score",
    "team2_score",
    "winner_side",
    "winner_team_id",
    "winner_team_name",
    "fetched_at",
]

MAP_PLAYER_COLUMNS = [
    "match_id",
    "map_index",
    "map_name",
    "team_side",
    "team_id",
    "team_name",
    "player_id",
    "player_name",
    "country_name",
    "country_logo",
    "rating",
    "mk_rating",
    "adr",
    "kast",
    "kpr",
    "kill",
    "death",
    "assist",
    "kd_rate",
    "kd_diff",
    "stat_index",
    "bout_status",
    "fetched_at",
]

BASE_REQUIRED_COLUMNS = [
    "match_id",
    "match_time",
    "bo",
    "team1_id",
    "team1",
    "team2_id",
    "team2",
    "event_id",
    "event_name",
    "event_logo",
    "event_start_time",
    "event_end_time",
    "score1",
    "score2",
    "status",
    "bout_count",
    "bout_details",
]

BOUT_DETAIL_PATTERN = re.compile(
    r"^\s*(?P<map_name>[^:]+)\s*:\s*(?P<score1>\d+)\s*-\s*(?P<score2>\d+)"
    r"(?:\s*\(winner:(?P<winner>t1|t2)\))?\s*$",
    re.IGNORECASE,
)


def build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1.2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(
        max_retries=retry,
        pool_connections=HTTP_POOL_SIZE,
        pool_maxsize=HTTP_POOL_SIZE,
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


SESSION = build_session()


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def to_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def normalize_source(value: str) -> str:
    text = normalize_text(value).lower()
    return text if text in {"mysql", "csv", "auto"} else "mysql"


def fetch_json(url: str) -> Dict[str, Any]:
    try:
        resp = SESSION.get(url, headers=HEADERS, timeout=(10, 30))
    except requests.RequestException:
        return {"success": False, "status_code": None, "data": None}

    if resp.status_code != 200:
        return {"success": False, "status_code": resp.status_code, "data": None}

    try:
        payload = resp.json()
    except ValueError:
        return {"success": False, "status_code": resp.status_code, "data": None}

    if not isinstance(payload, dict):
        return {"success": False, "status_code": resp.status_code, "data": None}

    return {
        "success": bool(payload.get("success")),
        "status_code": resp.status_code,
        "data": payload.get("data"),
    }


def read_csv_header(path: Path) -> List[str]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            return next(reader, [])
    except Exception:
        return []


def backup_legacy_compact_file_if_needed() -> bool:
    """Rotate old v1 raw-json output so v2 compact can rebuild safely."""
    header = {col.strip() for col in read_csv_header(OUTPUT_DETAIL_FILE)}
    if not header:
        return False

    legacy_columns = {
        "analysis_json",
        "data_json",
        "event_log_json",
        "mqtt_detail_auth_json",
        "mqtt_event_log_auth_json",
    }
    if not (header & legacy_columns):
        return False

    backup_name = (
        f"{OUTPUT_DETAIL_FILE.stem}_raw_backup_"
        f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    )
    backup_path = OUTPUT_DETAIL_FILE.with_name(backup_name)
    OUTPUT_DETAIL_FILE.rename(backup_path)
    print(f"[match_result_detail] legacy raw file moved to: {backup_path}")

    for path in (OUTPUT_PLAYER_FILE, OUTPUT_MAP_FILE, OUTPUT_MAP_PLAYER_FILE):
        if path.exists():
            path.unlink()
            print(f"[match_result_detail] removed old compact output: {path}")
    return True


def load_existing_match_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    ids: set[str] = set()
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                match_id = normalize_text(row.get("match_id"))
                if match_id:
                    ids.add(match_id)
    except Exception:
        return set()
    return ids


def write_rows(path: Path, rows: List[Dict[str, Any]], fieldnames: List[str]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = path.exists()
    mode = "a" if file_exists else "w"
    with path.open(mode, encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)


def parse_bout_details(
    match_id: str,
    bout_details: str,
    team1_id: str,
    team1_name: str,
    team2_id: str,
    team2_name: str,
    fetched_at: str,
) -> List[Dict[str, Any]]:
    if not bout_details:
        return []

    rows: List[Dict[str, Any]] = []
    parts = [part.strip() for part in str(bout_details).split("|") if part.strip()]
    for idx, part in enumerate(parts, start=1):
        match = BOUT_DETAIL_PATTERN.match(part)
        if not match:
            rows.append(
                {
                    "match_id": match_id,
                    "map_index": idx,
                    "map_name": part,
                    "team1_score": None,
                    "team2_score": None,
                    "winner_side": "",
                    "winner_team_id": "",
                    "winner_team_name": "",
                    "fetched_at": fetched_at,
                }
            )
            continue

        winner_side = normalize_text(match.group("winner")).lower()
        if winner_side == "t1":
            winner_team_id = team1_id
            winner_team_name = team1_name
        elif winner_side == "t2":
            winner_team_id = team2_id
            winner_team_name = team2_name
        else:
            winner_side = ""
            winner_team_id = ""
            winner_team_name = ""

        rows.append(
            {
                "match_id": match_id,
                "map_index": idx,
                "map_name": normalize_text(match.group("map_name")),
                "team1_score": to_int(match.group("score1")),
                "team2_score": to_int(match.group("score2")),
                "winner_side": winner_side,
                "winner_team_id": winner_team_id,
                "winner_team_name": winner_team_name,
                "fetched_at": fetched_at,
            }
        )
    return rows


def extract_player_rows(
    match_id: str,
    team1_id: str,
    team1_name: str,
    team2_id: str,
    team2_name: str,
    analysis_data: Dict[str, Any],
    fetched_at: str,
) -> List[Dict[str, Any]]:
    result = analysis_data.get("result") if isinstance(analysis_data, dict) else {}
    comparison = result.get("comparison") if isinstance(result, dict) else {}
    if not isinstance(comparison, dict):
        return []

    rows: List[Dict[str, Any]] = []
    specs: List[Tuple[str, str, str, Any]] = [
        ("t1", team1_id, team1_name, comparison.get("t1_player_stats") or []),
        ("t2", team2_id, team2_name, comparison.get("t2_player_stats") or []),
    ]
    for side, team_id, team_name, players in specs:
        if not isinstance(players, list):
            continue
        for idx, player in enumerate(players, start=1):
            if not isinstance(player, dict):
                continue
            rows.append(
                {
                    "match_id": match_id,
                    "team_side": side,
                    "team_id": team_id,
                    "team_name": team_name,
                    "player_id": normalize_text(player.get("id") or player.get("player_id")),
                    "player_name": normalize_text(player.get("name") or player.get("player_name")),
                    "country_name": normalize_text(player.get("country_name")),
                    "country_logo": normalize_text(player.get("country_logo")),
                    "rating": normalize_text(player.get("Rating") or player.get("rating")),
                    "adr": normalize_text(player.get("adr")),
                    "kast": normalize_text(player.get("kast")),
                    "kd": normalize_text(player.get("kd")),
                    "kpr": normalize_text(player.get("kpr")),
                    "mk_rating": normalize_text(player.get("mk_rating")),
                    "impact": normalize_text(player.get("impact")),
                    "swing": normalize_text(player.get("swing")),
                    "stat_index": idx,
                    "fetched_at": fetched_at,
                }
            )
    return rows


def extract_map_player_rows(
    match_id: str,
    team1_id: str,
    team1_name: str,
    team2_id: str,
    team2_name: str,
    data_data: Dict[str, Any],
    fetched_at: str,
) -> List[Dict[str, Any]]:
    match_data = data_data.get("match") if isinstance(data_data, dict) else {}
    bouts_state = match_data.get("bouts_state") if isinstance(match_data, dict) else []
    if not isinstance(bouts_state, list):
        return []

    rows: List[Dict[str, Any]] = []
    for fallback_idx, bout in enumerate(bouts_state, start=1):
        if not isinstance(bout, dict):
            continue
        map_index = to_int(bout.get("bout_num")) or fallback_idx
        map_name = normalize_text(bout.get("map_name"))
        bout_status = to_int(bout.get("status"))

        teams = [
            ("t1", team1_id, team1_name, bout.get("t1_pr_stats")),
            ("t2", team2_id, team2_name, bout.get("t2_pr_stats")),
        ]
        for side, team_id, team_name, players in teams:
            if not isinstance(players, list):
                continue
            for idx, player in enumerate(players, start=1):
                if not isinstance(player, dict):
                    continue
                rows.append(
                    {
                        "match_id": match_id,
                        "map_index": map_index,
                        "map_name": map_name,
                        "team_side": side,
                        "team_id": team_id,
                        "team_name": team_name,
                        "player_id": normalize_text(player.get("id")),
                        "player_name": normalize_text(player.get("name")),
                        "country_name": normalize_text(player.get("country_name")),
                        "country_logo": normalize_text(player.get("country_logo")),
                        "rating": normalize_text(player.get("rating")),
                        "mk_rating": normalize_text(player.get("mk_rating")),
                        "adr": normalize_text(player.get("adr")),
                        "kast": normalize_text(player.get("kast")),
                        "kpr": normalize_text(player.get("kpr")),
                        "kill": to_int(player.get("kill")),
                        "death": to_int(player.get("death")),
                        "assist": to_int(player.get("assist")),
                        "kd_rate": normalize_text(
                            player.get("kd_rate") or player.get("kdratio")
                        ),
                        "kd_diff": normalize_text(player.get("kd_diff")),
                        "stat_index": idx,
                        "bout_status": bout_status,
                        "fetched_at": fetched_at,
                    }
                )
    return rows


def collect_event_log_aggregates(event_log_data: Any) -> Tuple[Optional[int], Optional[int], str]:
    if not isinstance(event_log_data, dict):
        return None, None, ""

    event_list = event_log_data.get("list")
    if not isinstance(event_list, list):
        return None, None, normalize_text(event_log_data.get("to_ver"))

    map_names = {
        normalize_text(item.get("map_name"))
        for item in event_list
        if isinstance(item, dict) and normalize_text(item.get("map_name"))
    }
    return len(event_list), len(map_names), normalize_text(event_log_data.get("to_ver"))


def build_rows_for_match(
    base_row: Dict[str, Any],
) -> Optional[
    Tuple[
        Dict[str, Any],
        List[Dict[str, Any]],
        List[Dict[str, Any]],
        List[Dict[str, Any]],
    ]
]:
    match_id = normalize_text(base_row.get("match_id"))
    if not match_id:
        return None

    fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    team1_id = normalize_text(base_row.get("team1_id"))
    team2_id = normalize_text(base_row.get("team2_id"))
    team1_name = normalize_text(base_row.get("team1"))
    team2_name = normalize_text(base_row.get("team2"))

    analysis_resp = fetch_json(ANALYSIS_URL.format(match_id=match_id))
    data_resp = fetch_json(DATA_URL.format(match_id=match_id))
    if FETCH_EVENT_LOG:
        event_log_resp = fetch_json(EVENT_LOG_URL.format(match_id=match_id))
    else:
        event_log_resp = {"success": False, "status_code": None, "data": None}

    errors: List[str] = []
    if not analysis_resp["success"]:
        errors.append("analysis_v1")
    if not data_resp["success"]:
        errors.append("data")
    if FETCH_EVENT_LOG and not event_log_resp["success"]:
        errors.append("event_log")

    analysis_data = analysis_resp.get("data") if isinstance(analysis_resp.get("data"), dict) else {}
    data_data = data_resp.get("data") if isinstance(data_resp.get("data"), dict) else {}
    event_log_data = (
        event_log_resp.get("data") if isinstance(event_log_resp.get("data"), dict) else {}
    )

    result = analysis_data.get("result") if isinstance(analysis_data, dict) else {}
    comparison = result.get("comparison") if isinstance(result, dict) else {}
    if not isinstance(comparison, dict):
        comparison = {}
    team1_stats = comparison.get("t1_stats") if isinstance(comparison.get("t1_stats"), dict) else {}
    team2_stats = comparison.get("t2_stats") if isinstance(comparison.get("t2_stats"), dict) else {}

    event_log_count, event_log_map_count, event_log_to_ver = collect_event_log_aggregates(
        event_log_data
    )

    detail_row: Dict[str, Any] = {
        "match_id": match_id,
        "match_time": normalize_text(base_row.get("match_time")),
        "bo": to_int(base_row.get("bo")),
        "team1_id": team1_id,
        "team1": team1_name,
        "team2_id": team2_id,
        "team2": team2_name,
        "event_id": normalize_text(base_row.get("event_id")),
        "event_name": normalize_text(base_row.get("event_name")),
        "event_logo": normalize_text(base_row.get("event_logo")),
        "event_start_time": normalize_text(base_row.get("event_start_time")),
        "event_end_time": normalize_text(base_row.get("event_end_time")),
        "score1": to_int(base_row.get("score1")),
        "score2": to_int(base_row.get("score2")),
        "status": to_int(base_row.get("status")),
        "bout_count": to_int(base_row.get("bout_count")),
        "bout_details": normalize_text(base_row.get("bout_details")),
        "analysis_success": 1 if analysis_resp["success"] else 0,
        "analysis_state_ver": normalize_text(analysis_data.get("state_ver")),
        "data_success": 1 if data_resp["success"] else 0,
        "data_state_ver": normalize_text(data_data.get("state_ver")),
        "event_log_success": 1 if (FETCH_EVENT_LOG and event_log_resp["success"]) else 0,
        "event_log_to_ver": event_log_to_ver,
        "event_log_count": event_log_count,
        "event_log_map_count": event_log_map_count,
        "team1_form_rating": normalize_text(team1_stats.get("rating")),
        "team2_form_rating": normalize_text(team2_stats.get("rating")),
        "team1_form_win_rate": normalize_text(team1_stats.get("win_rate")),
        "team2_form_win_rate": normalize_text(team2_stats.get("win_rate")),
        "fetch_error": ",".join(errors),
        "fetched_at": fetched_at,
        "schema_version": SCHEMA_VERSION,
    }

    player_rows = extract_player_rows(
        match_id=match_id,
        team1_id=team1_id,
        team1_name=team1_name,
        team2_id=team2_id,
        team2_name=team2_name,
        analysis_data=analysis_data,
        fetched_at=fetched_at,
    )
    map_rows = parse_bout_details(
        match_id=match_id,
        bout_details=normalize_text(base_row.get("bout_details")),
        team1_id=team1_id,
        team1_name=team1_name,
        team2_id=team2_id,
        team2_name=team2_name,
        fetched_at=fetched_at,
    )
    map_player_rows = extract_map_player_rows(
        match_id=match_id,
        team1_id=team1_id,
        team1_name=team1_name,
        team2_id=team2_id,
        team2_name=team2_name,
        data_data=data_data,
        fetched_at=fetched_at,
    )
    return detail_row, player_rows, map_rows, map_player_rows


def load_base_rows_from_csv(
    max_matches: int,
    target_match_ids: Optional[set[str]] = None,
) -> List[Dict[str, Any]]:
    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"Missing match result csv: {INPUT_FILE}")

    with INPUT_FILE.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    for row in rows:
        for col in BASE_REQUIRED_COLUMNS:
            row.setdefault(col, "")
        row["match_id"] = normalize_text(row.get("match_id"))

    rows = [row for row in rows if row["match_id"]]

    unique: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        mid = row["match_id"]
        if mid not in unique:
            unique[mid] = row
    rows = list(unique.values())

    if target_match_ids:
        rows = [row for row in rows if row["match_id"] in target_match_ids]

    if max_matches > 0:
        rows = rows[:max_matches]
    print(f"[match_result_detail] source=csv rows={len(rows)}")
    return rows


def table_exists(cur: pymysql.cursors.DictCursor, table_name: str) -> bool:
    cur.execute("SHOW TABLES LIKE %s", (table_name,))
    return cur.fetchone() is not None


def normalize_base_row(row: Dict[str, Any]) -> Dict[str, Any]:
    normalized: Dict[str, Any] = {}
    for col in BASE_REQUIRED_COLUMNS:
        value = row.get(col, "")
        if isinstance(value, datetime):
            value = value.strftime("%Y-%m-%d %H:%M:%S")
        normalized[col] = "" if value is None else value
    normalized["match_id"] = normalize_text(normalized.get("match_id"))
    return normalized


def load_base_rows_from_mysql(
    max_matches: int,
    target_match_ids: Optional[set[str]] = None,
) -> List[Dict[str, Any]]:
    target_ids = sorted(target_match_ids or set())
    where_sql = ""
    where_params: List[Any] = []
    if target_ids:
        placeholders = ", ".join(["%s"] * len(target_ids))
        where_sql = f" WHERE match_id IN ({placeholders})"
        where_params = target_ids

    with pymysql.connect(**DB_CONFIG) as conn:
        with conn.cursor() as cur:
            source_table = ""
            select_sql = ""
            if table_exists(cur, "match_result"):
                source_table = "match_result"
                select_sql = (
                    "SELECT match_id, match_time, bo, team1_id, team1, team2_id, team2, "
                    "event_id, event_name, event_logo, event_start_time, event_end_time, "
                    "score1, score2, status, bout_count, bout_details "
                    "FROM match_result"
                )
            elif table_exists(cur, "match_schedule"):
                source_table = "match_schedule"
                select_sql = (
                    "SELECT match_id, match_time, bo, team1_id, team1, team2_id, team2, "
                    "event_id, event_name, event_logo, event_start_time, event_end_time, "
                    "score1, score2, status, NULL AS bout_count, '' AS bout_details "
                    "FROM match_schedule"
                )
            else:
                raise RuntimeError("Neither match_result nor match_schedule table exists in MySQL.")

            sql = f"{select_sql}{where_sql} ORDER BY match_time DESC"
            cur.execute(sql, tuple(where_params))
            fetched_rows = list(cur.fetchall() or [])

    unique: Dict[str, Dict[str, Any]] = {}
    for raw in fetched_rows:
        row = normalize_base_row(raw)
        match_id = row["match_id"]
        if not match_id:
            continue
        if match_id not in unique:
            unique[match_id] = row

    rows = list(unique.values())
    if max_matches > 0:
        rows = rows[:max_matches]
    print(f"[match_result_detail] source=mysql table={source_table} rows={len(rows)}")
    return rows


def load_base_rows(
    max_matches: int,
    target_match_ids: Optional[set[str]] = None,
    source: str = DEFAULT_BASE_SOURCE,
) -> List[Dict[str, Any]]:
    source_mode = normalize_source(source)
    if source_mode == "mysql":
        return load_base_rows_from_mysql(max_matches=max_matches, target_match_ids=target_match_ids)
    if source_mode == "csv":
        return load_base_rows_from_csv(max_matches=max_matches, target_match_ids=target_match_ids)

    try:
        return load_base_rows_from_mysql(max_matches=max_matches, target_match_ids=target_match_ids)
    except Exception as exc:
        print(f"[match_result_detail] source=auto mysql failed, fallback csv: {exc}")
        return load_base_rows_from_csv(max_matches=max_matches, target_match_ids=target_match_ids)


def crawl_match_details(
    max_matches: int = DEFAULT_MAX_MATCHES,
    max_workers: int = DEFAULT_MAX_WORKERS,
    batch_size: int = DEFAULT_BATCH_SIZE,
    source: str = DEFAULT_BASE_SOURCE,
) -> None:
    if FORCE_REFRESH:
        for path in (
            OUTPUT_DETAIL_FILE,
            OUTPUT_PLAYER_FILE,
            OUTPUT_MAP_FILE,
            OUTPUT_MAP_PLAYER_FILE,
        ):
            if path.exists():
                path.unlink()
                print(f"[match_result_detail] removed old output: {path}")

    backup_legacy_compact_file_if_needed()
    base_rows = load_base_rows(
        max_matches=max_matches,
        target_match_ids=TARGET_MATCH_IDS or None,
        source=source,
    )

    existing_ids: set[str] = set()
    if INCREMENTAL_ENABLED and not FORCE_REFRESH:
        required_outputs = [
            OUTPUT_DETAIL_FILE,
            OUTPUT_PLAYER_FILE,
            OUTPUT_MAP_FILE,
            OUTPUT_MAP_PLAYER_FILE,
        ]
        if all(path.exists() for path in required_outputs):
            existing_ids = load_existing_match_ids(OUTPUT_DETAIL_FILE)
            if existing_ids:
                base_rows = [
                    row for row in base_rows if row["match_id"] not in existing_ids
                ]
        else:
            missing_files = [path.name for path in required_outputs if not path.exists()]
            print(
                "[match_result_detail] incremental disabled for this run, "
                f"missing output files: {missing_files}"
            )

    if TARGET_MATCH_IDS:
        print(f"[match_result_detail] target_match_ids={sorted(TARGET_MATCH_IDS)}")

    total = len(base_rows)
    print(
        f"[match_result_detail] pending={total} existing={len(existing_ids)} "
        f"max_workers={max_workers} batch_size={batch_size} source={normalize_source(source)} "
        f"http_pool={HTTP_POOL_SIZE}"
    )
    if total == 0:
        print("[match_result_detail] no pending matches, skip.")
        return

    done = 0
    for start in range(0, total, batch_size):
        batch = base_rows[start : start + batch_size]
        details_out: List[Dict[str, Any]] = []
        players_out: List[Dict[str, Any]] = []
        maps_out: List[Dict[str, Any]] = []
        map_players_out: List[Dict[str, Any]] = []

        workers = max(1, min(max_workers, len(batch)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(build_rows_for_match, row) for row in batch]
            for future in as_completed(futures):
                try:
                    packed = future.result()
                except Exception:
                    packed = None
                if not packed:
                    continue
                detail_row, player_rows, map_rows, map_player_rows = packed
                details_out.append(detail_row)
                players_out.extend(player_rows)
                maps_out.extend(map_rows)
                map_players_out.extend(map_player_rows)

        details_out.sort(key=lambda x: normalize_text(x.get("match_time")), reverse=True)
        players_out.sort(
            key=lambda x: (
                normalize_text(x.get("match_id")),
                normalize_text(x.get("team_side")),
                to_int(x.get("stat_index")) or 0,
            )
        )
        maps_out.sort(
            key=lambda x: (
                normalize_text(x.get("match_id")),
                to_int(x.get("map_index")) or 0,
            )
        )
        map_players_out.sort(
            key=lambda x: (
                normalize_text(x.get("match_id")),
                to_int(x.get("map_index")) or 0,
                normalize_text(x.get("team_side")),
                to_int(x.get("stat_index")) or 0,
            )
        )

        write_rows(OUTPUT_DETAIL_FILE, details_out, DETAIL_COLUMNS)
        write_rows(OUTPUT_PLAYER_FILE, players_out, PLAYER_COLUMNS)
        write_rows(OUTPUT_MAP_FILE, maps_out, MAP_COLUMNS)
        write_rows(OUTPUT_MAP_PLAYER_FILE, map_players_out, MAP_PLAYER_COLUMNS)

        done += len(batch)
        print(
            f"[match_result_detail] batch_done={done}/{total} details={len(details_out)} "
            f"players={len(players_out)} maps={len(maps_out)} "
            f"map_players={len(map_players_out)}"
        )
        if SLEEP_SECONDS > 0:
            time.sleep(SLEEP_SECONDS)

    print(
        "[match_result_detail] completed: "
        f"{OUTPUT_DETAIL_FILE.name}, {OUTPUT_PLAYER_FILE.name}, "
        f"{OUTPUT_MAP_FILE.name}, {OUTPUT_MAP_PLAYER_FILE.name}"
    )


if __name__ == "__main__":
    crawl_match_details()
