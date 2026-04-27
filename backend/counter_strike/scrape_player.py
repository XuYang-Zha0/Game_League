"""
scrape_zywoo_csv_final.py
================================

This script fetches data for the Counter‑Strike player ZywOo (ID: csgo_pl_11893)
from 5EPlay's esports data API and writes the results into CSV files suitable
for loading into a relational database.  The script attempts to make live
HTTP requests against the 5EPlay API.  If those requests fail (for example
due to a 403 Forbidden response or network constraints), the script falls
back to reading sample JSON contained in the provided text files.  These
sample files mimic the API responses and are expected to be placed in the
same directory as this script or in the current working directory.  The
fallback filenames are fixed to:
    * csgo_pl_11893.txt
    * stats.txt
    * list.txt

The generated CSV files include:

    * player_basic.csv              – core player attributes
    * player_teammates.csv          – the list of current teammates
    * player_maps.csv               – per‑map statistics
    * player_rating_chart.csv       – historical rating chart data
    * player_history_honor.csv      – past tournament honours
    * player_milestones.csv         – milestone achievements
    * player_equipment.csv          – peripheral equipment list
    * player_mouse_config.csv       – detailed mouse settings
    * player_monitor_config.csv     – detailed monitor settings
    * player_stats_summary.csv      – summary of map and match statistics
    * player_performance_metrics.csv – per‑metric performance ranges
    * player_recent_matches.csv     – list of recent matches

Usage:

    python scrape_zywoo_csv_final.py

The script writes CSV files into the directory specified by ``BASE_DIR``.
If the directory does not exist it will be created automatically.  When
executed, it prints a summary of which CSV files were produced.
"""

import csv
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
import time
from typing import Any, Dict, List, Optional

import requests

# Directory to write CSV files into.  Adjust this path as needed.  If the
# directory does not exist it will be created automatically.
SCRIPT_DIR = Path(__file__).resolve().parent
BASE_DIR = SCRIPT_DIR / "cs_data"
TEAM_PLAYER_RELATION_CSV = BASE_DIR / "team_player_relation.csv"

# The ID of the player to fetch.  For this project we target ZywOo.
PLAYER_ID = "csgo_pl_11816"
AUTH_TOKEN = os.getenv("FIVEE_TOKEN", "").strip()
MAX_WORKERS = max(1, int(os.getenv("SCRAPE_PLAYER_MAX_WORKERS", "20")))
REQUEST_TIMEOUT_SECONDS = max(5, int(os.getenv("SCRAPE_PLAYER_TIMEOUT_SECONDS", "20")))
REQUEST_MAX_RETRIES = max(1, int(os.getenv("SCRAPE_PLAYER_REQUEST_RETRIES", "4")))
REQUEST_RETRY_BACKOFF_SECONDS = max(
    0.1, float(os.getenv("SCRAPE_PLAYER_RETRY_BACKOFF_SECONDS", "1.2"))
)
BUFFERED_CSV_WRITE = os.getenv("SCRAPE_PLAYER_BUFFERED_WRITE", "1").strip().lower() not in {
    "0",
    "false",
    "no",
}
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)

EQUIPMENT_COLUMNS = [
    "mouse",
    "headset",
    "monitor",
    "keyboard",
    "mousepad",
    "processor",
    "graphics_card",
    "chair",
]

EQUIPMENT_CATEGORY_ALIASES = {
    "mouse": "mouse",
    "headset": "headset",
    "headphone": "headset",
    "headphones": "headset",
    "earphone": "headset",
    "earphones": "headset",
    "monitor": "monitor",
    "keyboard": "keyboard",
    "mousepad": "mousepad",
    "mouse pad": "mousepad",
    "processor": "processor",
    "cpu": "processor",
    "graphics card": "graphics_card",
    "graphics_card": "graphics_card",
    "graphic card": "graphics_card",
    "grapphics card": "graphics_card",
    "gpu": "graphics_card",
    "chair": "chair",
}

CSV_MERGE_CACHE: Dict[Path, Dict[str, Any]] = {}
CSV_FIXED_CACHE: Dict[Path, Dict[str, Any]] = {}
EQUIPMENT_ROWS_CACHE: Optional[Dict[str, Dict[str, Any]]] = None

################################################################################
# Helper functions
################################################################################

def safe_get(d: Dict[str, Any], *keys: str, default: Any = "") -> Any:
    """Safely traverse nested dictionaries.

    Returns the value associated with the sequence of keys in the dict ``d``,
    returning ``default`` if any key is missing or if a non‑dict is encountered.

    Args:
        d: The dictionary to traverse.
        *keys: A sequence of keys to look up.
        default: A default value to return if the path is not present.

    Returns:
        The value at the nested key path or the default value.
    """
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k, default)
    return cur


def normalize_equipment_category(raw: Any) -> str:
    text = str(raw or "").strip().lower().replace("_", " ")
    text = " ".join(text.split())
    return EQUIPMENT_CATEGORY_ALIASES.get(text, "")


