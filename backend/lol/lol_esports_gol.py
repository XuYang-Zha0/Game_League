from __future__ import annotations

import csv
import html
import os
import re
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


BASE_DIR = Path(__file__).resolve().parent
LOL_DATA_DIR = BASE_DIR / "lol_data"
GOL_BASE_URL = "https://gol.gg"
DEFAULT_TOURNAMENTS = os.getenv("LOL_TOURNAMENTS", "Worlds 2025 Main Event")
DEFAULT_MATCH_LIMIT = int(os.getenv("LOL_MATCH_LIMIT", "20"))
DEFAULT_SLEEP_SECONDS = float(os.getenv("LOL_GOL_SLEEP_SECONDS", "0.4"))
DEFAULT_GOL_ENABLED = os.getenv("LOL_GOL_ENABLED", "0").strip().lower() not in {"0", "false", "no", "off"}
LOL_ESPORTS_API_KEY = os.getenv("LOL_ESPORTS_API_KEY", "0TvQnueqKa5mxJntVWt0w4LpLfEkrV1Ta8rQBb9Z")
LOL_ESPORTS_API_BASE = "https://esports-api.lolesports.com/persisted/gw"
LOL_LIVESTATS_API_BASE = "https://feed.lolesports.com/livestats/v1"
DEFAULT_SCHEDULE_START = os.getenv("LOL_SCHEDULE_START_DATE", "2023-01-01")
DEFAULT_SCHEDULE_FUTURE_MONTHS = int(os.getenv("LOL_SCHEDULE_FUTURE_MONTHS", "4"))
DEFAULT_SCHEDULE_LEAGUES = os.getenv(
    "LOL_SCHEDULE_LEAGUES",
    "worlds,msi,first_stand,lck,lpl,lec,lcp,lcs,cblol-brazil,vcs,pcs,lla",
)
DEFAULT_EVENT_DETAILS_ENABLED = os.getenv("LOL_EVENT_DETAILS_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}
DEFAULT_EVENT_DETAILS_WORKERS = int(os.getenv("LOL_EVENT_DETAILS_WORKERS", "8"))
DEFAULT_LIVESTATS_ENABLED = os.getenv("LOL_LIVESTATS_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}
DEFAULT_LIVESTATS_LIMIT = int(os.getenv("LOL_LIVESTATS_LIMIT", "0"))
DEFAULT_LIVESTATS_DETAIL_OFFSETS = os.getenv("LOL_LIVESTATS_DETAIL_OFFSETS", "30,40,50,60,70,80")
DEFAULT_GOL_BACKFILL_TOURNAMENTS = os.getenv("LOL_GOL_BACKFILL_TOURNAMENTS", "")

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
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
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


SESSION = build_session()


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_iso_datetime(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def parse_row_datetime(row: Dict[str, Any]) -> Optional[datetime]:
    for key in ("match_time", "match_date"):
        dt = parse_iso_datetime(row.get(key))
        if dt:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
    return None


def normalize_league_region(league_slug: str, region: str) -> str:
    slug = league_slug.lower()
    mapping = {
        "worlds": "International",
        "msi": "International",
        "first_stand": "International",
        "lck": "LCK",
        "lpl": "LPL",
        "lec": "LEC",
        "lcp": "LCP",
        "lcs": "LTA",
        "cblol-brazil": "LTA",
        "lla": "LTA",
        "vcs": "VCS",
        "pcs": "PCS",
    }
    return mapping.get(slug, region or "")


def normalize_role(value: Any) -> str:
    role = str(value or "").strip().upper()
    mapping = {
        "TOP": "TOP",
        "JUNGLE": "JUG",
        "JUG": "JUG",
        "MID": "MID",
        "MIDDLE": "MID",
        "BOTTOM": "BOT",
        "BOT": "BOT",
        "ADC": "BOT",
        "SUPPORT": "SUP",
        "SUP": "SUP",
    }
    return mapping.get(role, role)


def infer_league_slug(value: str) -> str:
    text = (value or "").lower()
    mapping = [
        ("world", "worlds"),
        ("msi", "msi"),
        ("first stand", "first_stand"),
        ("lck", "lck"),
        ("lpl", "lpl"),
        ("lec", "lec"),
        ("lcp", "lcp"),
        ("lcs", "lcs"),
        ("cblol", "cblol-brazil"),
        ("vcs", "vcs"),
        ("pcs", "pcs"),
        ("lla", "lla"),
    ]
    for needle, slug in mapping:
        if needle in text:
            return slug
    return ""


def player_name_from_summoner(summoner_name: Any, team_code: Any = "") -> str:
    text = str(summoner_name or "").strip()
    code = str(team_code or "").strip()
    if code and text.upper().startswith(code.upper()) and len(text) > len(code):
        return text[len(code) :].strip()
    return text


def common_summoner_prefix(players: List[Dict[str, Any]]) -> str:
    names = [str(player.get("summonerName") or "").strip() for player in players if str(player.get("summonerName") or "").strip()]
    if len(names) < 2:
        return ""
    prefix = names[0]
    for name in names[1:]:
        while prefix and not name.upper().startswith(prefix.upper()):
            prefix = prefix[:-1]
    if len(prefix.strip()) < 2:
        return ""
    return prefix


def livestats_detail_offsets() -> List[int]:
    offsets: List[int] = []
    for item in DEFAULT_LIVESTATS_DETAIL_OFFSETS.split(","):
        value = parse_int(item)
        if value is not None and value > 0:
            offsets.append(value)
    return offsets or [30, 40, 50, 60]


def normalize_image_url(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("//"):
        return f"https:{text}"
    if text.startswith("http://"):
        return "https://" + text[len("http://") :]
    return text


def image_filename(value: Any) -> str:
    return str(value or "").rsplit("/", 1)[-1].strip()


def request_lolesports(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    response = SESSION.get(
        f"{LOL_ESPORTS_API_BASE}{path}",
        params=params,
        headers={
            "Accept": "application/json",
            "User-Agent": HEADERS["User-Agent"],
            "x-api-key": LOL_ESPORTS_API_KEY,
        },
        timeout=(10, 30),
    )
    response.raise_for_status()
    return response.json()


def request_livestats(path: str) -> Dict[str, Any]:
    response = SESSION.get(
        f"{LOL_LIVESTATS_API_BASE}{path}",
        headers={
            "Accept": "application/json",
            "User-Agent": HEADERS["User-Agent"],
        },
        timeout=(10, 30),
    )
    response.raise_for_status()
    return response.json()


def slugify(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "_", value.strip().lower())
    return re.sub(r"_+", "_", text).strip("_") or "unknown"


def clean_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value or "")
    return html.unescape(value).replace("\xa0", " ").strip()


def parse_int(value: Any) -> Optional[int]:
    text = str(value).strip() if value is not None else ""
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def split_score(score: str) -> Tuple[Optional[int], Optional[int]]:
    match = re.search(r"(\d+)\s*-\s*(\d+)", score or "")
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def split_kda(kda: str) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    match = re.search(r"(\d+)\s*/\s*(\d+)\s*/\s*(\d+)", kda or "")
    if not match:
        return None, None, None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def request_html(url: str) -> str:
    response = SESSION.get(url, headers=HEADERS, timeout=(10, 30))
    response.raise_for_status()
    return response.text


def tournament_matchlist_url(tournament_name: str) -> str:
    return f"{GOL_BASE_URL}/tournament/tournament-matchlist/{quote(tournament_name)}/"


def parse_matchlist(tournament_name: str, html_text: str) -> List[Dict[str, Any]]:
    tournament_id = slugify(tournament_name)
    rows: List[Dict[str, Any]] = []
    pattern = re.compile(
        r"<tr><td class='text-left'><a href='(?P<href>[^']+)' title='(?P<title>[^']*)'>"
        r"(?P<label>.*?)</a></td>"
        r"<td class='text-right (?P<left_class>[^']*)'>(?P<team1>.*?)</td>"
        r"<td class='text-center'>(?P<score>.*?)</td>"
        r"<td class='(?P<right_class>[^']*)'>(?P<team2>.*?)</td>"
        r"<td class='text-center'>(?P<stage>.*?)</td>"
        r"<td class='text-center'>(?P<patch>.*?)</td>"
        r"<td class='text-center'>(?P<date>.*?)</td></tr>",
        re.S,
    )

    for match in pattern.finditer(html_text):
        first_game_id = re.search(r"/game/stats/(\d+)/", match.group("href"))
        if not first_game_id:
            continue
        score1, score2 = split_score(clean_text(match.group("score")))
        team1 = clean_text(match.group("team1"))
        team2 = clean_text(match.group("team2"))
        winner = ""
        if "text_victory" in match.group("left_class"):
            winner = team1
        elif "text_victory" in match.group("right_class"):
            winner = team2

        rows.append(
            {
                "match_id": f"gol_{first_game_id.group(1)}",
                "source": "gol.gg",
                "source_match_url": f"{GOL_BASE_URL}/game/stats/{first_game_id.group(1)}/page-summary/",
                "first_game_id": first_game_id.group(1),
                "event_id": tournament_id,
                "event_name": tournament_name,
                "match_date": clean_text(match.group("date")),
                "stage": clean_text(match.group("stage")),
                "patch": clean_text(match.group("patch")),
                "team1_id": slugify(team1),
                "team1": team1,
                "team2_id": slugify(team2),
                "team2": team2,
                "score1": score1,
                "score2": score2,
                "winner": winner,
                "bo": (score1 or 0) + (score2 or 0) if score1 is not None and score2 is not None else None,
                "fetched_at": now_text(),
            }
        )
    return rows


def parse_game_ids(game_html: str, fallback_game_id: str) -> List[str]:
    ids = re.findall(r"game/stats/(\d+)/page-game/", game_html)
    ordered: List[str] = []
    for game_id in [fallback_game_id, *ids]:
        if game_id not in ordered:
            ordered.append(game_id)
    return ordered


def parse_game_players(
    game_html: str,
    *,
    match_row: Dict[str, Any],
    game_id: str,
    game_number: int,
) -> List[Dict[str, Any]]:
    row_pattern = re.compile(
        r"alt='(?P<champion>[^']+)' src='../_img/champions_icon/[^']+'\s*/></a>&nbsp;"
        r"<a class='link-blanc'[^>]*href='(?P<player_href>[^']*)'[^>]*>(?P<player>[^<]+)</a>"
        r".*?<td style='text-align:center'>(?P<kda>[^<]*)</td>"
        r"<td style='text-align:center;?'?>\s*(?P<cs>[^<]*)</td>",
        re.S,
    )
    rows: List[Dict[str, Any]] = []
    for index, match in enumerate(row_pattern.finditer(game_html), start=1):
        kills, deaths, assists = split_kda(clean_text(match.group("kda")))
        team_side = "blue" if index <= 5 else "red"
        team_id = match_row["team1_id"] if index <= 5 else match_row["team2_id"]
        team_name = match_row["team1"] if index <= 5 else match_row["team2"]
        player_name = clean_text(match.group("player"))
        rows.append(
            {
                "game_id": game_id,
                "match_id": match_row["match_id"],
                "event_id": match_row["event_id"],
                "event_name": match_row["event_name"],
                "game_number": game_number,
                "team_side": team_side,
                "team_id": team_id,
                "team_name": team_name,
                "player_id": slugify(player_name),
                "player_name": player_name,
                "champion": clean_text(match.group("champion")),
                "kills": kills,
                "deaths": deaths,
                "assists": assists,
                "kda_text": clean_text(match.group("kda")),
                "cs": parse_int(clean_text(match.group("cs"))),
                "stat_index": index,
                "source_player_url": match.group("player_href"),
                "fetched_at": now_text(),
            }
        )
    return rows


def build_game_basic_row(
    match_row: Dict[str, Any],
    *,
    game_id: str,
    game_number: int,
    source_url: str,
) -> Dict[str, Any]:
    return {
        "game_id": game_id,
        "match_id": match_row["match_id"],
        "event_id": match_row["event_id"],
        "event_name": match_row["event_name"],
        "game_number": game_number,
        "match_date": match_row["match_date"],
        "stage": match_row["stage"],
        "patch": match_row["patch"],
        "team1_id": match_row["team1_id"],
        "team1": match_row["team1"],
        "team2_id": match_row["team2_id"],
        "team2": match_row["team2"],
        "source_game_url": source_url,
        "fetched_at": now_text(),
    }


def dedupe_rows(rows: Iterable[Dict[str, Any]], keys: List[str]) -> List[Dict[str, Any]]:
    seen = set()
    result = []
    for row in rows:
        marker = tuple(row.get(key) for key in keys)
        if marker in seen:
            continue
        seen.add(marker)
        result.append(row)
    return result


def source_priority(value: Any) -> int:
    source = str(value or "").strip().lower()
    if source == "lolesports":
        return 3
    if source == "stats-derived":
        return 2
    if source == "gol.gg":
        return 1
    return 0


def merge_player_basic_rows(rows: List[Dict[str, Any]], game_player_stats: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    team_counts: Dict[Tuple[str, str], int] = defaultdict(int)

    for stat in game_player_stats:
        name_key = slugify(str(stat.get("player_name") or stat.get("player_id") or ""))
        team_id = str(stat.get("team_id") or "").strip()
        if name_key and name_key != "unknown" and team_id:
            team_counts[(name_key, team_id)] += 1

    for row in rows:
        name_key = slugify(str(row.get("player_name") or row.get("player_id") or ""))
        if not name_key or name_key == "unknown":
            continue
        grouped[name_key].append(row)

    merged_rows: List[Dict[str, Any]] = []
    for name_key, items in grouped.items():
        if not items:
            continue

        def score(item: Dict[str, Any]) -> Tuple[int, int, int, int]:
            team_id = str(item.get("team_id") or "").strip()
            return (
                source_priority(item.get("source")),
                1 if str(item.get("avatar") or "").strip() else 0,
                1 if str(item.get("role") or "").strip() else 0,
                team_counts.get((name_key, team_id), 0),
            )

        sorted_items = sorted(items, key=score, reverse=True)
        lolesports_items = [item for item in sorted_items if str(item.get("source") or "").strip().lower() == "lolesports"]
        team_seed = lolesports_items[0] if lolesports_items else sorted_items[0]
        team_id = str(team_seed.get("team_id") or "").strip()
        team_items = [item for item in sorted_items if str(item.get("team_id") or "").strip() == team_id]
        base = dict(team_items[0] if team_items else sorted_items[0])

        base["player_name"] = next((str(item.get("player_name") or "").strip() for item in sorted_items if str(item.get("player_name") or "").strip()), base.get("player_name") or "")
        base["player_id"] = slugify(str(base.get("player_name") or "")) if str(base.get("player_name") or "").strip() else str(base.get("player_id") or "")
        base["team_id"] = team_id
        base["team_name"] = next((str(item.get("team_name") or "").strip() for item in team_items if str(item.get("team_name") or "").strip()), str(base.get("team_name") or "").strip())
        base["role"] = next((str(item.get("role") or "").strip() for item in sorted_items if str(item.get("role") or "").strip()), str(base.get("role") or "").strip())
        base["avatar"] = next((str(item.get("avatar") or "").strip() for item in sorted_items if str(item.get("avatar") or "").strip()), str(base.get("avatar") or "").strip())
        base["source"] = "merged"
        base["fetched_at"] = now_text()
        merged_rows.append(base)

    return dedupe_rows(merged_rows, ["player_id"])


LOL_ROLE_ORDER = {
    "TOP": 0,
    "JUNGLE": 1,
    "JUG": 1,
    "MID": 2,
    "MIDDLE": 2,
    "BOTTOM": 3,
    "BOT": 3,
    "ADC": 3,
    "SUPPORT": 4,
    "SUP": 4,
}


def player_matches_team_code(player: Dict[str, Any], team_code: Any) -> bool:
    code = re.sub(r"[^A-Za-z0-9]+", "", str(team_code or "")).upper()
    if not code:
        return False
    filename = image_filename(player.get("image")).upper()
    return bool(filename and re.search(rf"(^|_){re.escape(code)}(_|-)", filename))


def roster_pick_score(player: Dict[str, Any], team_code: Any) -> Tuple[int, int, str]:
    image = str(player.get("image") or "")
    filename = image_filename(image).lower()
    return (
        1 if player_matches_team_code(player, team_code) else 0,
        0 if "default-headshot" in filename else 1,
        str(player.get("summonerName") or player.get("name") or ""),
    )


def select_lolesports_roster_players(team: Dict[str, Any]) -> List[Dict[str, Any]]:
    players = [player for player in (team.get("players") or []) if str(player.get("summonerName") or player.get("name") or "").strip()]
    if not players:
        return []

    code_matches = [player for player in players if player_matches_team_code(player, team.get("code"))]
    candidates = code_matches or [player for player in players if "default-headshot" not in image_filename(player.get("image")).lower()] or players

    by_role: Dict[int, Dict[str, Any]] = {}
    extras: List[Dict[str, Any]] = []
    for player in sorted(candidates, key=lambda item: roster_pick_score(item, team.get("code")), reverse=True):
        role_idx = LOL_ROLE_ORDER.get(str(player.get("role") or "").strip().upper())
        if role_idx is None:
            extras.append(player)
        elif role_idx not in by_role:
            by_role[role_idx] = player
        else:
            extras.append(player)

    selected = [by_role[idx] for idx in sorted(by_role)]
    for player in extras:
        if len(selected) >= 5:
            break
        if player not in selected:
            selected.append(player)
    return selected[:5]


def write_csv(path: Path, rows: List[Dict[str, Any]], fieldnames: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"[DONE] {path.name}: {len(rows)} rows")


def crawl_tournament(
    tournament_name: str,
    *,
    match_limit: int = DEFAULT_MATCH_LIMIT,
    sleep_seconds: float = DEFAULT_SLEEP_SECONDS,
) -> Dict[str, List[Dict[str, Any]]]:
    url = tournament_matchlist_url(tournament_name)
    print(f"[lol_esports] Fetch tournament: {tournament_name}")
    matchlist_html = request_html(url)
    match_rows = parse_matchlist(tournament_name, matchlist_html)
    if match_limit > 0:
        match_rows = match_rows[:match_limit]

    game_rows: List[Dict[str, Any]] = []
    player_rows: List[Dict[str, Any]] = []

    for match_row in match_rows:
        first_game_id = str(match_row["first_game_id"])
        first_url = f"{GOL_BASE_URL}/game/stats/{first_game_id}/page-game/"
        try:
            first_html = request_html(first_url)
        except requests.RequestException as exc:
            print(f"[WARN] failed to fetch game nav {first_game_id}: {exc}")
            continue

        game_ids = parse_game_ids(first_html, first_game_id)
        for game_number, game_id in enumerate(game_ids, start=1):
            source_url = f"{GOL_BASE_URL}/game/stats/{game_id}/page-game/"
            try:
                game_html = first_html if game_id == first_game_id else request_html(source_url)
            except requests.RequestException as exc:
                print(f"[WARN] failed to fetch game {game_id}: {exc}")
                continue
            game_rows.append(
                build_game_basic_row(
                    match_row,
                    game_id=game_id,
                    game_number=game_number,
                    source_url=source_url,
                )
            )
            parsed_players = parse_game_players(
                game_html,
                match_row=match_row,
                game_id=game_id,
                game_number=game_number,
            )
            if len(parsed_players) != 10:
                print(f"[WARN] game {game_id} parsed player rows={len(parsed_players)}")
            player_rows.extend(parsed_players)
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)

    return {
        "events": [
            {
                "event_id": slugify(tournament_name),
                "event_name": tournament_name,
                "source": "gol.gg",
                "source_event_url": url,
                "fetched_at": now_text(),
            }
        ],
        "matches": match_rows,
        "games": game_rows,
        "players": player_rows,
    }


def crawl_all(
    tournaments: List[str],
    *,
    match_limit: int = DEFAULT_MATCH_LIMIT,
) -> Dict[str, List[Dict[str, Any]]]:
    all_events: List[Dict[str, Any]] = []
    all_matches: List[Dict[str, Any]] = []
    all_games: List[Dict[str, Any]] = []
    all_game_players: List[Dict[str, Any]] = []

    for tournament in tournaments:
        result = crawl_tournament(tournament, match_limit=match_limit)
        all_events.extend(result["events"])
        all_matches.extend(result["matches"])
        all_games.extend(result["games"])
        all_game_players.extend(result["players"])

    team_rows = []
    for match_row in all_matches:
        team_rows.extend(
            [
                {
                    "team_id": match_row["team1_id"],
                    "team_name": match_row["team1"],
                    "region": "",
                    "source": "gol.gg",
                    "fetched_at": now_text(),
                },
                {
                    "team_id": match_row["team2_id"],
                    "team_name": match_row["team2"],
                    "region": "",
                    "source": "gol.gg",
                    "fetched_at": now_text(),
                },
            ]
        )

    player_basic_rows = [
        {
            "player_id": row["player_id"],
            "player_name": row["player_name"],
            "team_id": row["team_id"],
            "team_name": row["team_name"],
            "role": "",
            "source": "gol.gg",
            "fetched_at": now_text(),
        }
        for row in all_game_players
    ]

    return {
        "event_basic": dedupe_rows(all_events, ["event_id"]),
        "team_basic": dedupe_rows(team_rows, ["team_id"]),
        "player_basic": dedupe_rows(player_basic_rows, ["player_id", "team_id"]),
        "match_result": dedupe_rows(all_matches, ["match_id"]),
        "game_basic": dedupe_rows(all_games, ["game_id"]),
        "game_player_stats": dedupe_rows(all_game_players, ["game_id", "stat_index"]),
    }


def crawl_gol_matchlists(tournaments: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    event_rows: List[Dict[str, Any]] = []
    team_rows: List[Dict[str, Any]] = []
    match_rows: List[Dict[str, Any]] = []

    for tournament in tournaments:
        url = tournament_matchlist_url(tournament)
        print(f"[gol-backfill] Fetch matchlist: {tournament}")
        try:
            rows = parse_matchlist(tournament, request_html(url))
        except requests.RequestException as exc:
            print(f"[WARN] failed backfill {tournament}: {exc}")
            continue

        league_slug = infer_league_slug(tournament)
        region = normalize_league_region(league_slug, "")
        event_rows.append(
            {
                "event_id": slugify(tournament),
                "event_name": tournament,
                "source": "gol.gg",
                "source_event_url": url,
                "fetched_at": now_text(),
            }
        )

        for row in rows:
            row["league_slug"] = league_slug
            row["status"] = "completed" if row.get("score1") is not None and row.get("score2") is not None else ""
            match_rows.append(row)
            team_rows.extend(
                [
                    {
                        "team_id": row["team1_id"],
                        "team_name": row["team1"],
                        "region": region,
                        "source": "gol.gg",
                        "fetched_at": now_text(),
                    },
                    {
                        "team_id": row["team2_id"],
                        "team_name": row["team2"],
                        "region": region,
                        "source": "gol.gg",
                        "fetched_at": now_text(),
                    },
                ]
            )

    return {
        "event_basic": dedupe_rows(event_rows, ["event_id"]),
        "team_basic": dedupe_rows(team_rows, ["team_id"]),
        "match_result": dedupe_rows(match_rows, ["match_id"]),
    }


def fetch_lolesports_leagues() -> List[Dict[str, Any]]:
    payload = request_lolesports("/getLeagues", {"hl": "en-US"})
    return payload.get("data", {}).get("leagues", []) or []


def fetch_lolesports_teams() -> List[Dict[str, Any]]:
    payload = request_lolesports("/getTeams", {"hl": "en-US"})
    return payload.get("data", {}).get("teams", []) or []


def fetch_schedule_page(league_id: str, page_token: str = "") -> Dict[str, Any]:
    params: Dict[str, Any] = {"hl": "en-US", "leagueId": league_id}
    if page_token:
        params["pageToken"] = page_token
    payload = request_lolesports("/getSchedule", params)
    return payload.get("data", {}).get("schedule", {}) or {}


def fetch_event_details(match_id: str) -> Optional[Dict[str, Any]]:
    clean_id = str(match_id or "").replace("lol_", "").strip()
    if not clean_id:
        return None
    payload = request_lolesports("/getEventDetails", {"hl": "en-US", "id": clean_id})
    return payload.get("data", {}).get("event") or None


def event_start_datetime(event: Dict[str, Any]) -> Optional[datetime]:
    return parse_iso_datetime(event.get("startTime"))


def collect_schedule_events(
    league: Dict[str, Any],
    *,
    start_dt: datetime,
    end_dt: datetime,
) -> List[Dict[str, Any]]:
    league_id = str(league.get("id") or "").strip()
    if not league_id:
        return []

    collected: Dict[str, Dict[str, Any]] = {}
    visited_tokens = set()

    def add_page(schedule: Dict[str, Any]) -> None:
        for event in schedule.get("events") or []:
            if event.get("type") != "match":
                continue
            dt = event_start_datetime(event)
            if not dt or dt < start_dt or dt > end_dt:
                continue
            match_id = str((event.get("match") or {}).get("id") or "").strip()
            if not match_id:
                continue
            collected[match_id] = event

    first = fetch_schedule_page(league_id)
    add_page(first)

    def walk(direction: str, stop_before_start: bool) -> None:
        token = (first.get("pages") or {}).get(direction)
        while token and token not in visited_tokens:
            visited_tokens.add(token)
            schedule = fetch_schedule_page(league_id, token)
            events = schedule.get("events") or []
            dates = [dt for dt in (event_start_datetime(event) for event in events) if dt]
            add_page(schedule)
            if dates:
                if stop_before_start and max(dates) < start_dt:
                    break
                if not stop_before_start and min(dates) > end_dt:
                    break
            token = (schedule.get("pages") or {}).get(direction)
            time.sleep(0.05)

    walk("older", True)
    walk("newer", False)
    return list(collected.values())


def lolesports_match_row(event: Dict[str, Any], league: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    match = event.get("match") or {}
    teams = match.get("teams") or []
    if len(teams) < 2:
        return None
    dt = event_start_datetime(event)
    if not dt:
        return None

    league_slug = str((event.get("league") or {}).get("slug") or league.get("slug") or "").strip()
    league_name = str((event.get("league") or {}).get("name") or league.get("name") or league_slug).strip()
    year = dt.year
    team1, team2 = teams[0], teams[1]
    result1 = team1.get("result") or {}
    result2 = team2.get("result") or {}
    score1 = result1.get("gameWins")
    score2 = result2.get("gameWins")
    state = str(event.get("state") or "").strip().lower()
    winner = ""
    if result1.get("outcome") == "win":
        winner = team1.get("name") or ""
    elif result2.get("outcome") == "win":
        winner = team2.get("name") or ""
    elif score1 is not None and score2 is not None:
        if int(score1 or 0) > int(score2 or 0):
            winner = team1.get("name") or ""
        elif int(score2 or 0) > int(score1 or 0):
            winner = team2.get("name") or ""
    if state == "completed" and not winner and int(score1 or 0) == 0 and int(score2 or 0) == 0:
        state = "unstarted"

    return {
        "match_id": f"lol_{match.get('id')}",
        "source": "lolesports",
        "source_match_url": f"https://lolesports.com/schedule?leagues={league_slug}",
        "first_game_id": "",
        "event_id": f"{league_slug}_{year}",
        "event_name": f"{league_name} {year}",
        "match_date": dt.date().isoformat(),
        "match_time": dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "league_slug": league_slug,
        "stage": str(event.get("blockName") or "").strip(),
        "patch": "",
        "team1_id": slugify(team1.get("name") or team1.get("code") or "team1"),
        "team1": team1.get("name") or "-",
        "team1_logo": normalize_image_url(team1.get("image")),
        "team2_id": slugify(team2.get("name") or team2.get("code") or "team2"),
        "team2": team2.get("name") or "-",
        "team2_logo": normalize_image_url(team2.get("image")),
        "score1": score1 if score1 is not None else None,
        "score2": score2 if score2 is not None else None,
        "winner": winner,
        "bo": (match.get("strategy") or {}).get("count"),
        "status": state,
        "fetched_at": now_text(),
    }


def crawl_lolesports_schedule(
    *,
    start_date: str = DEFAULT_SCHEDULE_START,
    future_months: int = DEFAULT_SCHEDULE_FUTURE_MONTHS,
    league_slugs: Optional[List[str]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.now(timezone.utc) + timedelta(days=max(0, future_months) * 31)
    target_slugs = league_slugs or [
        item.strip()
        for item in DEFAULT_SCHEDULE_LEAGUES.split(",")
        if item.strip()
    ]

    leagues = fetch_lolesports_leagues()
    league_by_slug = {str(row.get("slug") or ""): row for row in leagues}
    selected = [league_by_slug[slug] for slug in target_slugs if slug in league_by_slug]
    missing = [slug for slug in target_slugs if slug not in league_by_slug]
    if missing:
        print(f"[WARN] LoL Esports missing league slugs: {missing}")

    event_rows: List[Dict[str, Any]] = []
    team_rows: List[Dict[str, Any]] = []
    match_rows: List[Dict[str, Any]] = []

    for league in selected:
        slug = str(league.get("slug") or "")
        print(f"[lolesports] Fetch schedule: {slug}")
        try:
            events = collect_schedule_events(league, start_dt=start_dt, end_dt=end_dt)
        except requests.RequestException as exc:
            print(f"[WARN] failed schedule {slug}: {exc}")
            continue
        print(f"[lolesports] {slug}: {len(events)} matches")

        years = sorted({event_start_datetime(event).year for event in events if event_start_datetime(event)})
        for year in years:
            event_rows.append(
                {
                    "event_id": f"{slug}_{year}",
                    "event_name": f"{league.get('name') or slug} {year}",
                    "source": "lolesports",
                    "source_event_url": f"https://lolesports.com/schedule?leagues={slug}",
                    "fetched_at": now_text(),
                }
            )

        for event in events:
            row = lolesports_match_row(event, league)
            if not row:
                continue
            match_rows.append(row)
            region = normalize_league_region(slug, str(league.get("region") or ""))
            team_rows.extend(
                [
                    {
                        "team_id": row["team1_id"],
                        "team_name": row["team1"],
                        "region": region,
                        "team_logo": row.get("team1_logo") or "",
                        "source": "lolesports",
                        "fetched_at": now_text(),
                    },
                    {
                        "team_id": row["team2_id"],
                        "team_name": row["team2"],
                        "region": region,
                        "team_logo": row.get("team2_logo") or "",
                        "source": "lolesports",
                        "fetched_at": now_text(),
                    },
                ]
            )
        time.sleep(0.1)

    return {
        "event_basic": dedupe_rows(event_rows, ["event_id"]),
        "team_basic": dedupe_rows(team_rows, ["team_id"]),
        "match_result": dedupe_rows(match_rows, ["match_id"]),
    }


def crawl_lolesports_rosters(team_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    wanted_team_ids = {str(row.get("team_id") or "").strip() for row in team_rows}
    wanted_team_names = {slugify(str(row.get("team_name") or "")) for row in team_rows}
    wanted_team_ids.discard("")
    wanted_team_names.discard("")

    roster_rows: List[Dict[str, Any]] = []
    teams: List[Dict[str, Any]] = []
    for attempt in range(1, 4):
        try:
            teams = fetch_lolesports_teams()
            break
        except requests.RequestException as exc:
            print(f"[WARN] failed LoL Esports rosters attempt {attempt}/3: {exc}")
            time.sleep(attempt)
    if not teams:
        return roster_rows

    for team in teams:
        team_name = str(team.get("name") or "").strip()
        team_id = slugify(team_name or team.get("slug") or team.get("code") or "")
        if wanted_team_ids and team_id not in wanted_team_ids and slugify(team_name) not in wanted_team_names:
            continue
        for player in select_lolesports_roster_players(team):
            summoner_name = str(player.get("summonerName") or "").strip()
            player_name = player_name_from_summoner(summoner_name, team.get("code"))
            if not player_name:
                continue
            roster_rows.append(
                {
                    "player_id": slugify(player_name),
                    "player_name": player_name,
                    "team_id": team_id,
                    "team_name": team_name,
                    "role": normalize_role(player.get("role")),
                    "avatar": normalize_image_url(player.get("image")),
                    "source": "lolesports",
                    "fetched_at": now_text(),
                }
            )

    print(f"[lolesports] roster players: {len(roster_rows)}")
    return dedupe_rows(roster_rows, ["player_id", "team_id"])


def update_match_row_from_event_details(row: Dict[str, Any], event: Dict[str, Any]) -> Dict[str, Any]:
    match = event.get("match") or {}
    teams = match.get("teams") or []
    if len(teams) >= 2:
        result1 = teams[0].get("result") or {}
        result2 = teams[1].get("result") or {}
        score1 = result1.get("gameWins")
        score2 = result2.get("gameWins")
        if score1 is not None:
            row["score1"] = score1
        if score2 is not None:
            row["score2"] = score2
        if result1.get("outcome") == "win":
            row["winner"] = teams[0].get("name") or row.get("team1") or ""
        elif result2.get("outcome") == "win":
            row["winner"] = teams[1].get("name") or row.get("team2") or ""
        elif score1 is not None and score2 is not None:
            if int(score1 or 0) > int(score2 or 0):
                row["winner"] = teams[0].get("name") or row.get("team1") or ""
            elif int(score2 or 0) > int(score1 or 0):
                row["winner"] = teams[1].get("name") or row.get("team2") or ""

    games = match.get("games") or []
    states = {str(game.get("state") or "").strip().lower() for game in games}
    score_total = int(row.get("score1") or 0) + int(row.get("score2") or 0)
    if "in_game" in states or "inprogress" in states:
        row["status"] = "inProgress"
    elif states and states <= {"unstarted"} and score_total == 0 and not row.get("winner"):
        row["status"] = "unstarted"
    elif row.get("winner") or score_total > 0 or (states and states <= {"completed"}):
        row["status"] = "completed"
    return row


def game_rows_from_event_details(row: Dict[str, Any], event: Dict[str, Any]) -> List[Dict[str, Any]]:
    match = event.get("match") or {}
    games = match.get("games") or []
    teams_by_esports_id = {
        str(team.get("id") or ""): team
        for team in match.get("teams") or []
    }
    rows: List[Dict[str, Any]] = []
    for game in games:
        game_id = str(game.get("id") or "").strip()
        if not game_id:
            continue
        state = str(game.get("state") or "").strip().lower()
        if state in {"unneeded", "unstarted"}:
            continue
        side_team_ids = {
            str(team.get("side") or "").strip().lower(): str(team.get("id") or "").strip()
            for team in game.get("teams") or []
        }
        blue_team = teams_by_esports_id.get(side_team_ids.get("blue", ""), {})
        red_team = teams_by_esports_id.get(side_team_ids.get("red", ""), {})
        blue_name = blue_team.get("name") or row["team1"]
        red_name = red_team.get("name") or row["team2"]
        rows.append(
            {
                "game_id": game_id,
                "match_id": row["match_id"],
                "event_id": row["event_id"],
                "event_name": row["event_name"],
                "game_number": game.get("number"),
                "match_date": row["match_date"],
                "stage": row["stage"],
                "patch": row.get("patch") or "",
                "team1_id": slugify(blue_name),
                "team1": blue_name,
                "team2_id": slugify(red_name),
                "team2": red_name,
                "source_game_url": f"https://lolesports.com/schedule?leagues={row.get('league_slug') or ''}",
                "fetched_at": now_text(),
            }
        )
    return rows


def frame_score(frame: Dict[str, Any]) -> int:
    participants = frame_participants(frame)
    score = frame_stat_score(frame) * 100
    for participant in participants:
        score += parse_int(participant.get("level")) or 0
    return score


def frame_stat_score(frame: Dict[str, Any]) -> int:
    participants = frame_participants(frame)
    score = 0
    for participant in participants:
        score += parse_int(participant.get("kills")) or 0
        score += parse_int(participant.get("deaths")) or 0
        score += parse_int(participant.get("assists")) or 0
        score += parse_int(participant.get("creepScore")) or 0
    return score


def frame_participants(frame: Dict[str, Any]) -> List[Dict[str, Any]]:
    participants = frame.get("participants") or []
    if participants:
        return participants
    nested: List[Dict[str, Any]] = []
    for team_key in ("blueTeam", "redTeam"):
        nested.extend((frame.get(team_key) or {}).get("participants") or [])
    return nested


def participant_stats_by_id(frame: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    out: Dict[int, Dict[str, Any]] = {}
    for participant in frame_participants(frame):
        participant_id = parse_int(participant.get("participantId"))
        if participant_id is not None:
            out[participant_id] = participant
    return out


def rounded_livestats_time(value: datetime) -> str:
    rounded = value.astimezone(timezone.utc).replace(second=0, microsecond=0)
    rounded = rounded.replace(minute=(rounded.minute // 10) * 10)
    return rounded.isoformat().replace("+00:00", "Z")


def live_stats_rows_from_payload(
    payload: Dict[str, Any],
    game_row: Dict[str, Any],
    match_row: Dict[str, Any],
    frame: Dict[str, Any],
) -> List[Dict[str, Any]]:
    metadata = payload.get("gameMetadata") or {}
    frame_players = participant_stats_by_id(frame)
    rows: List[Dict[str, Any]] = []
    for side_key, team_side, team_id, team_name in (
        ("blueTeamMetadata", "blue", game_row.get("team1_id") or match_row["team1_id"], game_row.get("team1") or match_row["team1"]),
        ("redTeamMetadata", "red", game_row.get("team2_id") or match_row["team2_id"], game_row.get("team2") or match_row["team2"]),
    ):
        participant_metadata = (metadata.get(side_key) or {}).get("participantMetadata") or []
        team_prefix = common_summoner_prefix(participant_metadata)
        for participant in participant_metadata:
            participant_id = parse_int(participant.get("participantId"))
            if participant_id is None:
                continue
            stats = frame_players.get(participant_id, {})
            display_name = player_name_from_summoner(participant.get("summonerName"), team_prefix)
            rows.append(
                {
                    "game_id": game_row["game_id"],
                    "match_id": match_row["match_id"],
                    "event_id": match_row["event_id"],
                    "event_name": match_row["event_name"],
                    "game_number": game_row.get("game_number"),
                    "team_side": team_side,
                    "team_id": team_id,
                    "team_name": team_name,
                    "player_id": slugify(display_name),
                    "player_name": display_name,
                    "champion": participant.get("championId") or "",
                    "kills": parse_int(stats.get("kills")),
                    "deaths": parse_int(stats.get("deaths")),
                    "assists": parse_int(stats.get("assists")),
                    "kda_text": f"{parse_int(stats.get('kills')) or 0}/{parse_int(stats.get('deaths')) or 0}/{parse_int(stats.get('assists')) or 0}",
                    "cs": parse_int(stats.get("creepScore")),
                    "stat_index": participant_id,
                    "source_player_url": "",
                    "fetched_at": now_text(),
                }
            )
    return rows


def is_complete_livestats_rows(rows: List[Dict[str, Any]]) -> bool:
    if len(rows) != 10:
        return False
    side_counts: Dict[str, int] = defaultdict(int)
    for row in rows:
        side_counts[str(row.get("team_side") or "")] += 1
    return side_counts.get("blue") == 5 and side_counts.get("red") == 5


def livestsats_player_rows(game_row: Dict[str, Any], match_row: Dict[str, Any]) -> List[Dict[str, Any]]:
    try:
        payload = request_livestats(f"/window/{game_row['game_id']}")
    except requests.RequestException:
        return []

    frames = payload.get("frames") or []
    if not frames:
        return []

    candidate_frames = list(frames)
    best_frame = max(candidate_frames, key=frame_score)
    if frame_stat_score(best_frame) <= 0:
        first_dt = parse_iso_datetime(frames[0].get("rfc460Timestamp"))
        if first_dt:
            for offset in livestats_detail_offsets():
                detail_time = rounded_livestats_time(first_dt + timedelta(minutes=offset))
                try:
                    details = request_livestats(f"/details/{game_row['game_id']}?startingTime={quote(detail_time)}")
                    candidate_frames.extend(details.get("frames") or [])
                except requests.RequestException:
                    continue
            best_frame = max(candidate_frames, key=frame_score)

    rows = live_stats_rows_from_payload(payload, game_row, match_row, best_frame)
    if not is_complete_livestats_rows(rows):
        return []
    return rows


def should_fetch_livestats(game_row: Dict[str, Any], match_row: Dict[str, Any]) -> bool:
    status = str(match_row.get("status") or "").strip()
    if status in {"completed", "inProgress"}:
        return True
    if int(match_row.get("score1") or 0) + int(match_row.get("score2") or 0) > 0:
        return True
    match_dt = parse_row_datetime(match_row)
    return bool(match_dt and match_dt <= datetime.now(timezone.utc) and status != "unstarted")


def live_stats_sort_key(game_row: Dict[str, Any], match_by_id: Dict[str, Dict[str, Any]]) -> Tuple[float, int, str]:
    match_row = match_by_id.get(str(game_row.get("match_id") or ""), {})
    match_dt = parse_row_datetime(match_row)
    timestamp = match_dt.timestamp() if match_dt else 0.0
    return (timestamp, parse_int(game_row.get("game_number")) or 0, str(game_row.get("game_id") or ""))


def enrich_lolesports_details(match_rows: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    if not DEFAULT_EVENT_DETAILS_ENABLED:
        return {"match_result": match_rows, "game_basic": [], "game_player_stats": []}

    target_rows = [row for row in match_rows if str(row.get("source") or "") == "lolesports"]
    if not target_rows:
        return {"match_result": match_rows, "game_basic": [], "game_player_stats": []}

    updated_by_id: Dict[str, Dict[str, Any]] = {}
    game_rows: List[Dict[str, Any]] = []
    event_by_match: Dict[str, Dict[str, Any]] = {}

    print(f"[lolesports] Fetch event details: {len(target_rows)} matches")
    workers = max(1, DEFAULT_EVENT_DETAILS_WORKERS)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {
            executor.submit(fetch_event_details, row["match_id"]): row
            for row in target_rows
        }
        for idx, future in enumerate(as_completed(future_map), start=1):
            row = dict(future_map[future])
            try:
                event = future.result()
            except requests.RequestException as exc:
                print(f"[WARN] failed event details {row.get('match_id')}: {exc}")
                continue
            if not event:
                continue
            updated = update_match_row_from_event_details(row, event)
            updated_by_id[updated["match_id"]] = updated
            event_by_match[updated["match_id"]] = event
            game_rows.extend(game_rows_from_event_details(updated, event))
            if idx % 500 == 0:
                print(f"[lolesports] event details: {idx}/{len(target_rows)}")

    merged_matches = [updated_by_id.get(row.get("match_id"), row) for row in match_rows]
    player_rows: List[Dict[str, Any]] = []
    if DEFAULT_LIVESTATS_ENABLED and game_rows:
        match_by_id = {row["match_id"]: row for row in merged_matches}
        livestsats_targets = [
            game
            for game in game_rows
            if game.get("match_id") in match_by_id and should_fetch_livestats(game, match_by_id[game["match_id"]])
        ]
        livestsats_targets = sorted(
            livestsats_targets,
            key=lambda item: live_stats_sort_key(item, match_by_id),
            reverse=True,
        )
        if DEFAULT_LIVESTATS_LIMIT > 0:
            livestsats_targets = livestsats_targets[:DEFAULT_LIVESTATS_LIMIT]
        print(f"[lolesports] Fetch live stats windows: {len(livestsats_targets)} games")
        live_stats_empty = 0
        live_stats_incomplete = 0
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_map = {
                executor.submit(livestsats_player_rows, game, match_by_id[game["match_id"]]): game
                for game in livestsats_targets
                if game.get("match_id") in match_by_id
            }
            for idx, future in enumerate(as_completed(future_map), start=1):
                try:
                    rows = future.result()
                    if not rows:
                        live_stats_empty += 1
                    elif len(rows) != 10:
                        live_stats_incomplete += 1
                    player_rows.extend(rows)
                except Exception as exc:
                    game = future_map[future]
                    print(f"[WARN] failed live stats {game.get('game_id')}: {exc}")
                if idx % 100 == 0:
                    print(f"[lolesports] live stats: {idx}/{len(future_map)}")
        print(
            "[lolesports] live stats summary: "
            f"games={len(livestsats_targets)} player_rows={len(player_rows)} "
            f"empty_games={live_stats_empty} incomplete_games={live_stats_incomplete}"
        )

    return {
        "match_result": dedupe_rows(merged_matches, ["match_id"]),
        "game_basic": dedupe_rows(game_rows, ["game_id"]),
        "game_player_stats": dedupe_rows(player_rows, ["game_id", "stat_index"]),
    }


def merge_schedule_rows(base: Dict[str, List[Dict[str, Any]]], schedule: Dict[str, List[Dict[str, Any]]]) -> Dict[str, List[Dict[str, Any]]]:
    merged = {key: list(value) for key, value in base.items()}
    merged["event_basic"] = dedupe_rows(
        [*schedule.get("event_basic", []), *merged.get("event_basic", [])],
        ["event_id"],
    )
    merged["team_basic"] = dedupe_rows(
        [*schedule.get("team_basic", []), *merged.get("team_basic", [])],
        ["team_id"],
    )
    merged["match_result"] = dedupe_rows(
        [*schedule.get("match_result", []), *merged.get("match_result", [])],
        ["match_id"],
    )
    return merged


def append_supplemental_rows(base: Dict[str, List[Dict[str, Any]]], supplemental: Dict[str, List[Dict[str, Any]]]) -> Dict[str, List[Dict[str, Any]]]:
    merged = {key: list(value) for key, value in base.items()}
    merged["event_basic"] = dedupe_rows(
        [*merged.get("event_basic", []), *supplemental.get("event_basic", [])],
        ["event_id"],
    )
    merged["team_basic"] = dedupe_rows(
        [*merged.get("team_basic", []), *supplemental.get("team_basic", [])],
        ["team_id"],
    )
    merged["match_result"] = dedupe_rows(
        [*merged.get("match_result", []), *supplemental.get("match_result", [])],
        ["match_id"],
    )
    return merged


def main() -> int:
    tournaments = [item.strip() for item in DEFAULT_TOURNAMENTS.split(",") if item.strip()]
    backfill_tournaments = [item.strip() for item in DEFAULT_GOL_BACKFILL_TOURNAMENTS.split(",") if item.strip()]
    if DEFAULT_GOL_ENABLED and not tournaments:
        raise SystemExit("No tournaments configured. Set LOL_TOURNAMENTS.")

    rows = (
        crawl_all(tournaments, match_limit=DEFAULT_MATCH_LIMIT)
        if DEFAULT_GOL_ENABLED
        else {
            "event_basic": [],
            "team_basic": [],
            "player_basic": [],
            "match_result": [],
            "game_basic": [],
            "game_player_stats": [],
        }
    )
    schedule_rows = crawl_lolesports_schedule()
    detail_rows = enrich_lolesports_details(schedule_rows.get("match_result", []))
    schedule_rows["match_result"] = detail_rows.get("match_result", schedule_rows.get("match_result", []))
    rows = merge_schedule_rows(rows, schedule_rows)
    rows["game_basic"] = dedupe_rows(
        [*rows.get("game_basic", []), *detail_rows.get("game_basic", [])],
        ["game_id"],
    )
    rows["game_player_stats"] = dedupe_rows(
        [*rows.get("game_player_stats", []), *detail_rows.get("game_player_stats", [])],
        ["game_id", "stat_index"],
    )
    if DEFAULT_GOL_ENABLED and backfill_tournaments:
        backfill_rows = crawl_gol_matchlists(backfill_tournaments)
        rows = append_supplemental_rows(rows, backfill_rows)
    roster_rows = crawl_lolesports_rosters(rows.get("team_basic", []))
    rows["player_basic"] = merge_player_basic_rows(
        [*rows.get("player_basic", []), *roster_rows],
        rows.get("game_player_stats", []),
    )
    write_csv(
        LOL_DATA_DIR / "lol_event_basic.csv",
        rows["event_basic"],
        ["event_id", "event_name", "source", "source_event_url", "fetched_at"],
    )
    write_csv(
        LOL_DATA_DIR / "lol_team_basic.csv",
        rows["team_basic"],
        ["team_id", "team_name", "region", "team_logo", "source", "fetched_at"],
    )
    write_csv(
        LOL_DATA_DIR / "lol_player_basic.csv",
        rows["player_basic"],
        ["player_id", "player_name", "team_id", "team_name", "role", "avatar", "source", "fetched_at"],
    )
    write_csv(
        LOL_DATA_DIR / "lol_match_result.csv",
        rows["match_result"],
        [
            "match_id",
            "source",
            "source_match_url",
            "first_game_id",
            "event_id",
            "event_name",
            "match_date",
            "match_time",
            "league_slug",
            "stage",
            "patch",
            "team1_id",
            "team1",
            "team1_logo",
            "team2_id",
            "team2",
            "team2_logo",
            "score1",
            "score2",
            "winner",
            "bo",
            "status",
            "fetched_at",
        ],
    )
    write_csv(
        LOL_DATA_DIR / "lol_game_basic.csv",
        rows["game_basic"],
        [
            "game_id",
            "match_id",
            "event_id",
            "event_name",
            "game_number",
            "match_date",
            "stage",
            "patch",
            "team1_id",
            "team1",
            "team2_id",
            "team2",
            "source_game_url",
            "fetched_at",
        ],
    )
    write_csv(
        LOL_DATA_DIR / "lol_game_player_stats.csv",
        rows["game_player_stats"],
        [
            "game_id",
            "match_id",
            "event_id",
            "event_name",
            "game_number",
            "team_side",
            "team_id",
            "team_name",
            "player_id",
            "player_name",
            "champion",
            "kills",
            "deaths",
            "assists",
            "kda_text",
            "cs",
            "stat_index",
            "source_player_url",
            "fetched_at",
        ],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
