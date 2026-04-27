from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

URL = "https://app.5eplay.com/api/csgo/tournament/csgo_event_list_v1"
BASE_DIR = Path(__file__).resolve().parent
OUTPUT_FILE = BASE_DIR / "cs_data" / "event_basic_5eplay.csv"

DEFAULT_CUTOFF_TEXT = os.getenv("CS_EVENT_CUTOFF", "2023-01-01 00:00:00")
DEFAULT_END_TEXT = os.getenv("CS_EVENT_END", "")
DEFAULT_EVENT_GRADES = os.getenv("CS_EVENT_GRADES", "1,2,3,4,6,7,9")
DEFAULT_MIN_WINDOW_SECONDS = int(os.getenv("CS_EVENT_MIN_WINDOW_SECONDS", "86400"))
SLEEP_SECONDS = float(os.getenv("CS_EVENT_SLEEP_SECONDS", "0"))
PAGE_LIMIT_HINT = int(os.getenv("CS_EVENT_PAGE_LIMIT_HINT", "20"))

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
        backoff_factor=1.2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["POST"],
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


SESSION = build_session()


def parse_datetime_text(text: Any, fallback: Optional[datetime] = None) -> Optional[datetime]:
    raw = str(text or "").strip()
    if not raw:
        return fallback
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw, fmt)
            if fmt == "%Y-%m-%d":
                return dt.replace(hour=0, minute=0, second=0, microsecond=0)
            return dt
        except ValueError:
            continue
    return fallback


def parse_grade_tokens(text: str) -> List[str]:
    raw = str(text or "").strip()
    if not raw:
        return []
    return [x.strip() for x in raw.split(",") if x.strip()]


def fmt_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def month_end_of(dt: datetime) -> datetime:
    month_start = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    next_month = (month_start + timedelta(days=32)).replace(day=1)
    return next_month - timedelta(seconds=1)


def parse_item_start_time(item: Dict[str, Any]) -> Optional[datetime]:
    basic = item.get("basic_info", {}) or {}
    if not isinstance(basic, dict):
        return None
    return parse_datetime_text(basic.get("start_time"))


def request_event_items(start_dt: datetime, end_dt: datetime) -> Optional[List[Dict[str, Any]]]:
    time_value = f"{fmt_dt(start_dt)}_{fmt_dt(end_dt)}"
    payload = {
        "tournaments_options": {
            "grade": parse_grade_tokens(DEFAULT_EVENT_GRADES),
            "tt_series": [],
            "tt_bonus": [],
            "page_token": "",
            # Non-empty cursor makes this endpoint return historical range data.
            "cursor": "init",
            "time_value": time_value,
            "time_type": "self",
            "player_id": "",
            "team_id": "",
        }
    }

    try:
        resp = SESSION.post(URL, headers=HEADERS, json=payload, timeout=(10, 30))
        if resp.status_code != 200:
            print(f"[event_basic] window={time_value} status={resp.status_code}")
            return None
        data = resp.json()
    except requests.exceptions.RequestException as exc:
        print(f"[event_basic] window={time_value} request failed: {exc}")
        return None
    except ValueError as exc:
        print(f"[event_basic] window={time_value} invalid json: {exc}")
        return None

    items = data.get("data", {}).get("items", [])
    if not isinstance(items, list):
        items = []
    print(f"[event_basic] window={time_value} items={len(items)}")
    return items