def write_csv(file_path: Path, rows: List[Dict[str, Any]]) -> None:
    """Write a list of dictionaries to a CSV file.

    The function automatically collects all unique field names across all
    dictionaries so that missing keys appear as empty cells in the CSV.  This
    simplifies merging heterogeneous data.

    Args:
        file_path: Location of the CSV to write.
        rows: A list of dictionaries representing rows.
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return

    cache = CSV_MERGE_CACHE.get(file_path)
    if cache is None:
        existing_rows: List[Dict[str, Any]] = []
        existing_fieldnames: List[str] = []
        if file_path.exists():
            with file_path.open("r", newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                existing_fieldnames = reader.fieldnames or []
                existing_rows = list(reader)
        cache = {
            "fieldnames": existing_fieldnames,
            "rows": existing_rows,
        }
        CSV_MERGE_CACHE[file_path] = cache

    incoming_player_ids = {
        str(row.get("player_id", "")).strip()
        for row in rows
        if row.get("player_id") is not None
    }
    incoming_player_ids.discard("")

    cached_rows: List[Dict[str, Any]] = cache.get("rows", [])
    if incoming_player_ids:
        kept_rows = [
            row
            for row in cached_rows
            if str(row.get("player_id", "")).strip() not in incoming_player_ids
        ]
    else:
        kept_rows = cached_rows
    merged_rows = kept_rows + rows

    cached_fieldnames: List[str] = cache.get("fieldnames", [])
    fieldnames: List[str] = []
    seen = set()
    for key in cached_fieldnames:
        if key not in seen:
            seen.add(key)
            fieldnames.append(key)
    for row in merged_rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)

    cache["fieldnames"] = fieldnames
    cache["rows"] = merged_rows

    if not BUFFERED_CSV_WRITE and fieldnames:
        with file_path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(merged_rows)


def write_csv_fixed_fields(
    file_path: Path, rows: List[Dict[str, Any]], fieldnames: List[str]
) -> None:
    """Write CSV with exact fieldnames, ignoring existing-file schema."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    if not rows or not fieldnames:
        return

    CSV_FIXED_CACHE[file_path] = {
        "fieldnames": list(fieldnames),
        "rows": list(rows),
    }

    if not BUFFERED_CSV_WRITE:
        with file_path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)


def flush_csv_buffers() -> None:
    """Flush all buffered CSV writes to disk once."""
    if not BUFFERED_CSV_WRITE:
        return

    for file_path, cache in CSV_MERGE_CACHE.items():
        fieldnames = cache.get("fieldnames", [])
        rows = cache.get("rows", [])
        if not fieldnames:
            continue
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with file_path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    for file_path, cache in CSV_FIXED_CACHE.items():
        fieldnames = cache.get("fieldnames", [])
        rows = cache.get("rows", [])
        if not fieldnames or not rows:
            continue
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with file_path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)


def empty_equipment_row(player_id: str) -> Dict[str, Any]:
    row: Dict[str, Any] = {"player_id": player_id}
    for col in EQUIPMENT_COLUMNS:
        row[col] = ""
        row[f"{col}_logo"] = ""
    return row


def merge_equipment_item(
    row: Dict[str, Any], category_raw: Any, name: Any, logo: Any
) -> None:
    col = normalize_equipment_category(category_raw)
    if not col:
        return
    name_text = str(name or "").strip()
    logo_text = str(logo or "").strip()
    if name_text and not str(row.get(col) or "").strip():
        row[col] = name_text
    logo_col = f"{col}_logo"
    if logo_text and not str(row.get(logo_col) or "").strip():
        row[logo_col] = logo_text


def load_existing_equipment_rows(file_path: Path) -> Dict[str, Dict[str, Any]]:
    """Load and normalize existing equipment CSV rows into wide schema."""
    rows_by_player: Dict[str, Dict[str, Any]] = {}
    if not file_path.exists():
        return rows_by_player

    with file_path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for item in reader:
            player_id = str(item.get("player_id", "")).strip()
            if not player_id:
                continue
            row = rows_by_player.setdefault(player_id, empty_equipment_row(player_id))

            # Merge wide-schema columns if they already exist.
            for col in EQUIPMENT_COLUMNS:
                val = str(item.get(col, "")).strip()
                logo_val = str(item.get(f"{col}_logo", "")).strip()
                if val and not str(row.get(col) or "").strip():
                    row[col] = val
                if logo_val and not str(row.get(f"{col}_logo") or "").strip():
                    row[f"{col}_logo"] = logo_val

            # Merge old row-schema columns (category/name/logo) if present.
            merge_equipment_item(
                row,
                item.get("category", ""),
                item.get("name", ""),
                item.get("logo", ""),
            )
    return rows_by_player


def get_equipment_rows_cache(file_path: Path) -> Dict[str, Dict[str, Any]]:
    global EQUIPMENT_ROWS_CACHE
    if EQUIPMENT_ROWS_CACHE is None:
        EQUIPMENT_ROWS_CACHE = load_existing_equipment_rows(file_path)
    return EQUIPMENT_ROWS_CACHE


