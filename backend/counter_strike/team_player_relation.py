"""
team_player_relation.py
----------------------

Builds a mapping between teams and their players by querying the 5EPlay
esports API.  Each row in the output CSV reflects one player-to-team
relationship at the time of crawling.  Multiple rows will be produced
for teams with multiple players.

The following columns are included in the output:

    * team_id             – Identifier of the team
    * team_name           – Team name
    * player_id           – Identifier of the player
    * player_name         – Player name
    * player_portrait     – URL to the player’s portrait/avatar
    * player_country_logo – URL to the country flag of the player
    * crawl_time          – Unix timestamp when the record was captured

Unknown or missing fields are filled with empty strings.  The script
relies on the team detail endpoint to obtain full player details, as
the team list only includes player names.

"""

import csv
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, Any, List, Union

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
BASE_DIR = Path(__file__).resolve().parent
DEFAULT_MAX_PAGES = int(os.getenv("CS_TEAM_MAX_PAGES", "100"))
DEFAULT_MAX_WORKERS = max(1, int(os.getenv("CS_TEAM_MAX_WORKERS", "10")))


def fetch_teams(page: int = 1, limit: int = 20, name: str = "") -> Dict[str, Any]:
    """Retrieve a page of teams from the API.

    See :func:`team_basic.fetch_teams` for parameter descriptions.
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


def fetch_team_detail(team_id: int) -> Dict[str, Any]:
    """Fetch detailed information for a team, including players.

    Returns an empty dict on failure.
    """
    url = f"https://esports-data.5eplaycdn.com/v1/api/csgo/teams/{team_id}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/141.0.0.0 Safari/537.36"
        ),
        "Referer": "https://event.5eplay.com/csgo/teams",
    }
    try:
        resp = SESSION.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return {}


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


def crawl_team_player_relation(
    max_pages: int = DEFAULT_MAX_PAGES,
    output_file: Union[str, Path] = "team_player_relation.csv",
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> None:
    """Crawl team-player relationships and write them to CSV.

    Parameters
    ----------
    max_pages : int, optional
        Number of pages of the team list to process (default is 5).
    output_file : str, optional
        Destination CSV file path (default ``team_player_relation.csv``).
    """
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    rows: List[Dict[str, Any]] = []
    page_success_count = 0
    teams_without_players = 0

    max_workers = max(1, min(max_workers, max_pages))
    print(f"[team_player_relation] max_pages={max_pages}, max_workers={max_workers}")
    page_items_map: Dict[int, List[Dict[str, Any]]] = {}

    try:
        print("[team_player_relation] Crawling page 1...")
        first_data = fetch_teams(page=1, limit=20, name="")
        first_items = first_data.get("data", {}).get("items", [])
        first_items = first_items if isinstance(first_items, list) else []
        if not first_items:
            print("No teams on page 1. Stopping.")
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
                    print(f"No teams on page {page}. Stopping.")
                    break

                for team in items:
                    team_id = team.get("id")
                    team_name = team.get("name", "")

                    # Player roster is already present in team-list response.
                    players = team.get("players", [])
                    if not isinstance(players, list):
                        players = []

                    # Backward-compatible fallback: try detail endpoint if list
                    # response does not include players.
                    if not players:
                        detail = fetch_team_detail(team_id)
                        detail_data = detail.get("data", {}) if isinstance(detail, dict) else {}
                        players = (
                            detail_data.get("players", [])
                            if isinstance(detail_data.get("players", []), list)
                            else []
                        )

                    if not players:
                        teams_without_players += 1

                    for player in players:
                        player_id = player.get("player_id") or player.get("id", "")
                        player_name = player.get("name", "")
                        # Many APIs name the avatar field differently; check a few possibilities
                        portrait = (
                            player.get("portrait")
                            or player.get("avatar")
                            or player.get("portrait_url")
                            or ""
                        )
                        country_logo = (
                            player.get("country_logo")
                            or player.get("player_country_logo")
                            or ""
                        )

                        rows.append(
                            {
                                "team_id": team_id,
                                "team_name": team_name,
                                "player_id": player_id,
                                "player_name": player_name,
                                "player_portrait": portrait,
                                "player_country_logo": country_logo,
                                "crawl_time": int(time.time()),
                            }
                        )

                page_success_count += 1
                print(
                    f"[team_player_relation] Page {page} completed, "
                    f"teams_on_page={len(items)}, total_player_rows={len(rows)}"
                )
    except Exception as exc:
        print(f"Error processing team-player crawl: {exc}")
        raise

    # Write results
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        fieldnames = [
            "team_id",
            "team_name",
            "player_id",
            "player_name",
            "player_portrait",
            "player_country_logo",
            "crawl_time",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} team-player records to {output_path}")
    print(
        f"[team_player_relation] Successful pages: {page_success_count}, "
        f"teams_without_players={teams_without_players}"
    )
    if not rows:
        raise RuntimeError(
            "[team_player_relation] No team-player rows were crawled. "
            "Detail API likely failed or returned empty data."
        )


if __name__ == "__main__":
    crawl_team_player_relation(
        max_pages=DEFAULT_MAX_PAGES,
        output_file=BASE_DIR / "cs_data" / "team_player_relation.csv",
    )
