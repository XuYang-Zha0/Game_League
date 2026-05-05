"""
team_rank_snapshot.py
---------------------

Crawl team ranking snapshots from 5EPlay API.

Key behavior:
- Crawl up to max_pages (default 100).
- Stop early when an empty page is encountered.
- Fetch remaining pages concurrently to speed up crawling.
"""

from __future__ import annotations

import copy
import csv
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

TEAM_LIST_URL = "https://esports-data.5eplaycdn.com/v1/api/csgo/new/rank/team_list"
LOCAL_DEBUG_JSON = os.environ.get("CSGO_TEAM_LIST_DEBUG_FILE", "")
DEFAULT_MAX_PAGES = int(os.getenv("CS_TEAM_MAX_PAGES", "100"))
DEFAULT_MAX_WORKERS = max(1, int(os.getenv("CS_TEAM_MAX_WORKERS", "10")))

TEAM_LIST_PAYLOAD: Dict[str, Any] = {
    "sort_value": "desc",
    "rank_type": "rank",
    "sort_key": "rank",
    "region": "全部赛区",
    "team_options": {
        "tt_ids": [],
        "time_value": "",
        "grade": [],
        "team_id": "",
        "time_type": "",
        "tt_series": [],
        "maps": [],
        "page": 1,
    },
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


def fetch_team_list_page(page: int = 1) -> Dict[str, Any]:
    if LOCAL_DEBUG_JSON:
        return _load_local_debug_json(LOCAL_DEBUG_JSON)

    payload = copy.deepcopy(TEAM_LIST_PAYLOAD)
    payload["team_options"]["page"] = page

    print(f"[team_rank_snapshot] POST page={page}")
    resp = SESSION.post(
        TEAM_LIST_URL,
        json=payload,
        headers=COMMON_HEADERS,
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def parse_team_rank_rows(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []

    for item in items:
        fv = item.get("field_values", {}) or {}
        row = {
            "team_id": item.get("team_id", ""),
            "team_name": item.get("team_name", ""),
            "team_logo": item.get("team_logo", ""),
            "country_logo": item.get("country_logo", ""),
            "global_rank": fv.get("global_rank") or fv.get("rank") or "",
            "valve_rank": fv.get("valve_rank", ""),
            "valve_point": fv.get("valve_point", ""),
            "score": fv.get("score", ""),
            "point": fv.get("point", ""),
            "rank_change": fv.get("rank_change", ""),
            "rank_diff": fv.get("rank_diff", ""),
            "crawl_time": int(time.time()),
        }
        rows.append(row)

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


def crawl_team_rank_snapshot(
    max_pages: Optional[int] = DEFAULT_MAX_PAGES,
    output_file: str = "team_rank_snapshot.csv",
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> None:
    max_pages = max_pages or DEFAULT_MAX_PAGES
    max_workers = max(1, min(max_workers, max_pages))

    all_rows: List[Dict[str, Any]] = []
    print(f"[team_rank_snapshot] max_pages={max_pages}, max_workers={max_workers}")

    print("[team_rank_snapshot] Crawling page 1...")
    first_data = fetch_team_list_page(page=1)
    first_items = _extract_items(first_data)

    if not first_items:
        print("[team_rank_snapshot] page=1 no data, stop.")
    else:
        all_rows.extend(parse_team_rank_rows(first_items))
        print(
            "[team_rank_snapshot] Page 1 completed, "
            f"teams_on_page={len(first_items)}, total_rows={len(all_rows)}"
        )

        target_pages = _extract_total_page(first_data, max_pages)
        print(f"[team_rank_snapshot] target_pages={target_pages}")

        if not LOCAL_DEBUG_JSON and target_pages >= 2:
            pages = list(range(2, target_pages + 1))
            page_items_map: Dict[int, List[Dict[str, Any]]] = {}

            with ThreadPoolExecutor(max_workers=min(max_workers, len(pages))) as executor:
                futures = {executor.submit(fetch_team_list_page, p): p for p in pages}
                for future in as_completed(futures):
                    page = futures[future]
                    try:
                        data = future.result()
                    except Exception as exc:
                        print(f"[team_rank_snapshot] page={page} failed, skip: {exc}")
                        continue
                    page_items_map[page] = _extract_items(data)

            for page in range(2, target_pages + 1):
                if page not in page_items_map:
                    continue
                items = page_items_map.get(page, [])
                if not items:
                    print(f"[team_rank_snapshot] page={page} no data, stop.")
                    break

                all_rows.extend(parse_team_rank_rows(items))
                print(
                    f"[team_rank_snapshot] Page {page} completed, "
                    f"teams_on_page={len(items)}, total_rows={len(all_rows)}"
                )

    with open(output_file, "w", newline="", encoding="utf-8-sig") as f:
        fieldnames = [
            "team_id",
            "team_name",
            "team_logo",
            "country_logo",
            "global_rank",
            "valve_rank",
            "valve_point",
            "score",
            "point",
            "rank_change",
            "rank_diff",
            "crawl_time",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"Collected {len(all_rows)} ranking snapshots into {output_file}")
    if not all_rows:
        raise RuntimeError("[team_rank_snapshot] no ranking data crawled.")


if __name__ == "__main__":
    crawl_team_rank_snapshot(
        max_pages=DEFAULT_MAX_PAGES,
        output_file="./cs_data/team_rank_snapshot.csv",
    )
