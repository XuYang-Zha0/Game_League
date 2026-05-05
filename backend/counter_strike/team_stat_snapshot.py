"""
team_stat_snapshot.py
--------------------

Crawl team statistics snapshots from 5EPlay API.

Key behavior:
- Crawl up to max_pages (default 100).
- Stop early when an empty page is encountered.
- Fetch remaining pages concurrently to speed up crawling.
- Keep CSV schema aligned with downstream MySQL import fields.
"""

from __future__ import annotations

import copy
import csv
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

LIST_URL = "https://esports-data.5eplaycdn.com/v1/api/csgo/mfilter/team/list"
LOCAL_DEBUG_JSON = os.environ.get("CSGO_LIST_DEBUG_FILE", "")
DEFAULT_MAX_PAGES = int(os.getenv("CS_TEAM_MAX_PAGES", "100"))
DEFAULT_MAX_WORKERS = max(1, int(os.getenv("CS_TEAM_MAX_WORKERS", "20")))
DEFAULT_STAT_START_TIME = os.getenv("CS_TEAM_STAT_START_TIME", "2023-01-01 00:00:00").strip()
DEFAULT_STAT_END_TIME = os.getenv("CS_TEAM_STAT_END_TIME", "").strip()
DEFAULT_STAT_GRADES = os.getenv("CS_TEAM_STAT_GRADES", "").strip()

BASE_LIST_PAYLOAD: Dict[str, Any] = {
    "sort_key": "rating",
    "sort_value": "DESC",
}

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_PATH = BASE_DIR / "cs_data" / "team_stat_snapshot.csv"

OUTPUT_FIELDS: List[str] = [
    "id",
    "team_id",
    "team_name",
    "team_logo",
    "region_name",
    "rating",
    "map_num",
    "map_win_loss",
    "map_win_rate",
    "game_played",
    "win_rate",
    "avg_round",
    "kd_rate",
    "kd",
    "kd_diff",
    "avg_kill",
    "avg_death",
    "avg_assist",
    "total_kill",
    "total_death",
    "total_assist",
    "total_round",
    "first_five_win_num",
    "first_five_win_rate",
    "first_ten_win_num",
    "first_ten_win_rate",
    "ct_win_round",
    "ct_win_rate",
    "t_win_round",
    "t_win_rate",
    "ct_first_win_round",
    "ct_first_win_rate",
    "t_first_win_round",
    "t_first_win_rate",
    "first_kill",
    "first_kill_rate",
    "first_death_num",
    "first_death_rate",
    "global_rank",
    "valve_rank",
    "valve_point",
    "point",
    "score",
    "global_bonus",
    "rank",
    "rank_change",
    "rank_diff",
    "csv_index",
    "crawl_time",
]

ITEM_TOP_LEVEL_MAP: Dict[str, str] = {
    "team_id": "team_id",
    "team_name": "team_name",
    "team_logo": "team_logo",
}

FIELD_VALUES_MAP: Dict[str, str] = {
    "region_name": "region_name",
    "rating": "rating",
    "map_num": "map_num",
    "map_win_loss": "map_win_loss",
    "map_win_rate": "map_win_rate",
    "game_played": "game_played",
    "win_rate": "win_rate",
    "avg_round": "avg_round",
    "kd_rate": "kd_rate",
    "kd": "kd",
    "kd_diff": "kd_diff",
    "avg_kill": "avg_kill",
    "avg_death": "avg_death",
    "avg_assist": "avg_assist",
    "total_kill": "total_kill",
    "total_death": "total_death",
    "total_assist": "total_assist",
    "total_round": "total_round",
    "first_five_win_num": "first_five_win_num",
    "first_five_win_rate": "first_five_win_rate",
    "first_ten_win_num": "first_ten_win_num",
    "first_ten_win_rate": "first_ten_win_rate",
    "ct_win_round": "ct_win_round",
    "ct_win_rate": "ct_win_rate",
    "t_win_round": "t_win_round",
    "t_win_rate": "t_win_rate",
    "ct_first_win_round": "ct_first_win_round",
    "ct_first_win_rate": "ct_first_win_rate",
    "t_first_win_round": "t_first_win_round",
    "t_first_win_rate": "t_first_win_rate",
    "first_kill": "first_kill",
    "first_kill_rate": "first_kill_rate",
    "first_death_num": "first_death_num",
    "first_death_rate": "first_death_rate",
    "global_rank": "global_rank",
    "valve_rank": "valve_rank",
    "valve_point": "valve_point",
    "point": "point",
    "score": "score",
    "global_bonus": "global_bonus",
    "rank": "rank",
    "rank_change": "rank_change",
    "rank_diff": "rank_diff",
}


def build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["POST"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


SESSION = build_session()

COMMON_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/141.0.0.0 Safari/537.36"
    ),
    "Referer": "https://event.5eplay.com/csgo/teams",
    "Origin": "https://event.5eplay.com",
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
}