def _read_json_from_text_file(fallback_path: Path) -> Dict[str, Any]:
    """Extract the JSON object from a text file containing API response.

    Some of the supplied files include a few lines of metadata (such as
    Chinese headings) followed by a JSON object.  This helper finds the
    first opening brace and attempts to parse the JSON that follows.

    Args:
        fallback_path: Path to the text file.

    Returns:
        The parsed JSON object.
    """
    with fallback_path.open("r", encoding="utf-8") as f:
        content = f.read()
    # Find the first '{' character which marks the start of the JSON payload.
    start = content.find("{")
    if start == -1:
        raise ValueError(f"No JSON object found in {fallback_path}")
    json_str = content[start:]
    return json.loads(json_str)


def build_request_headers(player_id: str, mode: str) -> Dict[str, str]:
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Origin": "https://event.5eplay.com",
        "Referer": "https://event.5eplay.com/",
        "User-Agent": USER_AGENT,
    }
    if mode in {"stats", "list"}:
        headers["Content-Type"] = "application/json;charset=UTF-8"
        headers["Sec-Ch-Ua"] = '"Chromium";v="141", "Not_A Brand";v="8"'
        headers["Sec-Ch-Ua-Mobile"] = "?0"
        headers["Sec-Ch-Ua-Platform"] = '"Windows"'
        headers["Sec-Fetch-Dest"] = "empty"
        headers["Sec-Fetch-Mode"] = "cors"
        headers["Sec-Fetch-Site"] = "cross-site"
    if mode == "player":
        headers["Referer"] = f"https://event.5eplay.com/csgo/player/{player_id}"
    return headers


def build_stats_payload(player_id: str) -> Dict[str, Any]:
    end_time = datetime.now()
    start_time = end_time - timedelta(days=90)
    time_value = (
        f"{start_time.strftime('%Y-%m-%d %H:%M:%S')}_"
        f"{end_time.strftime('%Y-%m-%d %H:%M:%S')}"
    )
    return {
        "player_options": {
            "time_value": time_value,
            "player_id": player_id,
            "time_type": "recent",
        }
    }


def build_list_payload(player_id: str, page: int = 1) -> Dict[str, Any]:
    return {
        "player_options": {
            "player_id": player_id,
            "page": page,
        }
    }


def fetch_json_with_fallback(
    url: str,
    method: str = "GET",
    payload: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    fallback_file: Optional[Path] = None,
) -> Dict[str, Any]:
    """Fetch JSON from a URL with optional fallback to a local file.

    This helper tries to fetch JSON data over HTTP.  If the request fails
    (either due to a network error or an HTTP status code outside the 200
    range), and a fallback file is provided, the function will attempt to
    parse JSON content from that file.  If no fallback is provided the
    original exception is raised.

    Args:
        url: The URL to fetch.
        method: HTTP method ("GET" or "POST").
        payload: JSON payload for POST requests.
        headers: Extra HTTP headers to send.
        fallback_file: Optional path to a text file containing sample JSON.

    Returns:
        A Python object parsed from the JSON response.

    Raises:
        requests.RequestException: If the HTTP request fails and no fallback is
            available or the fallback could not be parsed.
        ValueError: If the fallback file does not contain a valid JSON object.
    """
    # Base headers; mode-specific headers should be passed by caller.
    req_headers = {"Accept": "application/json, text/plain, */*", "User-Agent": USER_AGENT}
    if headers:
        req_headers.update(headers)
    last_error: Optional[Exception] = None
    method_upper = method.upper()

    for attempt in range(1, REQUEST_MAX_RETRIES + 1):
        try:
            if method_upper == "GET":
                resp = requests.get(
                    url, headers=req_headers, timeout=REQUEST_TIMEOUT_SECONDS
                )
            else:
                resp = requests.post(
                    url,
                    headers=req_headers,
                    json=payload,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
            resp.raise_for_status()
            json_data = resp.json()
            # Some APIs return HTTP 200 but business-level failure payloads like:
            # {"success": false, "errcode": 500, "message": "...", "data": null}
            # In this case, prefer fallback sample files when available.
            if isinstance(json_data, dict):
                success_flag = json_data.get("success")
                data_field = json_data.get("data")
                if (success_flag is False or data_field is None) and fallback_file:
                    print(f"[fallback] {url} -> {fallback_file.name}")
                    return _read_json_from_text_file(fallback_file)
            return json_data
        except Exception as exc:
            last_error = exc
            if attempt < REQUEST_MAX_RETRIES:
                sleep_seconds = REQUEST_RETRY_BACKOFF_SECONDS * attempt
                print(
                    f"[retry] {method_upper} {url} attempt={attempt}/{REQUEST_MAX_RETRIES} "
                    f"error={type(exc).__name__}, wait={sleep_seconds:.1f}s"
                )
                time.sleep(sleep_seconds)
                continue

    if fallback_file:
        print(f"[fallback] {url} -> {fallback_file.name}")
        return _read_json_from_text_file(fallback_file)
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"Request failed without specific error: {method_upper} {url}")


