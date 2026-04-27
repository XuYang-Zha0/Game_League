from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

URL = "https://app.5eplay.com/api/tournament/session_result_list"
BASE_DIR = Path(__file__).resolve().parent
OUTPUT_FILE = BASE_DIR / "cs_data" / "cs2_results_5eplay.csv"
PAGE_SIZE = 20
DEFAULT_MAX_PAGES = int(os.getenv("CS_RESULT_MAX_PAGES", "0"))
DEFAULT_MAX_WORKERS = max(1, int(os.getenv("CS_RESULT_MAX_WORKERS", "10")))
SLEEP_SECONDS = float(os.getenv("CS_RESULT_SLEEP_SECONDS", "0"))
DEFAULT_CUTOFF_TEXT = os.getenv("CS_RESULT_CUTOFF", "2023-01-01 00:00:00")
DEFAULT_GRADE_FILTER_TEXT = os.getenv("CS_RESULT_GRADES", "1,2,3,4,6,7,9")
DEFAULT_COMPLETED_STATUS_TEXT = os.getenv("CS_RESULT_COMPLETED_STATUSES", "2")
HARD_MAX_PAGES = max(1, int(os.getenv("CS_RESULT_HARD_MAX_PAGES", "100000")))

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


def parse_csv_tokens(text: str) -> set[str]:
    value = str(text or "").strip()
    if not value:
        return set()
    return {x.strip() for x in value.split(",") if x.strip()}


ALLOWED_GRADE_CODES = parse_csv_tokens(DEFAULT_GRADE_FILTER_TEXT)
COMPLETED_STATUS_CODES = parse_csv_tokens(DEFAULT_COMPLETED_STATUS_TEXT)
HIGH_GRADE_LABEL_KEYWORDS = ("major", "s+", "s级", "a级", "b级")


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def is_b_or_above_grade(match: Dict[str, Any]) -> bool:
    mc_info = match.get("mc_info", {})
    tt_info = match.get("tt_info", {})
    if not isinstance(mc_info, dict):
        mc_info = {}
    if not isinstance(tt_info, dict):
        tt_info = {}

    grade_labels = [
        tt_info.get("special_grade_label"),
        tt_info.get("grade_label"),
        mc_info.get("grade_label"),
    ]
    for label in grade_labels:
        normalized_label = normalize_text(label).lower()
        if normalized_label and any(k in normalized_label for k in HIGH_GRADE_LABEL_KEYWORDS):
            return True

    if not ALLOWED_GRADE_CODES:
        return True

    grade_codes = [normalize_text(tt_info.get("grade")), normalize_text(mc_info.get("grade"))]
    return any(code in ALLOWED_GRADE_CODES for code in grade_codes if code)


def is_completed_match(match: Dict[str, Any]) -> bool:
    mc_info = match.get("mc_info", {})
    state = match.get("state", {})
    if not isinstance(mc_info, dict):
        mc_info = {}
    if not isinstance(state, dict):
        state = {}

    status_candidates = [
        normalize_text(state.get("status")),
        normalize_text(mc_info.get("match_status")),
    ]
    if any(code in COMPLETED_STATUS_CODES for code in status_candidates if code):
        return True

    text_status = normalize_text(mc_info.get("status")).lower()
    return text_status in {"past", "finished", "ended", "done"}


def format_time(ts: Any) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)


def make_realtime_token() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def make_next_token(last_ts: Any) -> Optional[str]:
    if not last_ts:
        return None
    try:
        dt = datetime.fromtimestamp(int(last_ts)) - timedelta(seconds=1)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def parse_plan_time(ts: Any) -> Optional[datetime]:
    if ts in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(ts))
    except Exception:
        return None


def fetch_page(page_num: int, page_token: str) -> Optional[Dict[str, Any]]:
    params = {
        "game_type": 1,
        "order_by": "asc",
        "grades": DEFAULT_GRADE_FILTER_TEXT,
        "page_size": PAGE_SIZE,
        "page_token": page_token,
    }

    try:
        resp = SESSION.get(URL, headers=HEADERS, params=params, timeout=(10, 30))
        print(f"[matches_result] page={page_num} status={resp.status_code}")
        if resp.status_code != 200:
            return None
        return resp.json()
    except requests.exceptions.RequestException as exc:
        print(f"[matches_result] page={page_num} request failed: {exc}")
        return None