def fetch_window_with_split(start_dt: datetime, end_dt: datetime, depth: int = 0) -> List[Dict[str, Any]]:
    if start_dt > end_dt:
        return []

    items = request_event_items(start_dt, end_dt)
    if items is None:
        return []

    in_range_items = []
    for item in items:
        item_start = parse_item_start_time(item)
        if isinstance(item_start, datetime) and item_start < start_dt:
            continue
        if isinstance(item_start, datetime) and item_start > end_dt:
            continue
        in_range_items.append(item)

    span_seconds = max(1, int((end_dt - start_dt).total_seconds()))
    if len(in_range_items) < PAGE_LIMIT_HINT:
        return in_range_items

    if span_seconds <= max(1, DEFAULT_MIN_WINDOW_SECONDS):
        print(
            f"[event_basic] window={fmt_dt(start_dt)}~{fmt_dt(end_dt)} "
            f"reached min split span with full page ({len(in_range_items)}), keep current slice"
        )
        return in_range_items

    mid_dt = start_dt + timedelta(seconds=span_seconds // 2)
    right_start = mid_dt + timedelta(seconds=1)
    if right_start > end_dt:
        return in_range_items

    indent = "  " * min(depth, 6)
    print(
        f"[event_basic] {indent}split window {fmt_dt(start_dt)}~{fmt_dt(end_dt)} "
        f"because items={len(in_range_items)}"
    )
    left_items = fetch_window_with_split(start_dt, mid_dt, depth=depth + 1)
    right_items = fetch_window_with_split(right_start, end_dt, depth=depth + 1)
    return left_items + right_items


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def extract_event_row(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    basic = item.get("basic_info", {}) or {}
    if not isinstance(basic, dict):
        return None

    event_id = normalize_text(basic.get("id"))
    if not event_id:
        return None

    return {
        "event_id": event_id,
        "event_name": normalize_text(basic.get("disp_name")),
        "event_logo": normalize_text(basic.get("logo")),
        "grade": normalize_text(basic.get("grade")),
        "grade_label": normalize_text(basic.get("grade_label")),
        "special_grade_label": normalize_text(basic.get("special_grade_label")),
        "status": normalize_text(basic.get("status")),
        "start_time": normalize_text(basic.get("start_time")),
        "end_time": normalize_text(basic.get("end_time")),
        "bonus": normalize_text(basic.get("bonus")),
        "city_name": normalize_text(basic.get("city_name")),
        "mode": normalize_text(basic.get("mode")),
        "cover": normalize_text(basic.get("cover")),
    }


def merge_event_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        event_id = normalize_text(row.get("event_id"))
        if not event_id:
            continue
        if event_id not in merged:
            merged[event_id] = dict(row)
            continue

        current = merged[event_id]
        for key, value in row.items():
            if key == "event_id":
                continue
            old_val = normalize_text(current.get(key))
            new_val = normalize_text(value)
            if (not old_val) and new_val:
                current[key] = value
    out = list(merged.values())
    out.sort(key=lambda x: (normalize_text(x.get("start_time")), normalize_text(x.get("event_id"))))
    return out


def crawl_events() -> None:
    cutoff = parse_datetime_text(DEFAULT_CUTOFF_TEXT, fallback=datetime(2023, 1, 1, 0, 0, 0))
    if cutoff is None:
        cutoff = datetime(2023, 1, 1, 0, 0, 0)

    end_dt = parse_datetime_text(DEFAULT_END_TEXT, fallback=datetime.now())
    if end_dt is None:
        end_dt = datetime.now()
    if end_dt < cutoff:
        print(
            f"[event_basic] end before cutoff, fallback end=now. "
            f"cutoff={fmt_dt(cutoff)} end_raw={DEFAULT_END_TEXT!r}"
        )
        end_dt = datetime.now()

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    all_items: List[Dict[str, Any]] = []
    window_start = cutoff
    month_index = 0
    while window_start <= end_dt:
        month_index += 1
        window_end = min(month_end_of(window_start), end_dt)
        print(
            f"[event_basic] month_window#{month_index} "
            f"{fmt_dt(window_start)} ~ {fmt_dt(window_end)}"
        )
        window_items = fetch_window_with_split(window_start, window_end)
        all_items.extend(window_items)

        window_start = window_end + timedelta(seconds=1)
        if SLEEP_SECONDS > 0:
            time.sleep(SLEEP_SECONDS)

    rows: List[Dict[str, Any]] = []
    for item in all_items:
        row = extract_event_row(item)
        if row is None:
            continue

        start_time = parse_datetime_text(row.get("start_time"))
        if isinstance(start_time, datetime) and start_time < cutoff:
            continue
        if isinstance(start_time, datetime) and start_time > end_dt:
            continue
        rows.append(row)

    final_rows = merge_event_rows(rows)
    df = pd.DataFrame(final_rows)
    df.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")
    print(f"[event_basic] saved to {OUTPUT_FILE}, total={len(df)}")


if __name__ == "__main__":
    crawl_events()