################################################################################
# Data fetch functions
################################################################################

def get_player_basic_info(player_id: str, fallback_file: Optional[Path] = None) -> Dict[str, Any]:
    """Fetch the player's basic information.

    Args:
        player_id: The player ID (e.g. "csgo_pl_11893").
        fallback_file: Optional local file containing sample data.

    Returns:
        The ``data`` field from the API response or an empty dict.
    """
    url = f"https://esports-data.5eplaycdn.com/v1/api/csgo/players/{player_id}"
    json_data = fetch_json_with_fallback(
        url,
        method="GET",
        headers=build_request_headers(player_id, "player"),
        fallback_file=fallback_file,
    )
    data = json_data.get("data")
    return data if isinstance(data, dict) else {}


def get_player_stats(
    player_id: str,
    fallback_file: Optional[Path] = None,
) -> Dict[str, Any]:
    """Fetch aggregated statistical data for a player.

    Args:
        player_id: Player ID to query.
        fallback_file: Optional local file containing sample data.

    Returns:
        The ``data`` field from the API response or an empty dict.
    """
    url = "https://esports-data.5eplaycdn.com/v1/api/csgo/mfilter/player/stats"
    payload = build_stats_payload(player_id)
    json_data = fetch_json_with_fallback(
        url,
        method="POST",
        payload=payload,
        headers=build_request_headers(player_id, "stats"),
        fallback_file=fallback_file,
    )
    data = json_data.get("data")
    return data if isinstance(data, dict) else {}


def get_match_list(
    player_id: str,
    page: int = 1,
    fallback_file: Optional[Path] = None,
) -> Dict[str, Any]:
    """Fetch a list of recent matches for a player.

    Args:
        player_id: The player ID to query.
        page: Page number for pagination.
        fallback_file: Optional local file containing sample data.

    Returns:
        The ``items`` field from the API response or an empty dict.
    """
    url = "https://esports-data.5eplaycdn.com/v1/api/csgo/mfilter/player/match/list"
    payload = build_list_payload(player_id, page=page)
    json_data = fetch_json_with_fallback(
        url,
        method="POST",
        payload=payload,
        headers=build_request_headers(player_id, "list"),
        fallback_file=fallback_file,
    )
    return safe_get(json_data, "data", "items", default={})


################################################################################
# CSV builders
################################################################################