def build_row(match: Dict[str, Any]) -> Dict[str, Any]:
    mc_info = match.get("mc_info", {})
    state = match.get("state", {})
    tt_info = match.get("tt_info", {})
    bout_states = state.get("bout_states", [])
    if not isinstance(bout_states, list):
        bout_states = []

    bout_summary_list = []
    for bout in bout_states:
        bout_summary = (
            f"{bout.get('map_name', '')}:"
            f"{bout.get('t1_score', '')}-{bout.get('t2_score', '')}"
            f"(winner:{bout.get('result', '')})"
        )
        bout_summary_list.append(bout_summary)

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
        "bout_count": len(bout_states),
        "bout_details": " | ".join(bout_summary_list),
    }


def build_rows_parallel(matches: List[Dict[str, Any]], max_workers: int) -> List[Dict[str, Any]]:
    if not matches:
        return []
    workers = max(1, min(max_workers, len(matches)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(build_row, matches))


def crawl_results(max_pages: int = DEFAULT_MAX_PAGES, max_workers: int = DEFAULT_MAX_WORKERS) -> None:
    max_workers = max(1, max_workers)
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    all_matches: List[Dict[str, Any]] = []
    seen_ids = set()

    page_token = make_realtime_token()
    page_limit_text = str(max_pages) if max_pages > 0 else "unlimited(until cutoff)"
    print(f"[matches_result] max_pages={page_limit_text}, max_workers={max_workers}")
    print(f"[matches_result] start_token={page_token}")
    print(f"[matches_result] cutoff>={CUTOFF_DT.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"[matches_result] grades={DEFAULT_GRADE_FILTER_TEXT or 'ALL'}")
    print(f"[matches_result] completed_status={','.join(sorted(COMPLETED_STATUS_CODES)) or 'ANY'}")

    page = 1
    while True:
        if max_pages > 0 and page > max_pages:
            print(f"[matches_result] reached configured page limit: {max_pages}")
            break
        if page > HARD_MAX_PAGES:
            print(f"[matches_result] reached hard page limit: {HARD_MAX_PAGES}, stop crawling")
            break

        data = fetch_page(page, page_token)
        if not data:
            print(f"[matches_result] page={page} fetch failed, stop crawling")
            break

        matches = data.get("data", {}).get("matches", [])
        if not isinstance(matches, list):
            matches = []

        print(f"[matches_result] page={page} match_count={len(matches)}")
        if not matches:
            print(f"[matches_result] page={page} is empty, stop crawling")
            break

        in_range_matches: List[Dict[str, Any]] = []
        page_times: List[datetime] = []
        older_skipped = 0
        grade_skipped = 0
        unfinished_skipped = 0
        for match in matches:
            match_dt = parse_plan_time(match.get("mc_info", {}).get("plan_ts"))
            if isinstance(match_dt, datetime):
                page_times.append(match_dt)
                if match_dt < CUTOFF_DT:
                    older_skipped += 1
                    continue

            if not is_b_or_above_grade(match):
                grade_skipped += 1
                continue

            if not is_completed_match(match):
                unfinished_skipped += 1
                continue

            in_range_matches.append(match)

        rows = build_rows_parallel(in_range_matches, max_workers=max_workers)
        page_new = 0
        for row in rows:
            match_id = row.get("match_id")
            if match_id in seen_ids:
                continue
            seen_ids.add(match_id)
            all_matches.append(row)
            page_new += 1

        print(
            f"[matches_result] page={page} in_range={len(in_range_matches)} "
            f"new_matches={page_new} older_skipped={older_skipped} "
            f"grade_skipped={grade_skipped} unfinished_skipped={unfinished_skipped}"
        )

        last_match = matches[-1]
        last_ts = last_match.get("mc_info", {}).get("plan_ts")
        next_token = make_next_token(last_ts)
        if not next_token:
            print("[matches_result] next token unavailable, stop crawling")
            break
        if next_token == page_token:
            print("[matches_result] next token did not advance, stop crawling")
            break

        if page_times and min(page_times) < CUTOFF_DT:
            print(f"[matches_result] page={page} reached cutoff boundary, stop crawling")
            break

        page_token = next_token
        page += 1
        if SLEEP_SECONDS > 0:
            time.sleep(SLEEP_SECONDS)

    df = pd.DataFrame(all_matches)
    df.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")
    print(f"[matches_result] saved to {OUTPUT_FILE}, total={len(df)}")


if __name__ == "__main__":
    crawl_results(max_pages=DEFAULT_MAX_PAGES, max_workers=DEFAULT_MAX_WORKERS)
