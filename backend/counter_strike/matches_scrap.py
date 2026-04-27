from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

URL = "https://app.5eplay.com/api/tournament/session_list"
BASE_DIR = Path(__file__).resolve().parent
OUTPUT_FILE = BASE_DIR / "cs_data" / "cs2_matches_5eplay.csv"
PAGE_SIZE = 20
DEFAULT_MAX_PAGES = int(os.getenv("CS_MATCH_MAX_PAGES", "100"))
DEFAULT_MAX_WORKERS = max(1, int(os.getenv("CS_MATCH_MAX_WORKERS", "10")))
DEFAULT_CUTOFF_TEXT = os.getenv("CS_MATCH_CUTOFF", "2023-01-01 00:00:00")

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://event.5eplay.com/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/141.0.0.0 Safari/537.36"
    ),
}


def build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


SESSION = build_session()


def parse_cutoff(text: str) -> datetime:
    value = str(text or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(value, fmt)
            if fmt == "%Y-%m-%d":
                return dt.replace(hour=0, minute=0, second=0, microsecond=0)
            return dt
        except ValueError:
            continue
    return datetime(2023, 1, 1, 0, 0, 0)


CUTOFF_DT = parse_cutoff(DEFAULT_CUTOFF_TEXT)


def format_time(ts: Any) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)


def parse_plan_time(ts: Any) -> Optional[datetime]:
    if ts in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(ts))
    except Exception:
        return None


def fetch_page(page: int) -> Optional[Dict[str, Any]]:
    params = {
        "game_status": 1,
        "game_type": 1,
        "grades": "",
        "page": page,
        "limit": PAGE_SIZE,
    }

    try:
        resp = SESSION.get(URL, headers=HEADERS, params=params, timeout=(10, 30))
        print(f"[matches_scrap] page={page} status={resp.status_code}")
        if resp.status_code != 200:
            return None
        return resp.json()
    except requests.exceptions.RequestException as exc:
        print(f"[matches_scrap] page={page} request failed: {exc}")
        return None


def build_row(match: Dict[str, Any]) -> Dict[str, Any]:
    mc_info = match.get("mc_info", {})
    state = match.get("state", {})
    tt_info = match.get("tt_info", {})

    return {
        "match_id": mc_info.get("id"),
        "match_time": format_time(mc_info.get("plan_ts")),
        "bo": mc_info.get("format"),
        "team1_id": mc_info.get("t1_info", {}).get("id"),
        "team1": mc_info.get("t1_info", {}).get("disp_name"),
        "team2_id": mc_info.get("t2_info", {}).get("id"),
        "team2": mc_info.get("t2_info", {}).get("disp_name"),
        "event_id": tt_info.get("id"),
        "event_name": tt_info.get("disp_name"),
        "event_logo": tt_info.get("logo"),
        "event_start_time": tt_info.get("start_time"),
        "event_end_time": tt_info.get("end_time"),
        "score1": state.get("t1_score"),
        "score2": state.get("t2_score"),
        "status": state.get("status"),
    }


def crawl_matches(max_pages: int = DEFAULT_MAX_PAGES, max_workers: int = DEFAULT_MAX_WORKERS) -> None:
    max_workers = max(1, min(max_workers, max_pages))
    print(f"[matches_scrap] max_pages={max_pages}, max_workers={max_workers}")
    print(f"[matches_scrap] cutoff>={CUTOFF_DT.strftime('%Y-%m-%d %H:%M:%S')}")
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    all_matches: List[Dict[str, Any]] = []
    seen_ids = set()
    should_stop = False

    for batch_start in range(1, max_pages + 1, max_workers):
        batch_end = min(max_pages, batch_start + max_workers - 1)
        pages = list(range(batch_start, batch_end + 1))
        page_data: Dict[int, Optional[Dict[str, Any]]] = {}

        with ThreadPoolExecutor(max_workers=len(pages)) as executor:
            futures = {executor.submit(fetch_page, p): p for p in pages}
            for future in as_completed(futures):
                page = futures[future]
                try:
                    page_data[page] = future.result()
                except Exception as exc:
                    print(f"[matches_scrap] page={page} future failed: {exc}")
                    page_data[page] = None

        for page in pages:
            data = page_data.get(page)
            if not data:
                print(f"[matches_scrap] page={page} fetch failed, stop crawling")
                should_stop = True
                break

            matches = data.get("data", {}).get("matches", [])
            if not isinstance(matches, list):
                matches = []

            print(f"[matches_scrap] page={page} match_count={len(matches)}")
            if not matches:
                print(f"[matches_scrap] page={page} is empty, stop crawling")
                should_stop = True
                break

            page_new = 0
            older_count = 0
            page_times: List[datetime] = []
            for match in matches:
                match_dt = parse_plan_time(match.get("mc_info", {}).get("plan_ts"))
                if isinstance(match_dt, datetime):
                    page_times.append(match_dt)
                    if match_dt < CUTOFF_DT:
                        older_count += 1
                        continue

                row = build_row(match)
                match_id = row.get("match_id")
                if match_id in seen_ids:
                    continue
                seen_ids.add(match_id)
                all_matches.append(row)
                page_new += 1

            print(
                f"[matches_scrap] page={page} new_matches={page_new} "
                f"older_skipped={older_count}"
            )

            if page_times and min(page_times) < CUTOFF_DT:
                print(f"[matches_scrap] page={page} reached cutoff boundary, stop crawling")
                should_stop = True
                break

        if should_stop:
            break

    df = pd.DataFrame(all_matches)
    df.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")
    print(f"[matches_scrap] saved to {OUTPUT_FILE}, total={len(df)}")


if __name__ == "__main__":
    crawl_matches(max_pages=DEFAULT_MAX_PAGES, max_workers=DEFAULT_MAX_WORKERS)