def build_basic_csv(player_id: str, data: Dict[str, Any]) -> None:
    """Create CSV files based on basic_info, player_profile, and player_config.

    This function writes several CSV files derived from the player's basic
    profile.  It covers general player attributes, teammate lists, per‑map
    information, rating history, tournament honours, milestone achievements,
    peripheral equipment, and hardware settings.

    Args:
        player_id: The player ID.
        data: The dictionary returned from get_player_basic_info().
    """
    if not isinstance(data, dict):
        data = {}

    basic_info_raw = data.get("basic_info", {})
    player_data_raw = data.get("player_data", {})
    player_profile_raw = data.get("player_profile", {})
    basic_info = basic_info_raw if isinstance(basic_info_raw, dict) else {}
    player_data = player_data_raw if isinstance(player_data_raw, dict) else {}
    player_profile = player_profile_raw if isinstance(player_profile_raw, dict) else {}

    players = player_data.get("players")
    if not isinstance(players, list):
        players = []
    maps = player_data.get("maps")
    if not isinstance(maps, list):
        maps = []
    rating_chart = player_data.get("rating_chart")
    if not isinstance(rating_chart, list):
        rating_chart = []

    history_honor = player_profile.get("history_honor")
    if not isinstance(history_honor, list):
        history_honor = []
    milestones = player_profile.get("milestones")
    if not isinstance(milestones, list):
        milestones = []

    # In the newer API responses the player_config lives under player_profile
    # rather than at the top level.  Fall back to the top-level key for
    # backward‑compatibility but prefer the nested value when present.
    nested_player_config = player_profile.get("player_config")
    top_level_player_config = data.get("player_config", {})
    if isinstance(nested_player_config, dict):
        player_config = nested_player_config
    elif isinstance(top_level_player_config, dict):
        player_config = top_level_player_config
    else:
        player_config = {}

    peripheral_equipment = player_config.get("peripheral_equipment")
    if not isinstance(peripheral_equipment, list):
        peripheral_equipment = []
    mouse_config = player_config.get("mouse_config")
    if not isinstance(mouse_config, dict):
        mouse_config = {}
    monitor_config = player_config.get("monitor_config")
    if not isinstance(monitor_config, dict):
        monitor_config = {}

    positions = basic_info.get("positions", [])
    if isinstance(positions, list):
        positions_text = "|".join(str(v) for v in positions if v is not None)
    else:
        positions_text = ""

    # Build player_basic.csv
    player_row = {
        "player_id": player_id,
        "name": basic_info.get("name", ""),
        "birthday": basic_info.get("birthday", ""),
        "country_zh": basic_info.get("country_zh", ""),
        "country_en": basic_info.get("country_en", ""),
        "team_id": basic_info.get("team_id", ""),
        "team_name": basic_info.get("team_name", ""),
        "bonus": basic_info.get("bonus", ""),
        "position": basic_info.get("position", ""),
        "positions": positions_text,
        "portrait": basic_info.get("portrait", ""),
        "half_portrait": basic_info.get("half_portrait", ""),
        "team_logo": basic_info.get("team_logo", ""),
        "country_logo": basic_info.get("country_logo", ""),
        "top20_num": basic_info.get("top20_num", ""),
        "maps_played": player_data.get("maps_played", ""),
        "rounds_played": player_data.get("rounds_played", ""),
        "kills": player_data.get("kill", ""),
        "deaths": player_data.get("death", ""),
        "rating": player_data.get("rating", ""),
        "kd": player_data.get("kd", ""),
        "adr": player_data.get("adr", ""),
        "kpr": player_data.get("kpr", ""),
        "dpr": player_data.get("dpr", ""),
        "kast": player_data.get("kast", ""),
        "head_shot": player_data.get("head_shot", ""),
        "impact": player_data.get("impact", ""),
        # Monitor and mouse config flattened into basic info for convenience
        "mouse_name": safe_get(player_config, "mouse_config", "mouse_name"),
        "dpi": safe_get(player_config, "mouse_config", "dpi"),
        "e_dpi": safe_get(player_config, "mouse_config", "e_dpi"),
        "sensitivity": safe_get(player_config, "mouse_config", "sensitivity"),
        "windows_sensitivity": safe_get(player_config, "mouse_config", "windows_sensitivity"),
        "zoom_sensitivity": safe_get(player_config, "mouse_config", "zoom_sensitivity"),
        "mouse_acceleration": safe_get(player_config, "mouse_config", "mouse_acceleration"),
        "raw_input": safe_get(player_config, "mouse_config", "raw_input"),
        "hz": safe_get(player_config, "mouse_config", "hz"),
        "resolution": safe_get(player_config, "monitor_config", "resolution"),
        "aspect_ratio": safe_get(player_config, "monitor_config", "aspect_ratio"),
        "color_mode": safe_get(player_config, "monitor_config", "color_mode"),
        "scaling_mode": safe_get(player_config, "monitor_config", "scaling_mode"),
        # Summary counts
        "milestone_count": len(milestones),
        "history_honor_count": len(history_honor),
    }
    write_csv(BASE_DIR / "player_basic.csv", [player_row])

    # Build teammates CSV
    teammates_rows = []
    for mate in players:
        if not isinstance(mate, dict):
            continue
        teammates_rows.append({
            "player_id": player_id,
            "teammate_id": mate.get("id", ""),
            "teammate_name": mate.get("name", ""),
            "birthday": mate.get("birthday", ""),
            "country_logo": mate.get("country_logo", ""),
            "portrait": mate.get("portrait", ""),
            "half_portrait": mate.get("half_portrait", ""),
            "rating": mate.get("rating", ""),
        })
    write_csv(BASE_DIR / "player_teammates.csv", teammates_rows)

    # Build maps CSV
    maps_rows = []
    for item in maps:
        if not isinstance(item, dict):
            continue
        maps_rows.append({
            "player_id": player_id,
            "map_name": item.get("name", ""),
            "map_kd": item.get("kd", ""),
            "map_rating": item.get("rating", ""),
            "use_num": item.get("use_num", ""),
        })
    write_csv(BASE_DIR / "player_maps.csv", maps_rows)

    # Build rating chart CSV
    rating_rows = []
    for item in rating_chart:
        if not isinstance(item, dict):
            continue
        rating_rows.append({
            "player_id": player_id,
            "date": item.get("date", ""),
            "rate": item.get("rate", ""),
        })
    write_csv(BASE_DIR / "player_rating_chart.csv", rating_rows)

    # Build history honour CSV
    honor_rows = []
    for item in history_honor:
        if not isinstance(item, dict):
            continue
        tt = item.get("history_tt", {})
        if not isinstance(tt, dict):
            tt = {}
        honor_rows.append({
            "player_id": player_id,
            "tt_id": tt.get("id", ""),
            "tt_name": tt.get("name", ""),
            "start_time": tt.get("start_time", ""),
            "bonus": tt.get("bonus", ""),
            "grade": tt.get("grade", ""),
            "team_name": tt.get("team_name", ""),
            "rank": item.get("rank", ""),
            "rank_desc": item.get("rank_desc", ""),
            "team_ranking": item.get("team_ranking", ""),
        })
    write_csv(BASE_DIR / "player_history_honor.csv", honor_rows)

    # Build milestones CSV
    milestone_rows = []
    for item in milestones:
        if not isinstance(item, dict):
            continue
        tt_info = item.get("tt_info", {})
        if not isinstance(tt_info, dict):
            tt_info = {}
        team_info = item.get("team", {})
        if not isinstance(team_info, dict):
            team_info = {}
        milestone_rows.append({
            "player_id": player_id,
            "milestone_id": item.get("id", ""),
            "achieve_time": item.get("achieve_time", ""),
            "created_at": item.get("created_at", ""),
            "honor_text": item.get("honor_text", ""),
            "detail": item.get("detail", ""),
            "dimension": item.get("dimension", ""),
            "dimension_text": item.get("dimension_text", ""),
            "values": item.get("values", ""),
            "match_id": item.get("match_id", ""),
            "tt_id": tt_info.get("tt_id", ""),
            "tt_name": tt_info.get("tt_name", ""),
            "team_id": team_info.get("team_id", ""),
            "team_name": team_info.get("team_name", ""),
        })
    write_csv(BASE_DIR / "player_milestones.csv", milestone_rows)

    # Build peripheral equipment CSV as one row per player (wide format).
    equipment_row = empty_equipment_row(player_id)

    for item in peripheral_equipment:
        if not isinstance(item, dict):
            continue
        merge_equipment_item(
            equipment_row,
            item.get("category", ""),
            item.get("name", ""),
            item.get("logo", ""),
        )

    equipment_file = BASE_DIR / "player_equipment.csv"
    rows_by_player = get_equipment_rows_cache(equipment_file)
    rows_by_player[player_id] = equipment_row
    output_rows = [rows_by_player[k] for k in sorted(rows_by_player.keys())]
    output_fields = ["player_id"] + [
        field
        for col in EQUIPMENT_COLUMNS
        for field in (col, f"{col}_logo")
    ]
    write_csv_fixed_fields(equipment_file, output_rows, output_fields)

    # Build mouse config CSV (single row)
    write_csv(
        BASE_DIR / "player_mouse_config.csv",
        [
            {
                "player_id": player_id,
                **mouse_config,
            }
        ],
    )

    # Build monitor config CSV (single row)
    write_csv(
        BASE_DIR / "player_monitor_config.csv",
        [
            {
                "player_id": player_id,
                **monitor_config,
            }
        ],
    )


