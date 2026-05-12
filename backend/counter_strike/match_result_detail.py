from __future__ import annotations

import csv
import json
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
FETCH_ERROR_MAX_LENGTH = 255

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
    "database": os.getenv("CS_DB_NAME", "esports"),
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


def join_fetch_errors(errors: List[str]) -> str:
    text = ",".join(error for error in errors if error)
    if len(text) <= FETCH_ERROR_MAX_LENGTH:
        return text
    return text[: FETCH_ERROR_MAX_LENGTH - 3] + "..."


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


def to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip().replace("%", "")
    if text == "":
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def format_number(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return ""
    return f"{value:.{digits}f}"


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


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


def _normalize_winner_side(value: Any) -> str:
    text = normalize_text(value).lower()
    if text in {"t1", "team1", "1"}:
        return "t1"
    if text in {"t2", "team2", "2"}:
        return "t2"
    return ""


def extract_team_score_from_bout(bout: Dict[str, Any], side: str) -> Optional[int]:
    direct_key = f"{side}_score"
    direct_score = to_int(bout.get(direct_key))
    if direct_score is not None:
        return direct_score
    stats = bout.get(f"{side}_stats")
    if isinstance(stats, dict):
        for key in ("all_score", "quick_score"):
            score = to_int(stats.get(key))
            if score is not None:
                return score
        fh_score = to_int(stats.get("fh_score")) or 0
        sh_score = to_int(stats.get("sh_score")) or 0
        ot_score = to_int(stats.get("ot_score")) or 0
        total = fh_score + sh_score + ot_score
        if total > 0:
            return total
    return None


def extract_round_count_from_bout(bout: Dict[str, Any]) -> Optional[int]:
    team1_score = extract_team_score_from_bout(bout, "t1")
    team2_score = extract_team_score_from_bout(bout, "t2")
    if team1_score is not None and team2_score is not None:
        total = team1_score + team2_score
        if total > 0:
            return total

    totals: List[int] = []
    for side in ("t1", "t2"):
        stats = bout.get(f"{side}_stats")
        if not isinstance(stats, dict):
            continue
        total = 0
        for key in ("fh_data", "sh_data", "ot_data"):
            values = stats.get(key)
            if isinstance(values, list):
                total += len(values)
        if total > 0:
            totals.append(total)
    if totals:
        return max(totals)
    return None


def parse_map_rows_from_data(
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
        t1_players = bout.get("t1_pr_stats")
        t2_players = bout.get("t2_pr_stats")
        has_player_stats = (
            isinstance(t1_players, list)
            and len(t1_players) > 0
            or isinstance(t2_players, list)
            and len(t2_players) > 0
        )
        map_index = to_int(bout.get("bout_num")) or fallback_idx
        map_name = normalize_text(
            bout.get("map_name")
            or bout.get("disp_name")
            or bout.get("display")
        )
        team1_score = extract_team_score_from_bout(bout, "t1")
        team2_score = extract_team_score_from_bout(bout, "t2")
        if team1_score is None and team2_score is None and not has_player_stats:
            continue

        winner_side = _normalize_winner_side(bout.get("result"))
        if not winner_side and team1_score is not None and team2_score is not None:
            if team1_score > team2_score:
                winner_side = "t1"
            elif team2_score > team1_score:
                winner_side = "t2"

        winner_team_id = ""
        winner_team_name = ""
        if winner_side == "t1":
            winner_team_id = team1_id
            winner_team_name = team1_name
        elif winner_side == "t2":
            winner_team_id = team2_id
            winner_team_name = team2_name

        rows.append(
            {
                "match_id": match_id,
                "map_index": map_index,
                "map_name": map_name,
                "team1_score": team1_score,
                "team2_score": team2_score,
                "winner_side": winner_side,
                "winner_team_id": winner_team_id,
                "winner_team_name": winner_team_name,
                "fetched_at": fetched_at,
            }
        )
    return rows


def merge_map_rows(
    preferred_rows: List[Dict[str, Any]],
    fallback_rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}

    def row_key(row: Dict[str, Any], fallback_idx: int) -> str:
        map_index = to_int(row.get("map_index")) or fallback_idx
        map_name = normalize_text(row.get("map_name")).lower()
        return f"{map_index}:{map_name}"

    for idx, row in enumerate(fallback_rows, start=1):
        merged[row_key(row, idx)] = dict(row)

    for idx, row in enumerate(preferred_rows, start=1):
        key = row_key(row, idx)
        base = merged.get(key, {})
        out = {**base, **row}
        # Keep more informative values.
        for field in ("map_name", "winner_side", "winner_team_id", "winner_team_name"):
            if normalize_text(base.get(field)) and not normalize_text(out.get(field)):
                out[field] = base.get(field)
        for field in ("team1_score", "team2_score"):
            if out.get(field) is None and base.get(field) is not None:
                out[field] = base.get(field)
        merged[key] = out

    rows = list(merged.values())
    rows.sort(key=lambda x: (to_int(x.get("map_index")) or 9999, normalize_text(x.get("map_name"))))
    return rows


def derive_match_fields_from_maps(
    map_rows: List[Dict[str, Any]],
) -> Tuple[Optional[int], Optional[int], int, str]:
    if not map_rows:
        return None, None, 0, ""

    wins_t1 = 0
    wins_t2 = 0
    bout_parts: List[str] = []

    for row in map_rows:
        map_name = normalize_text(row.get("map_name")) or "Unknown"
        s1 = to_int(row.get("team1_score"))
        s2 = to_int(row.get("team2_score"))
        winner_side = _normalize_winner_side(row.get("winner_side"))

        if not winner_side and s1 is not None and s2 is not None:
            if s1 > s2:
                winner_side = "t1"
            elif s2 > s1:
                winner_side = "t2"

        if winner_side == "t1":
            wins_t1 += 1
        elif winner_side == "t2":
            wins_t2 += 1

        if s1 is None and s2 is None and not winner_side:
            continue

        ds1 = 0 if s1 is None else s1
        ds2 = 0 if s2 is None else s2
        part = f"{map_name}:{ds1}-{ds2}"
        if winner_side:
            part += f"(winner:{winner_side})"
        bout_parts.append(part)

    bout_details = " | ".join(bout_parts)
    bout_count = len(map_rows)
    if wins_t1 == 0 and wins_t2 == 0:
        return None, None, bout_count, bout_details
    return wins_t1, wins_t2, bout_count, bout_details


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


def estimate_kill_rounds(player: Dict[str, Any], rounds: int) -> int:
    kills = to_int(player.get("kill")) or 0
    if kills <= 0 or rounds <= 0:
        return 0

    k2 = to_int(player.get("k2"))
    k3 = to_int(player.get("k3"))
    k4 = to_int(player.get("k4"))
    k5 = to_int(player.get("k5"))
    if any(value is not None for value in (k2, k3, k4, k5)):
        k2 = k2 or 0
        k3 = k3 or 0
        k4 = k4 or 0
        k5 = k5 or 0
        single_kill_rounds = kills - (2 * k2 + 3 * k3 + 4 * k4 + 5 * k5)
        kill_rounds = single_kill_rounds + k2 + k3 + k4 + k5
        return max(0, min(rounds, kill_rounds))

    multi_kill_rounds = to_int(player.get("more_kill")) or 0
    return max(0, min(rounds, kills - multi_kill_rounds))


def estimate_kast(player: Dict[str, Any], rounds: Optional[int]) -> str:
    existing = normalize_text(player.get("kast"))
    if existing:
        return existing
    if not rounds or rounds <= 0:
        return ""

    kills = to_int(player.get("kill")) or 0
    deaths = to_int(player.get("death")) or 0
    assists = to_int(player.get("assist")) or 0
    traded_deaths = to_int(player.get("traded_death")) or 0

    round_count = max(1, rounds)
    death_rounds = min(max(0, deaths), round_count)
    survived_rounds = max(0, round_count - death_rounds)
    kill_rounds = estimate_kill_rounds(player, round_count)
    assist_rounds = min(max(0, assists), round_count)

    # KAST is a per-round union of Kill/Assist/Survive/Traded. The 5E map payload
    # exposes aggregates, not the exact per-round union, so estimate only the
    # death rounds that were likely covered by K/A, then add traded deaths.
    death_rate = death_rounds / round_count
    death_rounds_with_action = int(round((kill_rounds + assist_rounds) * death_rate * 0.8))
    death_rounds_with_action = min(death_rounds, max(0, death_rounds_with_action))
    traded_only_rounds = min(
        max(0, death_rounds - death_rounds_with_action),
        max(0, traded_deaths),
    )
    kast_rounds = min(round_count, survived_rounds + death_rounds_with_action + traded_only_rounds)
    return format_number(kast_rounds / round_count * 100, 1)


def estimate_impact(player: Dict[str, Any], rounds: Optional[int]) -> Optional[float]:
    existing = to_float(player.get("impact"))
    if existing is not None and existing > 0:
        return existing
    if not rounds or rounds <= 0:
        return None

    kills = to_int(player.get("kill")) or 0
    assists = to_int(player.get("assist")) or 0
    kpr = to_float(player.get("kpr"))
    if kpr is None:
        kpr = kills / rounds
    apr = assists / rounds
    return clamp((2.13 * kpr) + (0.42 * apr) - 0.41, 0.0, 3.0)


def estimate_rating(player: Dict[str, Any], rounds: Optional[int], kast: str) -> str:
    existing = normalize_text(player.get("rating") or player.get("Rating"))
    if existing:
        return existing
    if not rounds or rounds <= 0:
        return ""

    kills = to_int(player.get("kill")) or 0
    deaths = to_int(player.get("death")) or 0
    adr = to_float(player.get("adr")) or 0.0
    kpr = to_float(player.get("kpr"))
    if kpr is None:
        kpr = kills / rounds
    dpr = to_float(player.get("dpr"))
    if dpr is None:
        dpr = deaths / rounds
    kast_value = to_float(kast)
    if kast_value is None:
        kast_value = to_float(estimate_kast(player, rounds)) or 0.0
    impact = estimate_impact(player, rounds) or 0.0

    # Public HLTV Rating 2.0-style approximation. The official Rating 2.0
    # formula is not published, so this is only used when the provider omits it.
    rating = (
        (0.0073 * kast_value)
        + (0.3591 * kpr)
        - (0.5329 * dpr)
        + (0.2372 * impact)
        + (0.0032 * adr)
        + 0.1587
    )
    return format_number(clamp(rating, 0.0, 3.0), 2)


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
        round_count = extract_round_count_from_bout(bout)

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
                kast = estimate_kast(player, round_count)
                rating = estimate_rating(player, round_count, kast)
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
                        "rating": rating,
                        "mk_rating": normalize_text(player.get("mk_rating")),
                        "adr": normalize_text(player.get("adr")),
                        "kast": kast,
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


def validate_completed_map_player_rows(
    map_rows: List[Dict[str, Any]],
    map_player_rows: List[Dict[str, Any]],
) -> Tuple[bool, str]:
    completed_map_indexes = {
        to_int(row.get("map_index"))
        for row in map_rows
        if to_int(row.get("team1_score")) is not None
        and to_int(row.get("team2_score")) is not None
    }
    completed_map_indexes = {idx for idx in completed_map_indexes if idx is not None}
    if not completed_map_indexes:
        return False, "data_no_completed_maps"

    counts: Dict[Tuple[int, str], int] = {}
    for row in map_player_rows:
        map_index = to_int(row.get("map_index"))
        if map_index not in completed_map_indexes:
            continue
        side = normalize_text(row.get("team_side")).lower()
        if side in {"team1", "a", "1"}:
            side = "t1"
        elif side in {"team2", "b", "2"}:
            side = "t2"
        if side not in {"t1", "t2"}:
            continue
        counts[(map_index, side)] = counts.get((map_index, side), 0) + 1

    missing: List[str] = []
    for map_index in sorted(completed_map_indexes):
        for side in ("t1", "t2"):
            count = counts.get((map_index, side), 0)
            if count < 5:
                missing.append(f"map{map_index}:{side}={count}")

    if missing:
        return False, "data_incomplete_map_players:" + ",".join(missing)
    return True, ""


def player_group_key(row: Dict[str, Any]) -> str:
    player_id = normalize_text(row.get("player_id"))
    if player_id:
        return f"id:{player_id}"
    return (
        f"name:{normalize_text(row.get('team_side')).lower()}:"
        f"{normalize_text(row.get('player_name')).lower()}"
    )


def aggregate_map_player_rows(
    map_rows: List[Dict[str, Any]],
    map_player_rows: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    rounds_by_map: Dict[int, int] = {}
    for row in map_rows:
        map_index = to_int(row.get("map_index"))
        if map_index is None:
            continue
        score1 = to_int(row.get("team1_score"))
        score2 = to_int(row.get("team2_score"))
        if score1 is None or score2 is None:
            continue
        round_count = score1 + score2
        if round_count > 0:
            rounds_by_map[map_index] = round_count

    grouped: Dict[str, Dict[str, Any]] = {}
    for row in map_player_rows:
        key = player_group_key(row)
        if not key:
            continue
        map_index = to_int(row.get("map_index"))
        rounds = rounds_by_map.get(map_index or 0)
        if not rounds:
            continue

        group = grouped.setdefault(
            key,
            {
                "sample": row,
                "rounds": 0,
                "kills": 0,
                "deaths": 0,
                "assists": 0,
                "adr_weighted": 0.0,
                "kast_weighted": 0.0,
                "rating_weighted": 0.0,
                "mk_rating_weighted": 0.0,
                "kast_rounds": 0,
                "rating_rounds": 0,
                "mk_rating_rounds": 0,
            },
        )
        group["rounds"] += rounds
        group["kills"] += to_int(row.get("kill")) or 0
        group["deaths"] += to_int(row.get("death")) or 0
        group["assists"] += to_int(row.get("assist")) or 0

        adr = to_float(row.get("adr"))
        if adr is not None:
            group["adr_weighted"] += adr * rounds
        kast = to_float(row.get("kast"))
        if kast is not None:
            group["kast_weighted"] += kast * rounds
            group["kast_rounds"] += rounds
        rating = to_float(row.get("rating"))
        if rating is not None:
            group["rating_weighted"] += rating * rounds
            group["rating_rounds"] += rounds
        mk_rating = to_float(row.get("mk_rating"))
        if mk_rating is not None:
            group["mk_rating_weighted"] += mk_rating * rounds
            group["mk_rating_rounds"] += rounds

    return grouped


def apply_player_row_fallbacks(
    player_rows: List[Dict[str, Any]],
    map_rows: List[Dict[str, Any]],
    map_player_rows: List[Dict[str, Any]],
    fetched_at: str,
) -> List[Dict[str, Any]]:
    grouped = aggregate_map_player_rows(map_rows, map_player_rows)
    if not grouped:
        return player_rows

    existing_keys = {player_group_key(row) for row in player_rows}
    for row in player_rows:
        group = grouped.get(player_group_key(row))
        if not group:
            continue
        rounds = int(group.get("rounds") or 0)
        if rounds <= 0:
            continue
        kills = int(group.get("kills") or 0)
        deaths = int(group.get("deaths") or 0)

        if not normalize_text(row.get("adr")):
            row["adr"] = format_number(group.get("adr_weighted", 0.0) / rounds, 1)
        if not normalize_text(row.get("kast")) and group.get("kast_rounds"):
            row["kast"] = format_number(group.get("kast_weighted", 0.0) / group["kast_rounds"], 1)
        if not normalize_text(row.get("rating")) and group.get("rating_rounds"):
            row["rating"] = format_number(group.get("rating_weighted", 0.0) / group["rating_rounds"], 2)
        if not normalize_text(row.get("mk_rating")) and group.get("mk_rating_rounds"):
            row["mk_rating"] = format_number(
                group.get("mk_rating_weighted", 0.0) / group["mk_rating_rounds"],
                2,
            )
        if not normalize_text(row.get("kd")):
            row["kd"] = format_number(kills / max(1, deaths), 2)
        if not normalize_text(row.get("kpr")):
            row["kpr"] = format_number(kills / rounds, 2)

    next_index_by_side: Dict[str, int] = {}
    for row in player_rows:
        side = normalize_text(row.get("team_side")) or "t1"
        next_index_by_side[side] = max(
            next_index_by_side.get(side, 0),
            to_int(row.get("stat_index")) or 0,
        )

    for key, group in grouped.items():
        if key in existing_keys:
            continue
        sample = group.get("sample") or {}
        rounds = int(group.get("rounds") or 0)
        if rounds <= 0:
            continue
        kills = int(group.get("kills") or 0)
        deaths = int(group.get("deaths") or 0)
        side = normalize_text(sample.get("team_side")) or "t1"
        next_index_by_side[side] = next_index_by_side.get(side, 0) + 1
        player_rows.append(
            {
                "match_id": normalize_text(sample.get("match_id")),
                "team_side": side,
                "team_id": normalize_text(sample.get("team_id")),
                "team_name": normalize_text(sample.get("team_name")),
                "player_id": normalize_text(sample.get("player_id")),
                "player_name": normalize_text(sample.get("player_name")),
                "country_name": normalize_text(sample.get("country_name")),
                "country_logo": normalize_text(sample.get("country_logo")),
                "rating": (
                    format_number(group.get("rating_weighted", 0.0) / group["rating_rounds"], 2)
                    if group.get("rating_rounds")
                    else ""
                ),
                "adr": format_number(group.get("adr_weighted", 0.0) / rounds, 1),
                "kast": (
                    format_number(group.get("kast_weighted", 0.0) / group["kast_rounds"], 1)
                    if group.get("kast_rounds")
                    else ""
                ),
                "kd": format_number(kills / max(1, deaths), 2),
                "kpr": format_number(kills / rounds, 2),
                "mk_rating": (
                    format_number(group.get("mk_rating_weighted", 0.0) / group["mk_rating_rounds"], 2)
                    if group.get("mk_rating_rounds")
                    else ""
                ),
                "impact": "",
                "swing": "",
                "stat_index": next_index_by_side[side],
                "fetched_at": fetched_at,
            }
        )

    return player_rows


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


ROUND_EVENT_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS match_result_round_events (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    match_id VARCHAR(50) NOT NULL,
    event_id VARCHAR(50),
    event_name VARCHAR(255),
    bout_id VARCHAR(80),
    map_index INT,
    map_name VARCHAR(100),
    source_map_name VARCHAR(100),
    round_number INT,
    round_global_index INT,
    event_type VARCHAR(32),
    event_type_code VARCHAR(16),
    update_version VARCHAR(64),
    source_order INT,
    team_side VARCHAR(32),
    team_name VARCHAR(100),
    player_id VARCHAR(50),
    player_name VARCHAR(100),
    related_player_id VARCHAR(50),
    related_player_name VARCHAR(100),
    weapon VARCHAR(100),
    weapon_logo VARCHAR(255),
    bomb_site VARCHAR(16),
    winner_side VARCHAR(32),
    win_type VARCHAR(64),
    score_ct INT,
    score_t INT,
    event_text VARCHAR(500),
    raw_event JSON,
    fetched_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_match_update_source (match_id, update_version, source_order),
    KEY idx_match_map_round (match_id, map_index, round_number),
    KEY idx_match_event_type (match_id, event_type),
    KEY idx_player_id (player_id),
    KEY idx_related_player_id (related_player_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

EVENT_TYPE_NAMES_ROUND = {
    "1": "round_start",
    "2": "round_end",
    "3": "player_join",
    "4": "player_quit",
    "6": "bomb_planted",
    "8": "kill",
    "10": "match_started",
}

ROUND_EVENT_COLUMNS = [
    "match_id", "event_id", "event_name", "bout_id", "map_index", "map_name",
    "source_map_name", "round_number", "round_global_index", "event_type",
    "event_type_code", "update_version", "source_order", "team_side", "team_name",
    "player_id", "player_name", "related_player_id", "related_player_name",
    "weapon", "weapon_logo", "bomb_site", "winner_side", "win_type",
    "score_ct", "score_t", "event_text", "raw_event", "fetched_at",
]


def _parse_log_info(item: Dict[str, Any]) -> Dict[str, Any]:
    raw = item.get("log_info")
    if isinstance(raw, dict):
        return raw
    try:
        payload = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _normalize_map_name(value: Any) -> str:
    text = normalize_text(value)
    if text.lower().startswith("de_"):
        text = text[3:]
    return text[:1].upper() + text[1:] if text else ""


def _int_update_version(item: Dict[str, Any]) -> int:
    value = normalize_text(item.get("update_version"))
    try:
        return int(value)
    except ValueError:
        return 0


def _parse_round_event_rows(
    match_id: str,
    event_id: str,
    event_name: str,
    event_items: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sorted_items = sorted(
        enumerate(event_items),
        key=lambda pair: (to_int(pair[1].get("bout_num")) or 0, _int_update_version(pair[1]), pair[0]),
    )
    current_round_by_map: Dict[int, Optional[int]] = {}
    max_round_by_map: Dict[int, int] = {}
    round_global_lookup: Dict[Tuple[int, int], int] = {}
    rows: List[Dict[str, Any]] = []

    for source_order, item in sorted_items:
        if not isinstance(item, dict):
            continue
        info = _parse_log_info(item)
        event_type_code = normalize_text(info.get("type"))
        event_type = EVENT_TYPE_NAMES_ROUND.get(event_type_code, f"type_{event_type_code}" if event_type_code else "unknown")
        map_index = to_int(item.get("bout_num")) or to_int(info.get("round_start", {}).get("bout_num"))
        source_map_name = normalize_text(item.get("map_name")) or normalize_text(info.get("round_start", {}).get("map"))
        map_name = _normalize_map_name(source_map_name)

        round_start = info.get("round_start") if isinstance(info.get("round_start"), dict) else {}
        round_end = info.get("round_end") if isinstance(info.get("round_end"), dict) else {}
        kill = info.get("kill") if isinstance(info.get("kill"), dict) else {}
        bomb_planted = info.get("bomb_planted") if isinstance(info.get("bomb_planted"), dict) else {}
        bomb_defused = info.get("bomb_defused") if isinstance(info.get("bomb_defused"), dict) else {}
        player_join = info.get("player_join") if isinstance(info.get("player_join"), dict) else {}
        player_quit = info.get("player_quit") if isinstance(info.get("player_quit"), dict) else {}
        assist = info.get("assist") if isinstance(info.get("assist"), dict) else {}
        match_started = info.get("match_started") if isinstance(info.get("match_started"), dict) else {}
        suicide = info.get("suicide") if isinstance(info.get("suicide"), dict) else {}

        if not map_name:
            map_name = _normalize_map_name(match_started.get("map_name"))

        round_number = to_int(round_start.get("round_num"))
        if round_number is not None and map_index is not None:
            known_max = max_round_by_map.get(map_index, 0)
            if event_type == "round_start" and round_number <= known_max:
                round_number = known_max + 1
            current_round_by_map[map_index] = round_number
            max_round_by_map[map_index] = max(known_max, round_number)
        elif map_index is not None:
            round_number = current_round_by_map.get(map_index)

        score_ct = to_int(round_end.get("ct_score"))
        score_t = to_int(round_end.get("t_score"))
        if event_type == "round_end" and map_index is not None:
            score_round = (score_ct or 0) + (score_t or 0) if score_ct is not None or score_t is not None else None
            if score_round:
                round_number = score_round
            elif round_number is None:
                inferred = max_round_by_map.get(map_index)
                round_number = inferred or None
            if round_number is not None:
                current_round_by_map[map_index] = round_number
                max_round_by_map[map_index] = max(max_round_by_map.get(map_index, 0), round_number)

        if map_index is not None and round_number is not None:
            round_global_lookup.setdefault((map_index, round_number), len(round_global_lookup) + 1)
        round_global_index = round_global_lookup.get((map_index, round_number)) if map_index is not None and round_number is not None else None

        player_id = ""
        player_name = ""
        related_player_id = ""
        related_player_name = ""
        team_side = ""
        weapon = ""
        weapon_logo = ""
        bomb_site = ""
        team_name = ""
        event_text = event_type

        if event_type == "kill":
            player_id = normalize_text(kill.get("killer_id"))
            player_name = normalize_text(kill.get("killer_nick")) or normalize_text(kill.get("killer_name"))
            related_player_id = normalize_text(kill.get("victim_id"))
            related_player_name = normalize_text(kill.get("victim_nick")) or normalize_text(kill.get("victim_name"))
            team_side = normalize_text(kill.get("killer_side"))
            weapon = normalize_text(kill.get("weapon"))
            weapon_logo = normalize_text(kill.get("weapon_logo"))
            event_text = f"{player_name or '-'} 击杀 {related_player_name or '-'}"
        elif event_type == "bomb_planted":
            player_name = normalize_text(bomb_planted.get("player_nick")) or normalize_text(bomb_planted.get("player_name"))
            bomb_site = normalize_text(bomb_planted.get("bomb_site"))
            event_text = f"{player_name or '-'} 安放炸弹 {bomb_site or ''}".strip()
        elif event_type == "round_end":
            team_side = normalize_text(round_end.get("winner"))
            event_text = f"回合结束 {score_ct if score_ct is not None else '-'}:{score_t if score_t is not None else '-'} {normalize_text(round_end.get('win_type'))}"
        elif event_type == "round_start":
            event_text = f"第 {round_number or '-'} 回合开始"
        elif event_type == "player_join":
            player_name = normalize_text(player_join.get("player_nick")) or normalize_text(player_join.get("player_name"))
            event_text = f"{player_name or '-'} 加入比赛"
        elif event_type == "player_quit":
            player_name = normalize_text(player_quit.get("player_nick")) or normalize_text(player_quit.get("player_name"))
            team_side = normalize_text(player_quit.get("player_side"))
            event_text = f"{player_name or '-'} 离开比赛"
        elif event_type == "match_started":
            event_text = "地图开始"
        elif normalize_text(bomb_defused.get("player_name")):
            player_name = normalize_text(bomb_defused.get("player_nick")) or normalize_text(bomb_defused.get("player_name"))
            event_text = f"{player_name} 拆除炸弹"
        elif normalize_text(suicide.get("player_name")):
            player_name = normalize_text(suicide.get("player_nick")) or normalize_text(suicide.get("player_name"))
            team_side = normalize_text(suicide.get("side"))
            weapon = normalize_text(suicide.get("weapon"))
            weapon_logo = normalize_text(suicide.get("weapon_logo"))
            event_text = f"{player_name} 自杀"

        if not related_player_name and normalize_text(assist.get("assister_name")):
            related_player_name = normalize_text(assist.get("assister_nick")) or normalize_text(assist.get("assister_name"))

        rows.append({
            "match_id": match_id,
            "event_id": event_id,
            "event_name": event_name,
            "bout_id": normalize_text(item.get("bout_id")),
            "map_index": map_index,
            "map_name": map_name,
            "source_map_name": source_map_name,
            "round_number": round_number,
            "round_global_index": round_global_index,
            "event_type": event_type,
            "event_type_code": event_type_code,
            "update_version": normalize_text(item.get("update_version")),
            "source_order": source_order,
            "team_side": team_side,
            "team_name": team_name,
            "player_id": player_id,
            "player_name": player_name,
            "related_player_id": related_player_id,
            "related_player_name": related_player_name,
            "weapon": weapon,
            "weapon_logo": weapon_logo,
            "bomb_site": bomb_site,
            "winner_side": normalize_text(round_end.get("winner")),
            "win_type": normalize_text(round_end.get("win_type")),
            "score_ct": score_ct,
            "score_t": score_t,
            "event_text": event_text,
            "raw_event": json.dumps({"source": item, "log_info": info}, ensure_ascii=False, separators=(",", ":")),
            "fetched_at": fetched_at,
        })

    return rows


def should_scrape_round_events(event_name: str, match_id: str, db_config: Dict[str, Any]) -> bool:
    if not event_name and not match_id:
        return False
    try:
        conn = pymysql.connect(**db_config)
        with conn.cursor() as cur:
            # DB_CONFIG uses DictCursor, so rows are dicts
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name = 'round_event_scrape_config'"
            )
            if cur.fetchone()["cnt"] == 0:
                return False
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM round_event_scrape_config WHERE enabled = 1 AND ("
                "  (target_type = 'tournament' AND %s LIKE target_value)"
                "  OR (target_type = 'match' AND target_value = %s)"
                ")",
                (event_name or "", match_id or ""),
            )
            return cur.fetchone()["cnt"] > 0
    except Exception:
        return False


def import_round_events_for_match(
    match_id: str,
    event_name: str,
    db_config: Dict[str, Any],
) -> int:
    if not should_scrape_round_events(event_name, match_id, db_config):
        return 0

    url = EVENT_LOG_URL.format(match_id=match_id).replace("limit=500", "limit=2000")
    resp = fetch_json(url)
    if not resp.get("success"):
        return 0
    data = resp.get("data")
    if not isinstance(data, dict):
        return 0
    items = data.get("list")
    if not isinstance(items, list):
        return 0
    items = [item for item in items if isinstance(item, dict)]
    if not items:
        return 0

    event_id = ""
    try:
        conn = pymysql.connect(**db_config)
        with conn.cursor() as cur:
            cur.execute("SELECT event_id FROM match_result WHERE match_id = %s", (match_id,))
            row = cur.fetchone()
            if row:
                event_id = normalize_text(row.get("event_id") or "")
        conn.close()
    except Exception:
        pass

    rows = _parse_round_event_rows(match_id, event_id, event_name, items)
    if not rows:
        return 0

    try:
        conn = pymysql.connect(**db_config)
        with conn.cursor() as cur:
            cur.execute(ROUND_EVENT_TABLE_SQL)
            placeholders = ", ".join(["%s"] * len(ROUND_EVENT_COLUMNS))
            cur.execute("DELETE FROM match_result_round_events WHERE match_id = %s", (match_id,))
            cur.executemany(
                f"INSERT INTO match_result_round_events ({', '.join(ROUND_EVENT_COLUMNS)}) VALUES ({placeholders})",
                [[row.get(col) for col in ROUND_EVENT_COLUMNS] for row in rows],
            )
        conn.close()
        return len(rows)
    except Exception:
        return 0


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
        "fetch_error": join_fetch_errors(errors),
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
    data_map_rows = parse_map_rows_from_data(
        match_id=match_id,
        team1_id=team1_id,
        team1_name=team1_name,
        team2_id=team2_id,
        team2_name=team2_name,
        data_data=data_data,
        fetched_at=fetched_at,
    )
    map_rows = merge_map_rows(map_rows, data_map_rows)

    map_player_rows = extract_map_player_rows(
        match_id=match_id,
        team1_id=team1_id,
        team1_name=team1_name,
        team2_id=team2_id,
        team2_name=team2_name,
        data_data=data_data,
        fetched_at=fetched_at,
    )
    data_complete, data_completeness_error = validate_completed_map_player_rows(
        map_rows,
        map_player_rows,
    )
    if not data_complete:
        detail_row["data_success"] = 0
        if data_completeness_error:
            errors.append(data_completeness_error)
            detail_row["fetch_error"] = join_fetch_errors(errors)

    derived_score1, derived_score2, derived_bout_count, derived_bout_details = derive_match_fields_from_maps(
        map_rows
    )
    if detail_row.get("score1") is None and derived_score1 is not None:
        detail_row["score1"] = derived_score1
    if detail_row.get("score2") is None and derived_score2 is not None:
        detail_row["score2"] = derived_score2
    if (to_int(detail_row.get("score1")) or 0) == 0 and (to_int(detail_row.get("score2")) or 0) == 0:
        if derived_score1 is not None and derived_score2 is not None:
            detail_row["score1"] = derived_score1
            detail_row["score2"] = derived_score2

    if not normalize_text(detail_row.get("bout_details")) and derived_bout_details:
        detail_row["bout_details"] = derived_bout_details
    if not to_int(detail_row.get("bout_count")) and derived_bout_count > 0:
        detail_row["bout_count"] = derived_bout_count

    player_rows = apply_player_row_fallbacks(
        player_rows,
        map_rows,
        map_player_rows,
        fetched_at,
    )

    # Keep derived fields for optional backfill into match_result/match_schedule by caller.
    detail_row["_derived_score1"] = derived_score1
    detail_row["_derived_score2"] = derived_score2
    detail_row["_derived_bout_count"] = derived_bout_count
    detail_row["_derived_bout_details"] = derived_bout_details

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