def _load_local_debug_json(filepath: str) -> Dict[str, Any]:
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def _resolve_stat_end_time() -> str:
    if DEFAULT_STAT_END_TIME:
        return DEFAULT_STAT_END_TIME
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _parse_grade_filters(raw: str) -> List[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    return [x.strip() for x in text.split(",") if x.strip()]


def build_list_payload(page: int) -> Dict[str, Any]:
    start_time = DEFAULT_STAT_START_TIME
    end_time = _resolve_stat_end_time()
    use_time_filter = bool(start_time and end_time)

    payload = copy.deepcopy(BASE_LIST_PAYLOAD)
    payload["team_options"] = {
        "tt_ids": [],
        "time_value": f"{start_time}_{end_time}" if use_time_filter else "",
        "grade": _parse_grade_filters(DEFAULT_STAT_GRADES),
        "team_id": "",
        "time_type": "self" if use_time_filter else "",
        "tt_series": [],
        "maps": [],
        "page": page,
    }
    return payload


def resolve_output_path(output_file: Optional[str]) -> Path:
    if not output_file:
        path = DEFAULT_OUTPUT_PATH
    else:
        raw = Path(output_file)
        path = raw if raw.is_absolute() else BASE_DIR / raw

    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def fetch_list_page(page: int = 1) -> Dict[str, Any]:
    if LOCAL_DEBUG_JSON:
        return _load_local_debug_json(LOCAL_DEBUG_JSON)

    payload = build_list_payload(page)

    print(f"[team_stat_snapshot] POST page={page}")
    resp = SESSION.post(
        LIST_URL,
        json=payload,
        headers=COMMON_HEADERS,
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def build_row(item: Dict[str, Any], row_index: int) -> Dict[str, Any]:
    fv = item.get("field_values", {}) or {}
    now_ts = int(time.time())

    row: Dict[str, Any] = {field: "" for field in OUTPUT_FIELDS}
    row["id"] = ""
    row["csv_index"] = row_index
    row["crawl_time"] = now_ts

    for out_key, src_key in ITEM_TOP_LEVEL_MAP.items():
        row[out_key] = item.get(src_key, "")

    for out_key, src_key in FIELD_VALUES_MAP.items():
        row[out_key] = fv.get(src_key, "")

    return row


def parse_team_stat_rows(items: List[Dict[str, Any]], start_index: int) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []

    for offset, item in enumerate(items, start=1):
        row_index = start_index + offset
        rows.append(build_row(item, row_index))

    return rows


def _extract_items(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    data_block = data.get("data", {}) if isinstance(data, dict) else {}
    items = data_block.get("items", [])
    return items if isinstance(items, list) else []


def _extract_total_page(data: Dict[str, Any], fallback: int) -> int:
    data_block = data.get("data", {}) if isinstance(data, dict) else {}
    raw_total_page = data_block.get("total_page")
    try:
        return max(1, min(int(raw_total_page), fallback))
    except (TypeError, ValueError):
        return fallback


def crawl_team_stat_snapshot(
    max_pages: Optional[int] = DEFAULT_MAX_PAGES,
    output_file: str = "team_stat_snapshot.csv",
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> None:
    max_pages = max_pages or DEFAULT_MAX_PAGES
    max_workers = max(1, min(max_workers, max_pages))

    all_rows: List[Dict[str, Any]] = []
    print(f"[team_stat_snapshot] max_pages={max_pages}, max_workers={max_workers}")
    print(
        "[team_stat_snapshot] filters: "
        f"time={DEFAULT_STAT_START_TIME}_{_resolve_stat_end_time()}, "
        f"grades={_parse_grade_filters(DEFAULT_STAT_GRADES) or 'ALL'}"
    )

    print("[team_stat_snapshot] Crawling page 1...")
    first_data = fetch_list_page(page=1)
    first_items = _extract_items(first_data)

    if not first_items:
        print("[team_stat_snapshot] page=1 no data, stop.")
    else:
        first_rows = parse_team_stat_rows(first_items, start_index=len(all_rows))
        all_rows.extend(first_rows)
        print(
            "[team_stat_snapshot] Page 1 completed, "
            f"teams_on_page={len(first_items)}, total_rows={len(all_rows)}"
        )

        reported_total_pages = _extract_total_page(first_data, max_pages)
        print(
            "[team_stat_snapshot] "
            f"reported_total_pages={reported_total_pages}, "
            f"crawl_cap_pages={max_pages} (stop when page is empty)"
        )

        if not LOCAL_DEBUG_JSON and max_pages >= 2:
            pages = list(range(2, max_pages + 1))
            page_items_map: Dict[int, List[Dict[str, Any]]] = {}

            with ThreadPoolExecutor(max_workers=min(max_workers, len(pages))) as executor:
                futures = {executor.submit(fetch_list_page, p): p for p in pages}
                for future in as_completed(futures):
                    page = futures[future]
                    try:
                        data = future.result()
                    except Exception as exc:
                        print(f"[team_stat_snapshot] page={page} failed, skip: {exc}")
                        continue
                    page_items_map[page] = _extract_items(data)

            for page in range(2, max_pages + 1):
                if page not in page_items_map:
                    continue
                items = page_items_map.get(page, [])
                if not items:
                    print(f"[team_stat_snapshot] page={page} no data, stop.")
                    break
                page_rows = parse_team_stat_rows(items, start_index=len(all_rows))
                all_rows.extend(page_rows)
                print(
                    f"[team_stat_snapshot] Page {page} completed, "
                    f"teams_on_page={len(items)}, total_rows={len(all_rows)}"
                )

    output_path = resolve_output_path(output_file)
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"Collected statistics for {len(all_rows)} teams into {output_path}")
    if not all_rows:
        raise RuntimeError("[team_stat_snapshot] no stat data crawled.")


if __name__ == "__main__":
    crawl_team_stat_snapshot(
        max_pages=DEFAULT_MAX_PAGES,
        output_file="./cs_data/team_stat_snapshot.csv",
    )