def build_stats_csv(player_id: str, stats: Dict[str, Any]) -> None:
    """Create CSV files based on aggregated player statistics.

    Args:
        player_id: The player ID.
        stats: Dictionary returned from get_player_stats().
    """
    if not isinstance(stats, dict):
        stats = {}

    map_stats = stats.get("map_stats", {})
    match_stats = stats.get("match_stats", {})
    player_data = stats.get("player_data", {})
    performance = stats.get("player_performance", {})
    if not isinstance(map_stats, dict):
        map_stats = {}
    if not isinstance(match_stats, dict):
        match_stats = {}
    if not isinstance(player_data, dict):
        player_data = {}
    if not isinstance(performance, dict):
        performance = {}

    # Build summary CSV combining map and match statistics along with overall metrics
    summary_row = {
        "player_id": player_id,
        "map_total": map_stats.get("total", ""),
        "map_win": map_stats.get("win", ""),
        "map_loss": map_stats.get("loss", ""),
        "map_draw": map_stats.get("draw", ""),
        "map_mvp_count": map_stats.get("mvp_count", ""),
        "map_win_rate": map_stats.get("win_rate", ""),
        "match_total": match_stats.get("total", ""),
        "match_win": match_stats.get("win", ""),
        "match_loss": match_stats.get("loss", ""),
        "match_draw": match_stats.get("draw", ""),
        "match_mvp_count": match_stats.get("mvp_count", ""),
        "match_win_rate": match_stats.get("win_rate", ""),
        **player_data,
        # Flatten key statistics for convenience
        "adr_value": safe_get(performance, "adr", "value"),
        "adr_avg": safe_get(performance, "adr", "avg_value"),
        "dpr_value": safe_get(performance, "dpr", "value"),
        "dpr_avg": safe_get(performance, "dpr", "avg_value"),
        "kast_value": safe_get(performance, "kast", "value"),
        "kast_avg": safe_get(performance, "kast", "avg_value"),
        "kpr_value": safe_get(performance, "kpr", "value"),
        "kpr_avg": safe_get(performance, "kpr", "avg_value"),
        "rating_value": safe_get(performance, "rating", "value"),
        "rating_avg": safe_get(performance, "rating", "avg_value"),
        "swing_value": safe_get(performance, "swing", "value"),
        "swing_avg": safe_get(performance, "swing", "avg_value"),
    }
    write_csv(BASE_DIR / "player_stats_summary.csv", [summary_row])

    # Build performance metrics CSV; each metric is flattened into a row
    performance_rows = []
    for metric_name, metric_data in performance.items():
        if not isinstance(metric_data, dict):
            metric_data = {}
        performance_rows.append({
            "player_id": player_id,
            "metric": metric_name,
            "value": metric_data.get("value", ""),
            "avg_value": metric_data.get("avg_value", ""),
            "lower_better": metric_data.get("lower_better", ""),
            "bad_start": safe_get(metric_data, "range", "bad", "start"),
            "bad_end": safe_get(metric_data, "range", "bad", "end"),
            "middle_start": safe_get(metric_data, "range", "middle", "start"),
            "middle_end": safe_get(metric_data, "range", "middle", "end"),
            "good_start": safe_get(metric_data, "range", "good", "start"),
            "good_end": safe_get(metric_data, "range", "good", "end"),
        })
    write_csv(BASE_DIR / "player_performance_metrics.csv", performance_rows)


