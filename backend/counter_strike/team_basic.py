"""
team_basic.py
-----------------

This script retrieves basic information about CS:GO teams from the 5EPlay
esports API and writes it to a CSV file.  Each row in the output file
corresponds to a team and includes the following fields:

    * team_id        – Unique identifier for the team
    * team_name      – Name of the team
    * team_logo      – URL of the team logo
    * country_logo   – URL of the team’s country flag
    * region_name    – Region the team belongs to

The script paginates through the available team list and, if necessary,
fetches detailed information for each team to populate missing fields.  It
uses polite delays between requests to avoid overwhelming the remote
service.

Note
----
The API endpoints documented here were reverse engineered from the
5EPlay CS:GO events site.  Network restrictions or API changes may cause
requests to fail or return unexpected data.  The logic is designed to
handle missing fields gracefully by providing empty strings when data
cannot be retrieved.

Example
-------
Run this module directly to crawl up to 10 pages of team data and
produce ``team_basic.csv`` in the current working directory:

    python team_basic.py

"""

import csv
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, List

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


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
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


SESSION = build_session()
DEFAULT_MAX_PAGES = int(os.getenv("CS_TEAM_MAX_PAGES", "100"))
DEFAULT_MAX_WORKERS = max(1, int(os.getenv("CS_TEAM_MAX_WORKERS", "10")))


def fetch_teams(page: int = 1, limit: int = 20, name: str = "") -> Dict[str, Any]:
    """Fetch a paginated list of teams from the 5EPlay CS:GO API.

    Parameters
    ----------
    page : int, optional
        Page number to request (default is 1).
    limit : int, optional
        Number of teams per page (default is 20).
    name : str, optional
        Partial name filter for teams (default is no filter).

    Returns
    -------
    dict
        Parsed JSON response from the API.
    """
    url = "https://esports-data.5eplaycdn.com/v1/api/csgo/teams"
    params = {
        "limit": limit,
        "page": page,
        "name": name,
    }
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/141.0.0.0 Safari/537.36"
        ),
        "Referer": "https://event.5eplay.com/csgo/teams",
    }
    response = SESSION.get(url, params=params, headers=headers, timeout=15)
    response.raise_for_status()
    return response.json()


def _parse_total_page(data: Dict[str, Any], fallback: int) -> int:
    data_block = data.get("data", {}) if isinstance(data, dict) else {}
    raw = data_block.get("total_page")
    try:
        return max(1, min(int(raw), fallback))
    except (TypeError, ValueError):
        return fallback


def _fetch_page_items(page: int) -> List[Dict[str, Any]]:
    data = fetch_teams(page=page, limit=20, name="")
    items = data.get("data", {}).get("items", [])
    return items if isinstance(items, list) else []


def crawl_team_basic(
    max_pages: int = DEFAULT_MAX_PAGES,
    output_file: str = "team_basic.csv",
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> None:
    """Crawl basic team information and write to a CSV file.

    This function iterates through the paginated team list, collects
    fundamental information for each team, and writes the result to a CSV
    file.  The number of pages to crawl can be controlled via
    ``max_pages``.

    Parameters
    ----------
    max_pages : int, optional
        Maximum number of pages to crawl (default is 5).
    output_file : str, optional
        Path to the CSV file to write (default is ``team_basic.csv``).
    """
    all_rows: List[Dict[str, Any]] = []
    page_success_count = 0

    max_workers = max(1, min(max_workers, max_pages))
    print(f"[team_basic] max_pages={max_pages}, max_workers={max_workers}")
    page_items_map: Dict[int, List[Dict[str, Any]]] = {}

    try:
        print("[team_basic] Crawling page 1...")
        first_data = fetch_teams(page=1, limit=20, name="")
        first_items = first_data.get("data", {}).get("items", [])
        first_items = first_items if isinstance(first_items, list) else []
        if not first_items:
            print("No data found on page 1. Stopping crawl.")
        else:
            page_items_map[1] = first_items
            target_pages = _parse_total_page(first_data, max_pages)

            if target_pages >= 2:
                pages = list(range(2, target_pages + 1))
                with ThreadPoolExecutor(max_workers=min(max_workers, len(pages))) as executor:
                    futures = {executor.submit(_fetch_page_items, p): p for p in pages}
                    for future in as_completed(futures):
                        page = futures[future]
                        page_items_map[page] = future.result()

            for page in range(1, target_pages + 1):
                items = page_items_map.get(page, [])
                if not items:
                    print(f"No data found on page {page}. Stopping crawl.")
                    break

                for team in items:
                    team_id = team.get("id")
                    team_name = team.get("name")
                    team_logo = team.get("logo", "")
                    country_logo = team.get("country_logo", "")
                    region_name = team.get("region_name", "")

                    all_rows.append(
                        {
                            "team_id": team_id,
                            "team_name": team_name,
                            "team_logo": team_logo,
                            "country_logo": country_logo or "",
                            "region_name": region_name or "",
                        }
                    )

                page_success_count += 1
                print(
                    f"[team_basic] Page {page} completed, "
                    f"teams_on_page={len(items)}, total_teams={len(all_rows)}"
                )
    except Exception as exc:
        print(f"Failed to crawl team pages: {exc}")
        raise

    # Write results to CSV
    with open(output_file, "w", newline="", encoding="utf-8-sig") as f:
        fieldnames = [
            "team_id",
            "team_name",
            "team_logo",
            "country_logo",
            "region_name",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"Wrote {len(all_rows)} teams to {output_file}")
    if not all_rows:
        raise RuntimeError(
            "[team_basic] No team data was crawled. CSV contains only header."
        )
    print(f"[team_basic] Successful pages: {page_success_count}")


if __name__ == "__main__":
    crawl_team_basic(max_pages=DEFAULT_MAX_PAGES, output_file="./cs_data/team_basic.csv")