def build_matches_csv(player_id: str, items: Dict[str, Any]) -> None:
    """Create CSV file based on the list of recent matches.

    Args:
        player_id: The player ID.
        items: A dictionary containing an array of matches under the key "matches".
    """
    if not isinstance(items, dict):
        items = {}

    matches = items.get("matches", [])
    if not isinstance(matches, list):
        matches = []

    match_rows = []
    for item in matches:
        if not isinstance(item, dict):
            continue
        mc = item.get("mc_info", {})
        if not isinstance(mc, dict):
            mc = {}
        tt = item.get("tt_info", {})
        if not isinstance(tt, dict):
            tt = {}
        opponent = mc.get("opponent_info", {})
        if not isinstance(opponent, dict):
            opponent = {}
        home = mc.get("home_info", {})
        if not isinstance(home, dict):
            home = {}
        match_rows.append({
            "player_id": player_id,
            "match_id": mc.get("id", ""),
            "format": mc.get("format", ""),
            "match_status": mc.get("match_status", ""),
            "status": mc.get("status", ""),
            "result": mc.get("result", ""),
            "home_team_id": home.get("id", ""),
            "home_team_name": home.get("disp_name", ""),
            "opponent_team_id": opponent.get("id", ""),
            "opponent_team_name": opponent.get("disp_name", ""),
            "home_score": mc.get("home_score", ""),
            "opponent_score": mc.get("opponent_score", ""),
            "home_quick_score": mc.get("home_quick_score", ""),
            "opponent_quick_score": mc.get("opponent_quick_score", ""),
            "ts": mc.get("ts", ""),
            "tt_stage": mc.get("tt_stage", ""),
            "tt_stage_desc": mc.get("tt_stage_desc", ""),
            "t1_odds": mc.get("t1_odds", ""),
            "t2_odds": mc.get("t2_odds", ""),
            "t1_odds_percent": mc.get("t1_odds_percent", ""),
            "t2_odds_percent": mc.get("t2_odds_percent", ""),
            "tournament_id": tt.get("id", ""),
            "tournament_name": tt.get("disp_name", ""),
            "tournament_start_time": tt.get("start_time", ""),
            "tournament_end_time": tt.get("end_time", ""),
            "tournament_bonus": tt.get("bonus", ""),
            "tournament_grade": tt.get("grade", ""),
            "tournament_grade_label": tt.get("grade_label", ""),
            "city_name": tt.get("city_name", ""),
        })
    write_csv(BASE_DIR / "player_recent_matches.csv", match_rows)


################################################################################
# Main routine
################################################################################

def find_fallback_file(candidates: List[str], script_dir: Path) -> Optional[Path]:
    """Find the first existing fallback file from a list of candidates.

    This helper checks for each candidate filename in ``script_dir`` and then
    in the current working directory.  It returns the first path that exists
    or ``None`` if none are found.

    Args:
        candidates: A list of filename strings to search for.
        script_dir: The directory where the script resides.

    Returns:
        A Path to the first existing file or None.
    """
    for fname in candidates:
        path = script_dir / fname
        if path.exists():
            return path
        cwd_path = Path.cwd() / fname
        if cwd_path.exists():
            return cwd_path
    return None


def load_player_ids_from_relation(csv_path: Path) -> List[str]:
    """Load unique player IDs from team_player_relation CSV."""
    if not csv_path.exists():
        return []

    player_ids: List[str] = []
    seen = set()
    with csv_path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            player_id = str(row.get("player_id", "")).strip()
            if not player_id or player_id in seen:
                continue
            seen.add(player_id)
            player_ids.append(player_id)
    return player_ids


def fetch_player_payload(
    player_id: str,
    basic_fallback: Optional[Path],
    stats_fallback: Optional[Path],
    matches_fallback: Optional[Path],
) -> Dict[str, Any]:
    """Fetch all API payloads required for one player."""
    basic_data: Dict[str, Any] = {}
    stats_data: Dict[str, Any] = {}
    match_items: Dict[str, Any] = {}

    try:
        basic_data = get_player_basic_info(player_id, fallback_file=basic_fallback)
    except Exception as exc:
        print(f"[warn] {player_id} basic_info 抓取失败：{exc}")

    try:
        stats_data = get_player_stats(
            player_id, fallback_file=stats_fallback
        )
    except Exception as exc:
        print(f"[warn] {player_id} player_stats 抓取失败：{exc}")

    try:
        match_items = get_match_list(
            player_id, page=1, fallback_file=matches_fallback
        )
    except Exception as exc:
        print(f"[warn] {player_id} match_list 抓取失败：{exc}")

    return {
        "basic_data": basic_data,
        "stats_data": stats_data,
        "match_items": match_items,
    }


def fetch_players_concurrently(
    player_ids: List[str],
    basic_fallback: Optional[Path],
    stats_fallback: Optional[Path],
    matches_fallback: Optional[Path],
) -> Dict[str, Dict[str, Any]]:
    """Fetch multiple players concurrently, then return data by player_id."""
    if not player_ids:
        return {}

    worker_count = min(MAX_WORKERS, len(player_ids))
    print(f"[信息] 并发抓取已开启：workers={worker_count}")

    results: Dict[str, Dict[str, Any]] = {}
    failed_players: List[str] = []
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_map = {
            executor.submit(
                fetch_player_payload,
                player_id,
                basic_fallback,
                stats_fallback,
                matches_fallback,
            ): player_id
            for player_id in player_ids
        }

        done = 0
        total = len(player_ids)
        for future in as_completed(future_map):
            player_id = future_map[future]
            done += 1
            try:
                results[player_id] = future.result()
                print(f"[抓取进度] {done}/{total} 完成：{player_id}")
            except Exception as exc:
                failed_players.append(player_id)
                results[player_id] = {
                    "basic_data": {},
                    "stats_data": {},
                    "match_items": {},
                }
                print(f"[warn] 抓取失败 {player_id}: {exc}")

    if failed_players:
        print(
            f"[warn] 抓取阶段有 {len(failed_players)} 个选手异常，已跳过并继续。"
        )

    return results


def main() -> None:
    """Entry point for the script.

    Attempts to fetch the player's data and writes CSV files.  If online
    requests fail (or returns business-level failure payloads), the script
    falls back to reading fixed local sample files.
    """
    print("开始抓取选手数据 …")

    # Ensure the base directory exists before writing files
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    if not AUTH_TOKEN:
        print("[提示] 未设置 FIVEE_TOKEN，stats/match/list 在线接口可能返回 unknown request error。")

    player_ids = load_player_ids_from_relation(TEAM_PLAYER_RELATION_CSV)
    if player_ids:
        print(
            f"[信息] 从 {TEAM_PLAYER_RELATION_CSV} 读取到 {len(player_ids)} 个唯一 player_id，"
            "将按此列表批量抓取。"
        )
    else:
        player_ids = [PLAYER_ID]
        print(f"[信息] 未读取到 team_player_relation.csv 中的 player_id，回退到默认选手 {PLAYER_ID}。")

    # Determine fallback file paths relative to this script or current working directory.
    script_dir = SCRIPT_DIR

    # Fallback files use fixed names in backend/counter_strike/.
    basic_candidates = ["csgo_pl_11893.txt"]
    stats_candidates = ["stats.txt"]
    list_candidates = ["list.txt"]

    basic_fallback = find_fallback_file(basic_candidates, script_dir)
    stats_fallback = find_fallback_file(stats_candidates, script_dir)
    matches_fallback = find_fallback_file(list_candidates, script_dir)

    # Sample fallback files are only meaningful for the single default player flow.
    use_fallback = len(player_ids) == 1 and player_ids[0] == PLAYER_ID
    fallback_basic_for_run = basic_fallback if use_fallback else None
    fallback_stats_for_run = stats_fallback if use_fallback else None
    fallback_matches_for_run = matches_fallback if use_fallback else None

    payloads_by_player = fetch_players_concurrently(
        player_ids,
        fallback_basic_for_run,
        fallback_stats_for_run,
        fallback_matches_for_run,
    )

    # Write CSVs sequentially to avoid concurrent file write conflicts.
    skipped_players = 0
    for idx, player_id in enumerate(player_ids, start=1):
        payload = payloads_by_player[player_id]
        if not any(
            [
                payload.get("basic_data"),
                payload.get("stats_data"),
                payload.get("match_items"),
            ]
        ):
            skipped_players += 1
            print(f"[warn] {player_id} 全部接口失败，跳过写入。")
            continue
        print(f"[写入进度] {idx}/{len(player_ids)} 写入：{player_id}")
        build_basic_csv(player_id, payload.get("basic_data", {}))
        build_stats_csv(player_id, payload.get("stats_data", {}))
        build_matches_csv(player_id, payload.get("match_items", {}))

    flush_csv_buffers()

    # Print the list of generated CSV files for user feedback
    generated_files = [
        "player_basic.csv",
        "player_teammates.csv",
        "player_maps.csv",
        "player_rating_chart.csv",
        "player_history_honor.csv",
        "player_milestones.csv",
        "player_equipment.csv",
        "player_mouse_config.csv",
        "player_monitor_config.csv",
        "player_stats_summary.csv",
        "player_performance_metrics.csv",
        "player_recent_matches.csv",
    ]
    print(f"CSV 文件已生成到目录：{BASE_DIR}")
    for name in generated_files:
        print(f" - {name}")
    if skipped_players:
        print(f"[warn] 本次有 {skipped_players} 个选手因请求全部失败未写入。")


if __name__ == "__main__":
    main()
