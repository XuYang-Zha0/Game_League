from __future__ import annotations

import os
import re
import time
import math
from collections import defaultdict
from datetime import datetime
from html import unescape
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import quote_plus

import pymysql
import requests
from fastapi import APIRouter, Query
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from counter_strike.cs_api_server import (
    LIVE_HEADERS,
    format_metric,
    get_conn,
    json_row,
    parse_datetime_text,
    safe_datetime,
    safe_float,
    safe_int,
    table_columns,
)


router = APIRouter()

def lol_table_exists(cur: pymysql.cursors.DictCursor, table_name: str) -> bool:
    cur.execute("SHOW TABLES LIKE %s", (table_name,))
    return cur.fetchone() is not None


def lol_fetch_all(cur: pymysql.cursors.DictCursor, sql: str, params: Tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
    cur.execute(sql, params)
    return [json_row(row) for row in cur.fetchall()]


def lol_active_player_sql(cur: pymysql.cursors.DictCursor, alias: str = "pb", prefix: str = "AND") -> str:
    if not lol_table_exists(cur, "lol_player_basic"):
        return ""
    try:
        if "is_active" not in table_columns(cur, "lol_player_basic"):
            return ""
    except Exception:
        return ""
    return f" {prefix} COALESCE({alias}.is_active, 1) = 1 "


LOL_TEAM_REGION_BY_ID = {
    "t1": "LCK",
    "gen_g_esports": "LCK",
    "hanwha_life_esports": "LCK",
    "kt_rolster": "LCK",
    "bilibili_gaming": "LPL",
    "top_esports": "LPL",
    "anyone_s_legend": "LPL",
    "g2_esports": "LEC",
    "fnatic": "LEC",
    "movistar_koi": "LEC",
    "100_thieves": "LTA",
    "flyquest": "LTA",
    "vivo_keyd_stars": "LTA",
    "ctbc_flying_oyster": "LCP",
    "psg_talon": "LCP",
    "team_secret_whales": "LCP",
}

LOL_ROLE_BY_INDEX = {
    1: "TOP",
    2: "JUG",
    3: "MID",
    4: "BOT",
    5: "SUP",
}
LOL_ESPORTS_API_KEY = os.getenv("LOL_ESPORTS_API_KEY", "0TvQnueqKa5mxJntVWt0w4LpLfEkrV1Ta8rQBb9Z")
LOL_ESPORTS_API_BASE = "https://esports-api.lolesports.com/persisted/gw"
LOL_EXTERNAL_ASSETS_ENABLED = os.getenv("LOL_EXTERNAL_ASSETS_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
LOL_EXTERNAL_CAREER_ENABLED = os.getenv("LOL_EXTERNAL_CAREER_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
LOL_CAREER_TEAM_MIN_GAMES = int(os.getenv("LOL_CAREER_TEAM_MIN_GAMES", "10"))
LOL_ESPORTS_ASSET_TTL_SECONDS = 30 * 60
LOL_ESPORTS_ASSET_CACHE: Dict[str, Any] = {
    "expiresAt": 0.0,
    "teamsBySlug": {},
    "teamsByName": {},
    "playersByName": {},
    "playersById": {},
    "playersByTeamAndName": {},
    "playersByTeamAndId": {},
}
LOL_GOL_BASE_URL = "https://gol.gg"
LOL_GOL_CAREER_TTL_SECONDS = 6 * 60 * 60
LOL_GOL_CAREER_CACHE: Dict[str, Dict[str, Any]] = {}
LOL_GOL_TEAM_ALIASES = {
    "t1": {"t1", "skt", "skt t1", "skt t1 k", "sk telecom t1", "sk telecom t1 k"},
    "sktelecomt1": {"t1", "skt", "skt t1", "skt t1 k", "sk telecom t1", "sk telecom t1 k"},
}
LOL_PLAYER_AVATAR_OVERRIDES = {
    "light": "https://liquipedia.net/commons/images/b/bd/WBG_Light_Worlds_2024.jpg",
    "crisp": "https://liquipedia.net/commons/images/2/2d/TES_Crisp_First_Stand_2025.jpg",
    "yagao": "https://liquipedia.net/commons/images/d/d2/BLG_Yagao_Worlds_2023.jpg",
}


def infer_lol_region(team_id: Any = "", team_name: Any = "", event_name: Any = "") -> str:
    team_key = str(team_id or "").strip().lower()
    if team_key in LOL_TEAM_REGION_BY_ID:
        return LOL_TEAM_REGION_BY_ID[team_key]

    name_key = re.sub(r"[^a-z0-9]+", "_", str(team_name or "").strip().lower()).strip("_")
    if name_key in LOL_TEAM_REGION_BY_ID:
        return LOL_TEAM_REGION_BY_ID[name_key]

    event_text = str(event_name or "").upper()
    for region in ("LCK", "LPL", "LEC", "LTA", "LCP", "VCS", "PCS", "CBLOL", "LLA"):
        if region in event_text:
            return region
    if "WORLD" in event_text or "MSI" in event_text:
        return "International"
    return "Unknown"


def normalize_lol_region(value: Any) -> str:
    text = str(value or "").strip()
    upper = text.upper()
    mapping = {
        "LCK": "LCK",
        "LPL": "LPL",
        "LEC": "LEC",
        "LES": "LEC",
        "LTA": "LTA",
        "LCS": "LTA",
        "CBLOL": "LTA",
        "LLA": "LTA",
        "LCP": "LCP",
        "VCS": "VCS",
        "PCS": "PCS",
        "WORLD": "International",
        "WORLDS": "International",
        "MSI": "International",
        "INTERNATIONAL": "International",
    }
    if upper in mapping:
        return mapping[upper]
    for needle, region in mapping.items():
        if needle in upper:
            return region
    return text if text else "Unknown"


def resolve_lol_team_region(team_id: Any = "", team_name: Any = "", db_region: Any = "", event_name: Any = "") -> str:
    asset_region = normalize_lol_region(lol_team_asset(team_id, team_name).get("region"))
    if asset_region and asset_region not in {"Unknown", "International"}:
        return asset_region
    db_value = normalize_lol_region(db_region)
    if db_value and db_value not in {"Unknown", "International"}:
        return db_value
    inferred = infer_lol_region(team_id, team_name, event_name)
    if inferred != "Unknown":
        return inferred
    return db_value or asset_region or "Unknown"


def normalize_lol_role(value: Any) -> str:
    role = str(value or "").strip().upper()
    mapping = {
        "JUNGLE": "JUG",
        "JUNGLER": "JUG",
        "BOTTOM": "BOT",
        "ADC": "BOT",
        "SUPPORT": "SUP",
    }
    return mapping.get(role, role or "-")


def clamp_float(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


LOL_ELO_BASE = 1500.0
LOL_ELO_SEASON_REGRESSION = 0.78
LOL_ELO_REGION_WEIGHT = 0.18


def lol_expected_score(rating: float, opponent_rating: float) -> float:
    return 1.0 / (1.0 + math.pow(10.0, (opponent_rating - rating) / 400.0))


def lol_match_k_factor(match: Dict[str, Any]) -> float:
    league_slug = str(match.get("league_slug") or "").strip().lower()
    event_name = str(match.get("event_name") or "").strip().lower()
    stage = str(match.get("stage") or "").strip().lower()
    if league_slug in {"worlds", "msi", "first_stand"} or "world" in event_name or "msi" in event_name:
        base = 42.0
    elif "playoff" in stage or "final" in stage:
        base = 32.0
    else:
        base = 24.0
    score1 = safe_int(match.get("score1"))
    score2 = safe_int(match.get("score2"))
    games = max(1, score1 + score2)
    margin = abs(score1 - score2)
    multiplier = 1.0 + max(0, margin - 1) * 0.10 + max(0, games - 1) * 0.025
    return base * multiplier


def lol_match_actual_score(match: Dict[str, Any], team_name: Any, own_score: int, opp_score: int) -> float:
    winner = str(match.get("winner") or "").strip()
    if winner:
        return 1.0 if winner == str(team_name or "").strip() else 0.0
    if own_score > opp_score:
        return 1.0
    if own_score < opp_score:
        return 0.0
    return 0.5


def lol_regress_rating(value: float, factor: float = LOL_ELO_SEASON_REGRESSION) -> float:
    return LOL_ELO_BASE + (value - LOL_ELO_BASE) * factor


def build_lol_power_ratings(cur: pymysql.cursors.DictCursor) -> Dict[str, Dict[str, Any]]:
    rows = lol_fetch_all(
        cur,
        """
        SELECT
            match_id,
            match_time,
            match_date,
            league_slug,
            event_name,
            stage,
            team1_id,
            team1,
            team2_id,
            team2,
            score1,
            score2,
            winner
        FROM lol_match_result
        WHERE score1 IS NOT NULL
          AND score2 IS NOT NULL
          AND score1 + score2 > 0
          AND LOWER(COALESCE(status, '')) IN ('completed', 'finished')
        ORDER BY COALESCE(match_time, CAST(match_date AS DATETIME)), match_id
        """,
    )
    ratings: Dict[str, float] = defaultdict(lambda: LOL_ELO_BASE)
    region_ratings: Dict[str, float] = defaultdict(lambda: LOL_ELO_BASE)
    stats: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {
            "matches": 0,
            "wins": 0,
            "game_wins": 0,
            "game_losses": 0,
            "lastMatch": "",
            "region": "Unknown",
        }
    )
    current_year: Optional[int] = None

    for match in rows:
        team1_id = str(match.get("team1_id") or "").strip()
        team2_id = str(match.get("team2_id") or "").strip()
        if not team1_id or not team2_id or team1_id == team2_id:
            continue
        match_dt = parse_datetime_text(match.get("match_time")) or parse_datetime_text(match.get("match_date"))
        match_year = match_dt.year if match_dt else None
        if match_year and current_year and match_year != current_year:
            for key in list(ratings.keys()):
                ratings[key] = lol_regress_rating(ratings[key])
            for key in list(region_ratings.keys()):
                region_ratings[key] = lol_regress_rating(region_ratings[key], 0.86)
        if match_year:
            current_year = match_year

        score1 = safe_int(match.get("score1"))
        score2 = safe_int(match.get("score2"))
        team1_name = match.get("team1")
        team2_name = match.get("team2")
        region1 = resolve_lol_team_region(team1_id, team1_name, event_name=match.get("event_name"))
        region2 = resolve_lol_team_region(team2_id, team2_name, event_name=match.get("event_name"))
        rating1 = ratings[team1_id]
        rating2 = ratings[team2_id]
        actual1 = lol_match_actual_score(match, team1_name, score1, score2)
        expected1 = lol_expected_score(rating1, rating2)
        delta = lol_match_k_factor(match) * (actual1 - expected1)
        ratings[team1_id] = rating1 + delta
        ratings[team2_id] = rating2 - delta

        if region1 and region2 and region1 != region2 and "Unknown" not in {region1, region2}:
            expected_region = lol_expected_score(region_ratings[region1], region_ratings[region2])
            region_delta = lol_match_k_factor(match) * 0.16 * (actual1 - expected_region)
            region_ratings[region1] += region_delta
            region_ratings[region2] -= region_delta

        for own_id, own_name, region, own_score, opp_score, actual in (
            (team1_id, team1_name, region1, score1, score2, actual1),
            (team2_id, team2_name, region2, score2, score1, 1.0 - actual1),
        ):
            item = stats[own_id]
            item["matches"] += 1
            item["wins"] += 1 if actual == 1.0 else 0
            item["game_wins"] += own_score
            item["game_losses"] += opp_score
            item["lastMatch"] = str(match.get("match_time") or match.get("match_date") or item.get("lastMatch") or "")
            item["region"] = region

    output: Dict[str, Dict[str, Any]] = {}
    for team_id, rating in ratings.items():
        item = stats[team_id]
        region = str(item.get("region") or "Unknown")
        region_rating = region_ratings[region]
        matches = safe_int(item.get("matches"))
        confidence = clamp_float((matches / 20) ** 0.45) if matches else 0.0
        blended = (1 - LOL_ELO_REGION_WEIGHT) * rating + LOL_ELO_REGION_WEIGHT * region_rating
        power = LOL_ELO_BASE + (blended - LOL_ELO_BASE) * (0.35 + 0.65 * confidence)
        output[team_id] = {
            **item,
            "elo": round(rating, 2),
            "regionElo": round(region_rating, 2),
            "powerRating": round(power, 2),
            "confidence": round(confidence, 4),
        }
    return output


def lol_role_from_stat_index(value: Any) -> str:
    idx = safe_int(value)
    if idx > 5:
        idx -= 5
    return LOL_ROLE_BY_INDEX.get(idx, "-")


def lol_asset_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def lol_slug_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")


def normalize_lol_image(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("//"):
        return f"https:{text}"
    if text.startswith("http://"):
        return "https://" + text[len("http://") :]
    return text


def fetch_lol_esports_assets(force: bool = False) -> Dict[str, Dict[str, Dict[str, Any]]]:
    if not LOL_EXTERNAL_ASSETS_ENABLED:
        return LOL_ESPORTS_ASSET_CACHE

    now = time.time()
    if not force and LOL_ESPORTS_ASSET_CACHE.get("expiresAt", 0) > now:
        return LOL_ESPORTS_ASSET_CACHE

    teams_by_slug: Dict[str, Dict[str, Any]] = {}
    teams_by_name: Dict[str, Dict[str, Any]] = {}
    players_by_name: Dict[str, Dict[str, Any]] = {}
    players_by_id: Dict[str, Dict[str, Any]] = {}
    players_by_team_and_name: Dict[str, Dict[str, Any]] = {}
    players_by_team_and_id: Dict[str, Dict[str, Any]] = {}

    try:
        response = requests.get(
            f"{LOL_ESPORTS_API_BASE}/getTeams",
            params={"hl": "en-US"},
            headers={
                "Accept": "application/json",
                "User-Agent": LIVE_HEADERS["User-Agent"],
                "x-api-key": LOL_ESPORTS_API_KEY,
            },
            timeout=(5, 20),
        )
        response.raise_for_status()
        teams = response.json().get("data", {}).get("teams", [])
    except Exception:
        LOL_ESPORTS_ASSET_CACHE["expiresAt"] = now + 120
        return LOL_ESPORTS_ASSET_CACHE

    for team in teams:
        slug = str(team.get("slug") or "").strip()
        name = str(team.get("name") or "").strip()
        home_league = team.get("homeLeague") or {}
        team_item = {
            "slug": slug,
            "name": name,
            "code": team.get("code") or "",
            "image": normalize_lol_image(team.get("image") or team.get("alternativeImage")),
            "region": home_league.get("name") or home_league.get("region") or "",
            "status": team.get("status") or "",
        }
        for key in {lol_slug_key(slug), lol_slug_key(name), lol_asset_key(name), lol_asset_key(team.get("code"))}:
            if key and (team_item["status"] == "active" or key not in teams_by_slug):
                teams_by_slug[key] = team_item
        name_key = lol_asset_key(name)
        if name_key and (team_item["status"] == "active" or name_key not in teams_by_name):
            teams_by_name[name_key] = team_item

        team_aliases = {lol_slug_key(slug), lol_slug_key(name), lol_asset_key(name), lol_asset_key(team.get("code"))}
        for player in team.get("players") or []:
            summoner_name = str(player.get("summonerName") or "").strip()
            display_name = str(player.get("name") or "").strip()
            first_name = str(player.get("firstName") or "").strip()
            last_name = str(player.get("lastName") or "").strip()
            full_name = " ".join([part for part in (first_name, last_name) if part]).strip()
            name_candidates = []
            for candidate in (summoner_name, display_name, full_name):
                if candidate and candidate not in name_candidates:
                    name_candidates.append(candidate)
            if not name_candidates:
                continue
            player_id = str(player.get("id") or "").strip()
            player_item = {
                "id": player_id,
                "name": summoner_name or display_name or full_name,
                "avatar": normalize_lol_image(player.get("image")),
                "role": str(player.get("role") or "").upper(),
                "teamSlug": slug,
                "teamName": name,
            }
            player_name_keys = []
            for player_name in name_candidates:
                player_key = lol_asset_key(player_name)
                if player_key and player_key not in player_name_keys:
                    player_name_keys.append(player_key)
            for player_key in player_name_keys:
                if team_item["status"] == "active" or player_key not in players_by_name:
                    players_by_name[player_key] = player_item
            id_keys = [player_id] if player_id else []
            normalized_player_id = lol_asset_key(player_id)
            if normalized_player_id and normalized_player_id not in id_keys:
                id_keys.append(normalized_player_id)
            for id_key in id_keys:
                if id_key and (team_item["status"] == "active" or id_key not in players_by_id):
                    players_by_id[id_key] = player_item
            for team_key in team_aliases:
                for player_key in player_name_keys:
                    if team_key and player_key:
                        players_by_team_and_name[f"{team_key}:{player_key}"] = player_item
                for id_key in id_keys:
                    if team_key and id_key:
                        players_by_team_and_id[f"{team_key}:{id_key}"] = player_item

    LOL_ESPORTS_ASSET_CACHE.update(
        {
            "expiresAt": now + LOL_ESPORTS_ASSET_TTL_SECONDS,
            "teamsBySlug": teams_by_slug,
            "teamsByName": teams_by_name,
            "playersByName": players_by_name,
            "playersById": players_by_id,
            "playersByTeamAndName": players_by_team_and_name,
            "playersByTeamAndId": players_by_team_and_id,
        }
    )
    return LOL_ESPORTS_ASSET_CACHE


def lol_team_asset(team_id: Any = "", team_name: Any = "") -> Dict[str, Any]:
    assets = fetch_lol_esports_assets()
    for key in (lol_slug_key(team_id), lol_slug_key(team_name), lol_asset_key(team_name), lol_asset_key(team_id)):
        if key and key in assets.get("teamsBySlug", {}):
            return assets["teamsBySlug"][key]
        if key and key in assets.get("teamsByName", {}):
            return assets["teamsByName"][key]
    return {}


def lol_player_asset(
    player_name: Any = "",
    team_id: Any = "",
    team_name: Any = "",
    player_id: Any = "",
) -> Dict[str, Any]:
    assets = fetch_lol_esports_assets()
    players_by_team = assets.get("playersByTeamAndName", {}) or {}
    players_by_team_id = assets.get("playersByTeamAndId", {}) or {}
    players_by_name = assets.get("playersByName", {}) or {}
    players_by_id = assets.get("playersById", {}) or {}

    def team_key_candidates() -> List[str]:
        raw_team_name = str(team_name or "").strip()
        keys: List[str] = []
        for item in (
            lol_slug_key(team_id),
            lol_slug_key(raw_team_name),
            lol_asset_key(raw_team_name),
            lol_asset_key(team_id),
        ):
            if item and item not in keys:
                keys.append(item)

        # Common short tag candidates from team name, e.g. "LNG Esports" -> "lng".
        tokens = [x for x in re.split(r"\s+", raw_team_name) if x]
        if tokens:
            first = lol_asset_key(tokens[0])
            if first and first not in keys:
                keys.append(first)
            initials = "".join(token[0] for token in tokens if token and token[0].isalnum()).lower()
            initials_key = lol_asset_key(initials)
            if initials_key and initials_key not in keys:
                keys.append(initials_key)
        return keys

    def player_key_candidates() -> List[str]:
        raw = str(player_name or "").strip()
        if not raw:
            return []
        out: List[str] = []

        def add_key(value: str) -> None:
            key = lol_asset_key(value)
            if key and key not in out:
                out.append(key)

        add_key(raw)

        # "MKOI Supa" / "C9 Vulcan" => "Supa" / "Vulcan"
        parts = [x for x in re.split(r"\s+", raw) if x]
        if len(parts) >= 2:
            add_key(parts[-1])
            add_key("".join(parts[1:]))

        raw_compact = re.sub(r"[^A-Za-z0-9]+", "", raw)
        if raw_compact:
            add_key(raw_compact)

        # Strip team prefixes from compact player name:
        # "LNGBuLLDoG" -> "BuLLDoG", "LNG1xn" -> "1xn"
        prefix_candidates = []
        for tk in team_key_candidates():
            if tk:
                prefix_candidates.append(tk)
                prefix_candidates.append(re.sub(r"(esports|academy|team)$", "", tk))
        for prefix in prefix_candidates:
            p = lol_asset_key(prefix)
            c = lol_asset_key(raw_compact)
            if p and c.startswith(p) and len(c) > len(p) + 1:
                add_key(c[len(p) :])

        # Generic fallback: remove 2-5 leading upper-case-ish letters from raw compact.
        m = re.match(r"^[A-Z0-9]{2,5}([A-Za-z0-9]{2,})$", raw_compact)
        if m:
            add_key(m.group(1))

        return out

    team_keys = team_key_candidates()
    player_keys = player_key_candidates()
    id_keys = []
    raw_player_id = str(player_id or "").strip()
    if raw_player_id:
        id_keys.append(raw_player_id)
    normalized_player_id = lol_asset_key(raw_player_id)
    if normalized_player_id and normalized_player_id not in id_keys:
        id_keys.append(normalized_player_id)

    for id_key in id_keys:
        for tkey in team_keys:
            composite = f"{tkey}:{id_key}"
            if tkey and id_key and composite in players_by_team_id:
                return players_by_team_id[composite]

    for id_key in id_keys:
        if id_key in players_by_id:
            return players_by_id[id_key]

    for pkey in player_keys:
        for tkey in team_keys:
            composite = f"{tkey}:{pkey}"
            if tkey and pkey and composite in players_by_team:
                return players_by_team[composite]

    for pkey in player_keys:
        if pkey in players_by_name:
            return players_by_name[pkey]

    # Fuzzy fallback: unique suffix match for unresolved prefixed names.
    for pkey in player_keys:
        if len(pkey) < 4:
            continue
        candidates = [item for key, item in players_by_name.items() if key.endswith(pkey) or pkey.endswith(key)]
        if len(candidates) == 1:
            return candidates[0]

    return {}


def build_lol_team_logo_map(cur: pymysql.cursors.DictCursor) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not lol_table_exists(cur, "lol_match_result"):
        return out
    rows = lol_fetch_all(
        cur,
        """
        WITH unioned AS (
            SELECT team1_id AS team_id, team1 AS team_name, team1_logo AS team_logo,
                   COALESCE(match_time, CONCAT(match_date, ' 00:00:00')) AS ts
            FROM lol_match_result
            WHERE team1_id IS NOT NULL AND team1_id <> ''
              AND team1_logo IS NOT NULL AND team1_logo <> ''
            UNION ALL
            SELECT team2_id AS team_id, team2 AS team_name, team2_logo AS team_logo,
                   COALESCE(match_time, CONCAT(match_date, ' 00:00:00')) AS ts
            FROM lol_match_result
            WHERE team2_id IS NOT NULL AND team2_id <> ''
              AND team2_logo IS NOT NULL AND team2_logo <> ''
        ),
        ranked AS (
            SELECT
                team_id, team_name, team_logo, ts,
                ROW_NUMBER() OVER (PARTITION BY team_id ORDER BY ts DESC) AS rn
            FROM unioned
        )
        SELECT team_id, team_name, team_logo
        FROM ranked
        WHERE rn = 1
        """,
    )
    for row in rows:
        logo = normalize_lol_image(row.get("team_logo"))
        if not logo:
            continue
        team_id = str(row.get("team_id") or "").strip()
        team_name = str(row.get("team_name") or "").strip()
        if team_id:
            out[f"id:{team_id.lower()}"] = logo
        if team_name:
            out[f"name:{team_name.lower()}"] = logo
    return out


def lol_avatar_placeholder(name: Any) -> str:
    text = str(name or "").strip()
    if not text:
        return ""
    return f"https://ui-avatars.com/api/?name={quote_plus(text)}&background=1f6feb&color=ffffff&size=128"


def lol_player_avatar_override(player_name: Any = "", player_id: Any = "") -> str:
    for key in (
        lol_asset_key(player_id),
        lol_asset_key(player_name),
    ):
        if key and key in LOL_PLAYER_AVATAR_OVERRIDES:
            return normalize_lol_image(LOL_PLAYER_AVATAR_OVERRIDES[key])
    return ""


def lol_clean_html_text(value: Any) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return " ".join(unescape(text).split())


def lol_year_from_text(value: Any) -> str:
    text = str(value or "")
    year_match = re.search(r"(20\d{2}|19\d{2})", text)
    if year_match:
        return year_match.group(1)
    season_match = re.search(r"\bS(\d{1,2})\b", text, re.IGNORECASE)
    if season_match:
        season_number = safe_int(season_match.group(1))
        if season_number >= 3:
            return str(2010 + season_number)
    return ""


def lol_extract_gol_player_id(value: Any) -> str:
    match = re.search(r"player-(?:stats|matchlist|history)/(\d+)/", str(value or ""))
    return match.group(1) if match else ""


def lol_gol_get(url: str) -> str:
    response = requests.get(
        url,
        headers={"Accept": "text/html,*/*", "User-Agent": LIVE_HEADERS["User-Agent"]},
        timeout=(5, 25),
    )
    response.raise_for_status()
    return response.text


def fetch_lol_gol_career_profile(gol_player_id: Any) -> Dict[str, Any]:
    player_key = str(gol_player_id or "").strip()
    if not player_key:
        return {}
    now = time.time()
    cached = LOL_GOL_CAREER_CACHE.get(player_key)
    if cached and cached.get("expiresAt", 0) > now:
        return cached.get("profile", {})

    stats_url = f"{LOL_GOL_BASE_URL}/players/player-stats/{player_key}/season-ALL/split-ALL/tournament-ALL/champion-ALL/"
    history_url = f"{LOL_GOL_BASE_URL}/players/player-history/{player_key}/season-ALL/split-ALL/tournament-ALL/"
    profile: Dict[str, Any] = {
        "source": "Games of Legends",
        "sourceUrl": stats_url,
        "careerStart": "",
        "careerStartLabel": "",
        "oldestTournament": "",
        "oldestMatchTitle": "",
        "honors": [],
    }

    try:
        stats_html = lol_gol_get(stats_url)
        option_labels = [
            lol_clean_html_text(label)
            for _, label in re.findall(r"<option[^>]*value=['\"]([^'\"]+)['\"][^>]*>(.*?)</option>", stats_html, re.S)
        ]
        tournament_labels = [label for label in option_labels if label and label != "-- ALL --"]
        year_items = [
            (lol_year_from_text(label), label)
            for label in tournament_labels
            if lol_year_from_text(label)
        ]
        if year_items:
            oldest_year, oldest_label = min(year_items, key=lambda item: (safe_int(item[0]), item[1]))
            profile.update(
                {
                    "careerStart": oldest_year,
                    "careerStartLabel": f"{oldest_year} · {oldest_label}",
                    "oldestTournament": oldest_label,
                }
            )
    except Exception:
        pass

    try:
        history_html = lol_gol_get(history_url)
        honors: List[Dict[str, Any]] = []
        oldest_history: Dict[str, Any] = {}
        for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", history_html, re.S):
            cols = [lol_clean_html_text(col) for col in re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.S)]
            if len(cols) < 6 or not re.match(r"^S\d{1,2}$", cols[0], re.IGNORECASE):
                continue
            year = lol_year_from_text(cols[0]) or lol_year_from_text(cols[2])
            if not year:
                continue
            result = cols[5].upper()
            rank_desc = "冠军" if "WIN" in result else ("亚军" if "LOSS" in result else result.title())
            honor = {
                "tt_id": f"gol_{player_key}_{year}_{lol_slug_key(cols[2])}",
                "tt_name": cols[2],
                "start_time": year,
                "team_name": "",
                "rank_desc": rank_desc,
                "grade": cols[1] or "S",
                "source": "Games of Legends",
                "matchTitle": cols[4],
            }
            honors.append(honor)
            if not oldest_history or safe_int(year) < safe_int(oldest_history.get("year")):
                oldest_history = {"year": year, "tournament": cols[2], "matchTitle": cols[4]}
        if honors:
            profile["honors"] = sorted(
                honors,
                key=lambda item: (safe_int(item.get("start_time")), str(item.get("tt_name") or "")),
                reverse=True,
            )[:80]
        if oldest_history:
            profile["oldestMatchTitle"] = oldest_history.get("matchTitle") or ""
            if not profile.get("careerStart") or safe_int(oldest_history.get("year")) < safe_int(profile.get("careerStart")):
                profile.update(
                    {
                        "careerStart": oldest_history.get("year") or "",
                        "careerStartLabel": f"{oldest_history.get('year')} · {oldest_history.get('tournament')}",
                        "oldestTournament": oldest_history.get("tournament") or "",
                    }
                )
    except Exception:
        pass

    LOL_GOL_CAREER_CACHE[player_key] = {"expiresAt": now + LOL_GOL_CAREER_TTL_SECONDS, "profile": profile}
    return profile


def lol_team_alias_keys(team_id: Any = "", team_name: Any = "") -> Set[str]:
    keys = {
        lol_asset_key(team_id),
        lol_asset_key(team_name),
        lol_asset_key(lol_team_asset(team_id, team_name).get("name")),
        lol_asset_key(lol_team_asset(team_id, team_name).get("code")),
    }
    expanded = {key for key in keys if key}
    for key in list(expanded):
        expanded.update(lol_asset_key(alias) for alias in LOL_GOL_TEAM_ALIASES.get(key, set()))
    return {key for key in expanded if key}


def lol_gol_profile_matches_team(profile: Dict[str, Any], team_id: Any = "", team_name: Any = "") -> bool:
    title = str(profile.get("oldestMatchTitle") or "")
    if not title:
        return False
    aliases = lol_team_alias_keys(team_id, team_name)
    if not aliases:
        return False
    sides = re.split(r"\s+vs\s+", title, flags=re.IGNORECASE)
    for side in sides:
        side_name = re.split(r"\s+-\s+", side)[-1].strip()
        side_key = lol_asset_key(side_name)
        for alias in aliases:
            if len(alias) <= 2:
                if side_key == alias:
                    return True
            elif alias in side_key:
                return True
    return False


def lol_status_code(status: Any, match_time: Any = None, score1: Any = None, score2: Any = None) -> int:
    text = str(status or "").strip().lower()
    if text in {"inprogress", "in_progress", "live"}:
        return 1
    has_result = score1 is not None and score2 is not None and (safe_int(score1) + safe_int(score2) > 0)
    if text in {"completed", "finished"}:
        return 2 if has_result else 1
    if text in {"unstarted", "not_started", "scheduled"}:
        return 0
    if has_result:
        return 2
    if isinstance(match_time, datetime):
        return 0 if match_time > datetime.now() else 1
    return 0


def lol_status_text(status: Any, match_time: Any = None, score1: Any = None, score2: Any = None) -> str:
    code = lol_status_code(status, match_time, score1, score2)
    if code == 1:
        return "进行中"
    if code == 2:
        return "已完赛"
    return "未开赛"


def build_lol_matches(cur: pymysql.cursors.DictCursor, limit: int = 500) -> List[Dict[str, Any]]:
    if not lol_table_exists(cur, "lol_match_result"):
        return []

    rows = lol_fetch_all(
        cur,
        """
        SELECT
            match_id,
            event_name,
            match_date,
            match_time,
            stage,
            patch,
            team1_id,
            team1,
            team1_logo,
            team2_id,
            team2,
            team2_logo,
            score1,
            score2,
            winner,
            bo,
            status
        FROM lol_match_result
        ORDER BY COALESCE(match_time, match_date) DESC, match_id DESC
        LIMIT %s
        """,
        (limit,),
    )

    matches: List[Dict[str, Any]] = []
    for row in rows:
        score1 = row.get("score1")
        score2 = row.get("score2")
        winner = row.get("winner") or ""
        if not winner and score1 is not None and score2 is not None:
            if safe_int(score1) > safe_int(score2):
                winner = row.get("team1") or ""
            elif safe_int(score2) > safe_int(score1):
                winner = row.get("team2") or ""
        score = "-"
        if score1 is not None and score2 is not None:
            score = f"{safe_int(score1)}:{safe_int(score2)}"
        matches.append(
            {
                "matchId": row.get("match_id"),
                "date": row.get("match_date") or "",
                "matchTime": row.get("match_time") or "",
                "tournament": row.get("event_name") or "-",
                "stage": row.get("stage") or "-",
                "teamA": row.get("team1") or "-",
                "teamB": row.get("team2") or "-",
                "teamAId": row.get("team1_id") or "",
                "teamBId": row.get("team2_id") or "",
                "teamALogo": row.get("team1_logo") or lol_team_asset(row.get("team1_id"), row.get("team1")).get("image") or "",
                "teamBLogo": row.get("team2_logo") or lol_team_asset(row.get("team2_id"), row.get("team2")).get("image") or "",
                "score": score,
                "scoreA": score1,
                "scoreB": score2,
                "winner": winner or "-",
                "note": f"Patch {row.get('patch') or '-'} · BO{row.get('bo') or '-'}",
                "status": lol_status_text(row.get("status"), row.get("match_time"), score1, score2),
                "statusCode": lol_status_code(row.get("status"), row.get("match_time"), score1, score2),
            }
        )
    return matches


def build_lol_tournaments(cur: pymysql.cursors.DictCursor) -> List[Dict[str, Any]]:
    if not lol_table_exists(cur, "lol_event_basic"):
        return []

    rows = lol_fetch_all(
        cur,
        """
        SELECT
            eb.event_id,
            eb.event_name,
            MAX(mr.league_slug) AS league_slug,
            MIN(mr.match_date) AS start_date,
            MAX(mr.match_date) AS end_date,
            COUNT(mr.match_id) AS match_count
        FROM lol_event_basic eb
        LEFT JOIN lol_match_result mr ON mr.event_id = eb.event_id
        GROUP BY eb.event_id, eb.event_name
        ORDER BY end_date DESC, eb.event_name
        """,
    )
    return [
        {
            "name": row.get("event_name") or row.get("event_id") or "-",
            "tier": "S",
            "region": infer_lol_region(event_name=row.get("league_slug") or row.get("event_name")),
            "start": row.get("start_date") or "",
            "end": row.get("end_date") or "",
            "status": "已结束" if row.get("end_date") and str(row.get("end_date")) < datetime.now().strftime("%Y-%m-%d") else "进行中/待赛",
            "prize": "-",
            "matchCount": safe_int(row.get("match_count")),
        }
        for row in rows
    ]


def build_lol_teams(cur: pymysql.cursors.DictCursor) -> List[Dict[str, Any]]:
    if not lol_table_exists(cur, "lol_team_basic"):
        return []

    team_logo_map = build_lol_team_logo_map(cur)
    tb_cols = table_columns(cur, "lol_team_basic")
    logo_col = next(
        (
            col
            for col in ("team_logo", "logo", "image", "icon", "team_image")
            if col in tb_cols
        ),
        "",
    )
    db_logo_expr = f"NULLIF(tb.{logo_col}, '')" if logo_col else "NULL"

    rows = lol_fetch_all(
        cur,
        f"""
        SELECT
            tb.team_id,
            tb.team_name,
            tb.region,
            {db_logo_expr} AS db_logo
        FROM lol_team_basic tb
        ORDER BY tb.team_name
        """,
    )
    power_ratings = build_lol_power_ratings(cur)
    teams = []
    for row in rows:
        asset = lol_team_asset(row.get("team_id"), row.get("team_name"))
        db_logo = normalize_lol_image(row.get("db_logo"))
        map_logo = (
            team_logo_map.get(f"id:{str(row.get('team_id') or '').strip().lower()}")
            or team_logo_map.get(f"name:{str(row.get('team_name') or '').strip().lower()}")
            or ""
        )
        logo = db_logo or normalize_lol_image(asset.get("image")) or map_logo
        region = resolve_lol_team_region(row.get("team_id"), row.get("team_name"), row.get("region"))
        team_id = str(row.get("team_id") or "").strip()
        stat = power_ratings.get(team_id, {})
        matches_played = safe_int(stat.get("matches"))
        wins = safe_int(stat.get("wins"))
        losses = max(0, matches_played - wins)
        game_wins = safe_int(stat.get("game_wins"))
        game_losses = safe_int(stat.get("game_losses"))
        win_rate = wins / max(1, matches_played)
        rank_score = safe_float(stat.get("powerRating"))
        teams.append({
            "teamId": team_id,
            "name": row.get("team_name") or row.get("team_id") or "-",
            "region": region,
            "logo": logo or "",
            "teamLogo": logo or "",
            "coach": "-",
            "style": "LoL Esports",
            "form": f"{wins}W / {matches_played}M",
            "wins": wins,
            "losses": losses,
            "matchesPlayed": matches_played,
            "winRateRaw": win_rate,
            "winRate": f"{round(win_rate * 100)}%",
            "gameWins": game_wins,
            "gameLosses": game_losses,
            "rankScore": round(rank_score, 2),
            "points": round(rank_score, 1),
            "elo": stat.get("elo") or LOL_ELO_BASE,
            "regionElo": stat.get("regionElo") or LOL_ELO_BASE,
            "rankingModel": "elo_power",
        })
    teams.sort(
        key=lambda item: (
            -safe_float(item.get("rankScore")),
            -safe_int(item.get("matchesPlayed")),
            str(item.get("name") or ""),
        )
    )
    region_ranks: Dict[str, int] = {}
    for idx, team in enumerate(teams, start=1):
        region_key = str(team.get("region") or "-")
        region_ranks[region_key] = region_ranks.get(region_key, 0) + 1
        team["rank"] = idx
        team["globalRank"] = idx
        team["regionRank"] = region_ranks[region_key]
    return teams


def build_lol_players(cur: pymysql.cursors.DictCursor, limit: int = 0) -> List[Dict[str, Any]]:
    has_basic = lol_table_exists(cur, "lol_player_basic")
    has_stats = lol_table_exists(cur, "lol_game_player_stats")
    if not has_basic and not has_stats:
        return []

    basic_cols: Set[str] = set()
    stats_cols: Set[str] = set()
    if has_basic:
        basic_cols = table_columns(cur, "lol_player_basic")
    if has_stats:
        stats_cols = table_columns(cur, "lol_game_player_stats")

    basic_avatar_cols = [c for c in ("portrait", "half_portrait", "avatar", "image", "headshot", "photo") if c in basic_cols]
    if basic_avatar_cols:
        basic_avatar_expr = "COALESCE(" + ", ".join([f"NULLIF(pb.{c}, '')" for c in basic_avatar_cols]) + ")"
    else:
        basic_avatar_expr = "NULL"

    stats_avatar_cols = [c for c in ("player_portrait", "portrait", "half_portrait", "avatar", "player_avatar", "image") if c in stats_cols]
    if stats_avatar_cols:
        stats_avatar_expr = "COALESCE(" + ", ".join([f"NULLIF(gps.{c}, '')" for c in stats_avatar_cols]) + ")"
    else:
        stats_avatar_expr = "NULL"

    if has_basic and has_stats:
        stats_join = """
        LEFT JOIN lol_game_player_stats gps
          ON gps.player_id = pb.player_id
        LEFT JOIN lol_match_result mr
          ON mr.match_id = gps.match_id
        LEFT JOIN (
            SELECT game_id, team_side, SUM(kills) AS team_kills
            FROM lol_game_player_stats
            GROUP BY game_id, team_side
        ) tg
          ON tg.game_id = gps.game_id
         AND tg.team_side = gps.team_side
        """
        stats_select = """
            COUNT(gps.game_id) AS games_played,
            SUM(gps.kills) AS kills,
            SUM(gps.deaths) AS deaths,
            SUM(gps.assists) AS assists,
            SUM(CASE WHEN mr.winner = gps.team_name THEN 1 ELSE 0 END) AS wins,
            SUM(tg.team_kills) AS team_kills,
            AVG(gps.cs) AS avg_cs,
            MIN(gps.stat_index) AS role_index,
            MAX(%s) AS stats_avatar
        """
        stats_select = stats_select % stats_avatar_expr
    else:
        stats_join = ""
        stats_select = """
            0 AS games_played,
            0 AS kills,
            0 AS deaths,
            0 AS assists,
            0 AS wins,
            0 AS team_kills,
            NULL AS avg_cs,
            NULL AS role_index,
            NULL AS stats_avatar
        """
    rows: List[Dict[str, Any]] = []
    if has_basic:
        active_pb_where = lol_active_player_sql(cur, "pb", "WHERE")
        rows.extend(
            lol_fetch_all(
                cur,
                f"""
                SELECT
                    pb.player_id,
                    MAX(NULLIF(pb.player_name, '')) AS player_name,
                    pb.team_id,
                    MAX(NULLIF(pb.team_name, '')) AS team_name,
                    MAX(NULLIF(pb.role, '')) AS role,
                    MAX(NULLIF(pb.source, '')) AS source,
                    MAX({basic_avatar_expr}) AS db_avatar,
                    COALESCE(MAX(NULLIF(tb.region, '')), '') AS region,
                    {stats_select}
                FROM lol_player_basic pb
                LEFT JOIN lol_team_basic tb ON tb.team_id = pb.team_id
                {stats_join}
                {active_pb_where}
                GROUP BY pb.player_id, pb.team_id
                ORDER BY games_played DESC, player_name
                """,
            )
        )

    if has_stats:
        missing_basic_filter = """
                  AND NOT EXISTS (
                    SELECT 1
                    FROM lol_player_basic pb
                    WHERE pb.player_id = gps.player_id
                  )
        """ if has_basic else ""
        rows.extend(
            lol_fetch_all(
                cur,
                f"""
                SELECT
                    gps.player_id,
                    MAX(NULLIF(gps.player_name, '')) AS player_name,
                    MAX(NULLIF(gps.team_id, '')) AS team_id,
                    MAX(NULLIF(gps.team_name, '')) AS team_name,
                    NULL AS role,
                    'lol_game_player_stats' AS source,
                    MAX({stats_avatar_expr}) AS db_avatar,
                    COALESCE(MAX(NULLIF(tb.region, '')), '') AS region,
                    COUNT(gps.game_id) AS games_played,
                    SUM(gps.kills) AS kills,
                    SUM(gps.deaths) AS deaths,
                    SUM(gps.assists) AS assists,
                    SUM(CASE WHEN mr.winner = gps.team_name THEN 1 ELSE 0 END) AS wins,
                    SUM(tg.team_kills) AS team_kills,
                    AVG(gps.cs) AS avg_cs,
                    MIN(gps.stat_index) AS role_index,
                    MAX({stats_avatar_expr}) AS stats_avatar
                FROM lol_game_player_stats gps
                LEFT JOIN lol_team_basic tb ON tb.team_id = gps.team_id
                LEFT JOIN lol_match_result mr ON mr.match_id = gps.match_id
                LEFT JOIN (
                    SELECT game_id, team_side, SUM(kills) AS team_kills
                    FROM lol_game_player_stats
                    GROUP BY game_id, team_side
                ) tg
                  ON tg.game_id = gps.game_id
                 AND tg.team_side = gps.team_side
                WHERE gps.player_id IS NOT NULL
                  AND gps.player_id <> ''
                  {missing_basic_filter}
                GROUP BY gps.player_id
                ORDER BY games_played DESC, player_name
                """,
            )
        )

    players: List[Dict[str, Any]] = []
    team_logo_map = build_lol_team_logo_map(cur)
    dedup_rows: Dict[str, Dict[str, Any]] = {}

    def row_priority(item: Dict[str, Any]) -> Tuple[int, int, int]:
        source = str(item.get("source") or "").strip().lower()
        source_rank = 2 if source == "lolesports" else (1 if source else 0)
        games = safe_int(item.get("games_played"))
        has_avatar = 1 if normalize_lol_image(item.get("db_avatar")) or normalize_lol_image(item.get("stats_avatar")) else 0
        return (source_rank, games, has_avatar)

    for row in rows:
        player_id_key = str(row.get("player_id") or "").strip()
        if not player_id_key:
            continue
        existing = dedup_rows.get(player_id_key)
        if existing is None or row_priority(row) > row_priority(existing):
            dedup_rows[player_id_key] = row

    rows = list(dedup_rows.values())
    power_ratings = build_lol_power_ratings(cur) if has_stats else {}
    for row in rows:
        player_id = str(row.get("player_id") or "").strip()
        raw_deaths = safe_int(row.get("deaths"))
        deaths = max(1, raw_deaths)
        kills = safe_int(row.get("kills"))
        assists = safe_int(row.get("assists"))
        wins = safe_int(row.get("wins"))
        team_kills = safe_int(row.get("team_kills"))
        games_played = safe_int(row.get("games_played"))
        kda = (kills + assists) / deaths if games_played else 0
        win_rate = wins / max(1, games_played)
        kill_participation = (kills + assists) / max(1, team_kills)
        avg_deaths = raw_deaths / max(1, games_played)
        sample_score = clamp_float((games_played / 30) ** 0.7) if games_played else 0.0
        kda_score = clamp_float(kda / 6)
        kp_score = clamp_float(kill_participation / 0.75)
        survival_score = clamp_float(1 - (avg_deaths / 5))
        team_power = safe_float(power_ratings.get(str(row.get("team_id") or "").strip(), {}).get("powerRating")) or LOL_ELO_BASE
        team_context_score = clamp_float((team_power - 1450.0) / 240.0)
        performance_score = 100 * (
            0.36 * kda_score
            + 0.24 * win_rate
            + 0.22 * kp_score
            + 0.08 * survival_score
        ) if games_played else 0
        raw_score = 0.76 * performance_score + 0.24 * team_context_score * 100
        rank_score = raw_score * (0.35 + 0.65 * sample_score) if games_played else 0
        avg_cs = format_metric(row.get("avg_cs"), 1)
        role = normalize_lol_role(row.get("role")) if row.get("role") else lol_role_from_stat_index(row.get("role_index"))
        region = resolve_lol_team_region(row.get("team_id"), row.get("team_name"), row.get("region"))
        asset = lol_player_asset(
            row.get("player_name"),
            row.get("team_id"),
            row.get("team_name"),
            player_id=row.get("player_id"),
        )
        db_avatar = normalize_lol_image(row.get("db_avatar"))
        stats_avatar = normalize_lol_image(row.get("stats_avatar"))
        avatar = (
            db_avatar
            or stats_avatar
            or normalize_lol_image(asset.get("avatar"))
            or lol_player_avatar_override(row.get("player_name"), row.get("player_id"))
            or lol_avatar_placeholder(row.get("player_name") or row.get("player_id"))
        )
        if asset.get("role"):
            role = normalize_lol_role(asset["role"])
        players.append(
            {
                "playerId": row.get("player_id"),
                "playerKey": f"{row.get('player_id') or ''}:{row.get('team_id') or ''}",
                "name": row.get("player_name") or row.get("player_id") or "-",
                "teamId": row.get("team_id") or "",
                "team": row.get("team_name") or "-",
                "role": role,
                "region": region,
                "avatar": avatar or "",
                "rating": f"{kda:.2f}" if games_played else "-",
                "kda": f"{kda:.2f}" if games_played else "-",
                "kd": f"{kills}/{raw_deaths}/{assists}" if games_played else "-",
                "rankScore": round(rank_score, 2),
                "rawKda": round(kda, 4),
                "winRateRaw": win_rate,
                "killParticipationRaw": kill_participation,
                "primaryMetric": "KDA",
                "primaryMetricValue": f"{kda:.2f}" if games_played else "-",
                "secondaryMetric": "场次",
                "secondaryMetricValue": str(games_played),
                "impact": str(games_played),
                "highlight": f"评分 {rank_score:.1f} · 胜率 {round(win_rate * 100)}%" if games_played else "暂无比赛统计",
                "gamesPlayed": games_played,
                "avgCs": avg_cs if games_played else "-",
            }
        )
        if limit > 0 and len(players) >= limit:
            break
    players.sort(
        key=lambda item: (
            -safe_float(item.get("rankScore")),
            -safe_int(item.get("gamesPlayed")),
            str(item.get("name") or ""),
        )
    )
    region_ranks: Dict[str, int] = {}
    for idx, item in enumerate(players, start=1):
        region_key = str(item.get("region") or "-")
        region_ranks[region_key] = region_ranks.get(region_key, 0) + 1
        item["rank"] = idx
        item["globalRank"] = idx
        item["regionRank"] = region_ranks[region_key]
    return players


def build_lol_player_rank(cur: pymysql.cursors.DictCursor, player_id: str, target_region: str = "") -> Dict[str, Any]:
    if not player_id:
        return {"globalRank": "-", "regionRank": "-", "rankScore": 0}

    target_id = str(player_id).strip().lower()
    for item in build_lol_players(cur):
        if str(item.get("playerId") or "").strip().lower() == target_id:
            return {
                "globalRank": item.get("globalRank") or item.get("rank") or "-",
                "regionRank": item.get("regionRank") or "-",
                "rankScore": item.get("rankScore") or 0,
            }
    return {"globalRank": "-", "regionRank": "-", "rankScore": 0}


def build_lol_leaderboard(teams: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    leaderboard: List[Dict[str, Any]] = []
    for idx, team in enumerate(teams, start=1):
        played = safe_int(team.get("matchesPlayed"))
        wins = safe_int(team.get("wins"))
        win_rate = wins / max(1, played)
        rank_score = safe_float(team.get("rankScore"))
        leaderboard.append(
            {
                "rank": team.get("rank") or idx,
                "globalRank": team.get("globalRank") or idx,
                "regionRank": team.get("regionRank") or "-",
                "name": team.get("name") or "-",
                "region": team.get("region") or "-",
                "points": round(rank_score, 1),
                "rankScore": round(rank_score, 2),
                "elo": team.get("elo"),
                "regionElo": team.get("regionElo"),
                "rankingModel": team.get("rankingModel") or "elo_power",
                "winRate": f"{round(win_rate * 100)}%",
                "winRateRaw": win_rate,
                "trend": "0",
                "teamId": team.get("teamId") or "",
                "logo": team.get("logo") or team.get("teamLogo") or "",
                "teamLogo": team.get("teamLogo") or team.get("logo") or "",
                "matchesPlayed": played,
                "wins": wins,
                "losses": safe_int(team.get("losses")),
            }
        )
    return leaderboard


def build_lol_dataset() -> Dict[str, Any]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            tournaments = build_lol_tournaments(cur)
            matches = build_lol_matches(cur, limit=5000)
            teams = build_lol_teams(cur)
            players = build_lol_players(cur)

    leaderboard = build_lol_leaderboard(teams)
    updated_at = safe_datetime(datetime.now())
    return {
        "gameId": "lol",
        "gameName": "英雄联盟",
        "gameSubtitle": "League of Legends",
        "color": "#0f8f8f",
        "updatedAt": updated_at,
        "leaderboard": leaderboard,
        "tournaments": tournaments,
        "matches": matches,
        "teams": teams,
        "players": players,
        "analysis": {
            "summary": "LoL dataset generated from MySQL lol_* esports tables.",
            "turningPoints": [
                f"{matches[0]['teamA']} vs {matches[0]['teamB']}: {matches[0]['score']}"
            ] if matches else ["No LoL matches imported yet."],
            "teamInsight": "Team records are aggregated from lol_match_result.",
            "playerInsight": "Player KDA samples are aggregated from lol_game_player_stats.",
        },
        "mappingNotes": [
            {"title": "Data source", "desc": "All LoL homepage blocks are read from MySQL lol_* tables."},
            {"title": "Series and games", "desc": "BO matches are stored in lol_match_result; maps/games are stored in lol_game_basic."},
            {"title": "Player stats", "desc": "Single-game champion, KDA, and CS are stored in lol_game_player_stats."},
        ],
        "metrics": [
            {"label": "赛事总数", "value": str(len(tournaments)), "detail": "From lol_event_basic"},
            {"label": "比赛总数", "value": str(len(matches)), "detail": "From lol_match_result"},
            {"label": "战队总数", "value": str(len(teams)), "detail": "From lol_team_basic"},
            {"label": "选手总数", "value": str(len(players)), "detail": "From lol_player_basic"},
        ],
        "filters": {
            "regions": sorted({
                item["region"]
                for item in [*tournaments, *teams, *players]
                if item.get("region") and item.get("region") != "-"
            }),
            "tiers": sorted({item["tier"] for item in tournaments if item.get("tier")}),
        },
        "analysisOutput": [
            {"key": "当前项目", "value": "英雄联盟"},
            {"key": "最新同步", "value": updated_at},
            {"key": "赛事总量", "value": str(len(tournaments))},
            {"key": "比赛总量", "value": str(len(matches))},
            {"key": "战队总量", "value": str(len(teams))},
            {"key": "选手总量", "value": str(len(players))},
        ],
    }


def build_lol_matches_filtered(
    cur: pymysql.cursors.DictCursor,
    *,
    view: str = "fixture",
    date_filter: str = "",
    limit: int = 3000,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    if not lol_table_exists(cur, "lol_match_result"):
        return []
    where = []
    params: List[Any] = []
    if date_filter:
        where.append("match_date = %s")
        params.append(date_filter)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    safe_view = str(view or "").strip().lower()
    if safe_view not in {"fixture", "result", "all"}:
        safe_view = "fixture"
    safe_limit = max(1, min(safe_int(limit, 3000), 10000))
    safe_offset = max(0, safe_int(offset, 0))
    fetch_limit = min(100000, max(safe_limit + safe_offset + 5000, 12000))
    params.append(fetch_limit)
    rows = lol_fetch_all(
        cur,
        f"""
        SELECT
            match_id,
            event_name,
            match_date,
            match_time,
            stage,
            patch,
            team1_id,
            team1,
            team1_logo,
            team2_id,
            team2,
            team2_logo,
            score1,
            score2,
            winner,
            bo,
            status
        FROM lol_match_result
        {where_sql}
        ORDER BY COALESCE(match_time, match_date) DESC, match_id DESC
        LIMIT %s
        """,
        tuple(params),
    )
    parsed_rows = []
    for row in rows:
        score1 = row.get("score1")
        score2 = row.get("score2")
        status_code = lol_status_code(row.get("status"), row.get("match_time"), score1, score2)
        is_finished = status_code == 2
        if safe_view == "result" and not is_finished:
            continue
        if safe_view == "fixture" and is_finished:
            continue
        winner = row.get("winner") or ""
        if not winner and score1 is not None and score2 is not None:
            if safe_int(score1) > safe_int(score2):
                winner = row.get("team1") or ""
            elif safe_int(score2) > safe_int(score1):
                winner = row.get("team2") or ""
        parsed_rows.append(
            (
                parse_datetime_text(row.get("match_time")) or parse_datetime_text(row.get("match_date")),
                {
                    "matchId": row.get("match_id"),
                    "date": row.get("match_date") or "",
                    "matchTime": row.get("match_time") or "",
                    "tournament": row.get("event_name") or "-",
                    "stage": f"{row.get('stage') or '-'} · BO{row.get('bo') or '-'}",
                    "teamA": row.get("team1") or "-",
                    "teamB": row.get("team2") or "-",
                    "teamAId": row.get("team1_id") or "",
                    "teamBId": row.get("team2_id") or "",
                    "teamALogo": row.get("team1_logo") or lol_team_asset(row.get("team1_id"), row.get("team1")).get("image") or "",
                    "teamBLogo": row.get("team2_logo") or lol_team_asset(row.get("team2_id"), row.get("team2")).get("image") or "",
                    "score": f"{safe_int(score1)}:{safe_int(score2)}" if score1 is not None and score2 is not None else "-",
                    "scoreA": score1,
                    "scoreB": score2,
                    "winner": winner or "-",
                    "status": lol_status_text(row.get("status"), row.get("match_time"), score1, score2),
                    "statusCode": status_code,
                    "note": f"Patch {row.get('patch') or '-'}",
                },
            )
        )
    reverse_order = safe_view == "result" or safe_view == "all"
    parsed_rows.sort(
        key=lambda item: (
            item[0] is None,
            item[0] or datetime.min,
            str(item[1].get("matchId") or ""),
        ),
        reverse=reverse_order,
    )
    sliced = [row for _, row in parsed_rows[safe_offset : safe_offset + safe_limit]]
    return sliced


def build_lol_match_detail(cur: pymysql.cursors.DictCursor, match_id: str) -> Dict[str, Any]:
    if not lol_table_exists(cur, "lol_match_result"):
        return {"matchId": match_id, "maps": [], "playerStats": {"teamA": [], "teamB": []}, "mapPlayerStats": []}

    cur.execute(
        """
        SELECT *
        FROM lol_match_result
        WHERE match_id = %s
        LIMIT 1
        """,
        (match_id,),
    )
    match = cur.fetchone()
    if not match:
        return {"matchId": match_id, "maps": [], "playerStats": {"teamA": [], "teamB": []}, "mapPlayerStats": []}
    match = json_row(match)

    game_rows = []
    if lol_table_exists(cur, "lol_game_basic"):
        game_rows = lol_fetch_all(
            cur,
            """
            SELECT *
            FROM lol_game_basic
            WHERE match_id = %s
            ORDER BY game_number ASC
            """,
            (match_id,),
        )

    maps = [
        {
            "index": row.get("game_number"),
            "map": f"Game {row.get('game_number')}",
            "team1Score": None,
            "team2Score": None,
            "winner": "-",
        }
        for row in game_rows
    ]

    map_player_stats = []
    first_team_a: List[Dict[str, Any]] = []
    first_team_b: List[Dict[str, Any]] = []
    if lol_table_exists(cur, "lol_game_player_stats"):
        for game in game_rows:
            players = lol_fetch_all(
                cur,
                """
                SELECT *
                FROM lol_game_player_stats
                WHERE game_id = %s
                ORDER BY stat_index ASC
                """,
                (game.get("game_id"),),
            )
            team_a = []
            team_b = []
            for row in players:
                deaths = max(1, safe_int(row.get("deaths")))
                kda = (safe_int(row.get("kills")) + safe_int(row.get("assists"))) / deaths
                asset = lol_player_asset(
                    row.get("player_name"),
                    row.get("team_id"),
                    row.get("team_name"),
                    player_id=row.get("player_id"),
                )
                item = {
                    "playerId": row.get("player_id"),
                    "name": row.get("player_name"),
                    "avatar": (
                        normalize_lol_image(asset.get("avatar"))
                        or lol_player_avatar_override(row.get("player_name"), row.get("player_id"))
                        or lol_avatar_placeholder(row.get("player_name") or row.get("player_id"))
                    ),
                    "champion": row.get("champion"),
                    "rating": f"{kda:.2f}",
                    "kda": f"{kda:.2f}",
                    "adr": "-",
                    "kast": "-",
                    "kpr": "-",
                    "kd": row.get("kda_text") or "-",
                    "kill": row.get("kills"),
                    "death": row.get("deaths"),
                    "assist": row.get("assists"),
                    "cs": row.get("cs"),
                }
                if row.get("team_side") == "blue":
                    team_a.append(item)
                else:
                    team_b.append(item)
            if not first_team_a and not first_team_b:
                first_team_a = team_a
                first_team_b = team_b
            map_player_stats.append(
                {
                    "mapIndex": game.get("game_number"),
                    "mapName": f"Game {game.get('game_number')}",
                    "teamA": team_a,
                    "teamB": team_b,
                }
            )

    match_winner = match.get("winner") or ""
    if not match_winner and match.get("score1") is not None and match.get("score2") is not None:
        if safe_int(match.get("score1")) > safe_int(match.get("score2")):
            match_winner = match.get("team1") or ""
        elif safe_int(match.get("score2")) > safe_int(match.get("score1")):
            match_winner = match.get("team2") or ""

    return {
        "matchId": match.get("match_id"),
        "exists": True,
        "date": match.get("match_date") or "",
        "matchTime": match.get("match_time") or match.get("match_date") or "",
        "tournament": {
            "name": match.get("event_name") or "-",
            "tier": "S",
        },
        "stage": match.get("stage") or "-",
        "statusText": lol_status_text(match.get("status"), match.get("match_time"), match.get("score1"), match.get("score2")),
        "statusCode": lol_status_code(match.get("status"), match.get("match_time"), match.get("score1"), match.get("score2")),
        "patch": match.get("patch") or "-",
        "bo": match.get("bo"),
        "score": f"{safe_int(match.get('score1'))}:{safe_int(match.get('score2'))}" if match.get("score1") is not None and match.get("score2") is not None else "-",
        "winner": match_winner or "-",
        "teamA": {
            "id": match.get("team1_id"),
            "name": match.get("team1") or "-",
            "score": match.get("score1"),
            "logo": match.get("team1_logo") or lol_team_asset(match.get("team1_id"), match.get("team1")).get("image") or "",
        },
        "teamB": {
            "id": match.get("team2_id"),
            "name": match.get("team2") or "-",
            "score": match.get("score2"),
            "logo": match.get("team2_logo") or lol_team_asset(match.get("team2_id"), match.get("team2")).get("image") or "",
        },
        "maps": maps,
        "playerStats": {"teamA": first_team_a, "teamB": first_team_b},
        "mapPlayerStats": map_player_stats,
    }


def build_lol_team_detail(cur: pymysql.cursors.DictCursor, team_key: str) -> Dict[str, Any]:
    teams = build_lol_teams(cur)
    team = next(
        (
            row
            for row in teams
            if str(row.get("teamId") or "").lower() == team_key.lower()
            or str(row.get("name") or "").lower() == team_key.lower()
        ),
        None,
    )
    if not team:
        return {"basic": None, "members": [], "recentMatches": []}

    members = []
    if lol_table_exists(cur, "lol_player_basic"):
        has_stats = lol_table_exists(cur, "lol_game_player_stats")
        active_pb_filter = lol_active_player_sql(cur, "pb", "AND")
        stats_join = """
            LEFT JOIN lol_game_player_stats gps
              ON gps.player_id = pb.player_id
             AND gps.team_id = pb.team_id
        """ if has_stats else ""
        games_expr = "COUNT(gps.game_id)" if has_stats else "0"
        role_expr = "COALESCE(NULLIF(pb.role, ''), MIN(gps.stat_index))" if has_stats else "pb.role"
        members = lol_fetch_all(
            cur,
            f"""
            SELECT
                pb.player_id AS playerId,
                pb.player_name AS name,
                pb.team_name AS teamName,
                {role_expr} AS role_index,
                {games_expr} AS gamesPlayed
            FROM lol_player_basic pb
            {stats_join}
            WHERE pb.team_id = %s
              {active_pb_filter}
            GROUP BY pb.player_id, pb.player_name, pb.team_name, pb.role
            ORDER BY gamesPlayed DESC, pb.player_name
            LIMIT 12
            """,
            (team.get("teamId"),),
        )
    recent = []
    for row in build_lol_matches(cur, limit=50):
        is_team_a = row.get("teamAId") == team.get("teamId")
        is_team_b = row.get("teamBId") == team.get("teamId")
        if not is_team_a and not is_team_b:
            continue
        own_name = row.get("teamA") if is_team_a else row.get("teamB")
        opponent = row.get("teamB") if is_team_a else row.get("teamA")
        winner = row.get("winner") or "-"
        recent.append(
            {
                **row,
                "teamName": own_name,
                "opponent": opponent,
                "result": "胜" if winner == own_name else "负" if winner == opponent else "-",
            }
        )
    for member in members:
        role_index = member.get("role_index")
        member["role"] = str(role_index or "").strip()
        if str(role_index or "").isdigit():
            member["role"] = lol_role_from_stat_index(role_index)
        member["role"] = normalize_lol_role(member["role"])
        asset = lol_player_asset(member.get("name"), team.get("teamId"), team.get("name"))
        if asset.get("role"):
            member["role"] = normalize_lol_role(asset["role"])
        member["position"] = member["role"]
        member["region"] = team.get("region") or "-"
        member["avatar"] = (
            normalize_lol_image(asset.get("avatar"))
            or lol_player_avatar_override(member.get("name"), member.get("playerId"))
            or lol_avatar_placeholder(member.get("name") or member.get("playerId"))
        )

    matches_played = safe_int(team.get("matchesPlayed"))
    wins = safe_int(team.get("wins"))
    win_rate = f"{round((wins / max(1, matches_played)) * 100)}%"
    return {
        "basic": {
            "teamId": team.get("teamId"),
            "teamName": team.get("name"),
            "region": team.get("region"),
            "teamLogo": lol_team_asset(team.get("teamId"), team.get("name")).get("image") or "",
        },
        "rank": {
            "globalRank": team.get("globalRank") or team.get("rank") or "-",
            "regionRank": team.get("regionRank") or "-",
            "score": team.get("rankScore") or 0,
        },
        "stats": {
            "matchesPlayed": matches_played,
            "wins": wins,
            "winRate": win_rate,
            "rankScore": team.get("rankScore") or 0,
        },
        "members": members,
        "recentMatches": recent,
    }


def build_lol_player_detail(cur: pymysql.cursors.DictCursor, player_id: str) -> Dict[str, Any]:
    has_basic = lol_table_exists(cur, "lol_player_basic")
    has_stats = lol_table_exists(cur, "lol_game_player_stats")
    if not has_basic and not has_stats:
        return {"basic": None}

    stats_join = """
        LEFT JOIN lol_game_player_stats gps
          ON gps.player_id = pb.player_id
         AND gps.team_id = pb.team_id
    """ if has_stats else ""
    stats_select = """
        COUNT(gps.game_id) AS games_played,
        SUM(gps.kills) AS kills,
        SUM(gps.deaths) AS deaths,
        SUM(gps.assists) AS assists,
        AVG(gps.cs) AS avg_cs
    """ if has_stats else """
        0 AS games_played,
        0 AS kills,
        0 AS deaths,
        0 AS assists,
        NULL AS avg_cs
    """

    row = None
    if has_basic:
        cur.execute(
            f"""
            SELECT
                pb.player_id,
                pb.player_name,
                pb.team_id,
                pb.team_name,
                pb.role,
                pb.source,
                {stats_select}
            FROM lol_player_basic pb
            {stats_join}
            WHERE pb.player_id = %s
              AND (
                  pb.source = 'lolesports'
                  OR NOT EXISTS (
                      SELECT 1
                      FROM lol_player_basic official
                      WHERE official.player_id = pb.player_id
                        AND official.source = 'lolesports'
                  )
              )
            GROUP BY pb.player_id, pb.player_name, pb.team_id, pb.team_name, pb.role, pb.source
            ORDER BY CASE WHEN pb.source = 'lolesports' THEN 0 ELSE 1 END, games_played DESC
            LIMIT 1
            """,
            (player_id,),
        )
        row = cur.fetchone()
    if not row and has_stats:
        cur.execute(
            """
            SELECT
                gps.player_id,
                MAX(NULLIF(gps.player_name, '')) AS player_name,
                gps.team_id,
                MAX(NULLIF(gps.team_name, '')) AS team_name,
                NULL AS role,
                'lol_game_player_stats' AS source,
                COUNT(gps.game_id) AS games_played,
                SUM(gps.kills) AS kills,
                SUM(gps.deaths) AS deaths,
                SUM(gps.assists) AS assists,
                AVG(gps.cs) AS avg_cs
            FROM lol_game_player_stats gps
            WHERE gps.player_id = %s
            GROUP BY gps.player_id, gps.team_id
            ORDER BY games_played DESC, team_name
            LIMIT 1
            """,
            (player_id,),
        )
        row = cur.fetchone()
    if not row:
        return {"basic": None}
    row = json_row(row)
    team_id = row.get("team_id")
    player_asset = lol_player_asset(
        row.get("player_name"),
        row.get("team_id"),
        row.get("team_name"),
        player_id=row.get("player_id"),
    )
    team_asset = lol_team_asset(row.get("team_id"), row.get("team_name"))
    raw_deaths = safe_int(row.get("deaths"))
    deaths = max(1, raw_deaths)
    kills = safe_int(row.get("kills"))
    assists = safe_int(row.get("assists"))
    games_played = safe_int(row.get("games_played"))
    kda = (safe_int(row.get("kills")) + safe_int(row.get("assists"))) / deaths if games_played else 0
    recent = []
    recent_scope = "none"
    gol_player_id = ""
    if has_stats:
        cur.execute(
            """
            SELECT source_player_url
            FROM lol_game_player_stats
            WHERE player_id = %s
              AND source_player_url IS NOT NULL
              AND source_player_url <> ''
            GROUP BY source_player_url
            ORDER BY COUNT(*) DESC
            LIMIT 8
            """,
            (player_id,),
        )
        for source_row in cur.fetchall():
            gol_player_id = lol_extract_gol_player_id(source_row.get("source_player_url"))
            if gol_player_id:
                break
    career_profile = fetch_lol_gol_career_profile(gol_player_id) if (LOL_EXTERNAL_CAREER_ENABLED and gol_player_id) else {}
    teammates = []
    teammate_scope = "none"
    if team_id and has_basic:
        active_teammate_filter = lol_active_player_sql(cur, "pb", "AND")
        teammates = lol_fetch_all(
            cur,
            f"""
            SELECT
                pb.player_id AS teammate_id,
                pb.player_name AS teammate_name,
                pb.team_id AS team_id,
                pb.team_name AS team_name,
                pb.role AS role,
                NULL AS role_index,
                0 AS gamesTogether,
                pb.avatar AS avatar
            FROM lol_player_basic pb
            WHERE pb.team_id = %s
              AND pb.player_id <> %s
              {active_teammate_filter}
            ORDER BY
                CASE UPPER(pb.role)
                    WHEN 'TOP' THEN 1
                    WHEN 'JUG' THEN 2
                    WHEN 'JUNGLE' THEN 2
                    WHEN 'MID' THEN 3
                    WHEN 'BOT' THEN 4
                    WHEN 'BOTTOM' THEN 4
                    WHEN 'SUP' THEN 5
                    WHEN 'SUPPORT' THEN 5
                    ELSE 9
                END,
                pb.player_name
            LIMIT 4
            """,
            (team_id, player_id),
        )
        if teammates:
            teammate_scope = "current_team"

    region = resolve_lol_team_region(row.get("team_id"), row.get("team_name"))
    rank_info = build_lol_player_rank(cur, str(row.get("player_id") or ""), region)
    advanced_stats: List[Dict[str, Any]] = []
    champion_stats: List[Dict[str, Any]] = []
    honors: List[Dict[str, Any]] = []
    performance_metrics: List[Dict[str, Any]] = []
    win_rate_text = "-"
    kill_participation_text = "-"
    kill_share_text = "-"
    death_share_text = "-"
    champion_pool_count = 0
    avg_kills = "-"
    avg_deaths = "-"
    avg_assists = "-"
    career_teams: List[Dict[str, Any]] = []
    recent_form: Dict[str, Any] = {
        "games": 0,
        "wins": 0,
        "winRate": "-",
        "kda": "-",
        "avgCs": "-",
        "champions": [],
    }

    current_team_id = str(team_id or "").strip()
    career_team_map: Dict[str, Dict[str, Any]] = {}
    if has_basic:
        cur.execute(
            """
            SELECT
                team_id,
                MAX(team_name) AS team_name,
                MAX(role) AS role,
                MAX(source) AS source
            FROM lol_player_basic
            WHERE player_id = %s
              AND (
                  source = 'lolesports'
                  OR NOT EXISTS (
                      SELECT 1
                      FROM lol_player_basic official
                      WHERE official.player_id = lol_player_basic.player_id
                        AND official.source = 'lolesports'
                  )
              )
            GROUP BY team_id
            """,
            (player_id,),
        )
        career_team_map = {
            str(item.get("team_id") or ""): json_row(item)
            for item in cur.fetchall()
            if str(item.get("team_id") or "")
        }
    official_asset_team_name = str(player_asset.get("teamName") or "").strip()
    if official_asset_team_name:
        official_asset_team_id = re.sub(r"[^A-Za-z0-9]+", "_", official_asset_team_name.strip().lower()).strip("_")
        if official_asset_team_id:
            career_team_map.setdefault(
                official_asset_team_id,
                {
                    "team_id": official_asset_team_id,
                    "team_name": official_asset_team_name,
                    "role": player_asset.get("role") or row.get("role") or "",
                    "source": "lolesports-assets",
                },
            )
    if current_team_id:
        current_base = career_team_map.setdefault(
            current_team_id,
            {
                "team_id": current_team_id,
                "team_name": row.get("team_name") or current_team_id,
                "role": row.get("role") or "",
                "source": row.get("source") or "mysql",
            },
        )
        current_base.update(
            {
                "team_id": current_team_id,
                "team_name": row.get("team_name") or current_base.get("team_name") or current_team_id,
                "role": row.get("role") or current_base.get("role") or "",
                "source": row.get("source") or current_base.get("source") or "mysql",
                "isCurrent": True,
            }
        )
    elif official_asset_team_name and official_asset_team_id:
        career_team_map[official_asset_team_id].update(
            {
                "team_id": official_asset_team_id,
                "team_name": official_asset_team_name,
                "role": player_asset.get("role") or row.get("role") or "",
                "source": "lolesports-assets",
                "isCurrent": True,
            }
        )
    if has_stats:
        cur.execute(
            """
            SELECT
                gps.team_id,
                MAX(gps.team_name) AS team_name,
                MAX(gps.stat_index) AS stat_index,
                COUNT(*) AS games,
                MIN(COALESCE(DATE(mr.match_time), mr.match_date)) AS first_seen,
                MAX(COALESCE(DATE(mr.match_time), mr.match_date)) AS last_seen
            FROM lol_game_player_stats gps
            LEFT JOIN lol_match_result mr ON mr.match_id = gps.match_id
            WHERE gps.player_id = %s
            GROUP BY gps.team_id
            """,
            (player_id,),
        )
        for item in cur.fetchall():
            item = json_row(item)
            key = str(item.get("team_id") or "")
            if not key:
                continue
            games = safe_int(item.get("games"))
            if key not in career_team_map and games < LOL_CAREER_TEAM_MIN_GAMES:
                continue
            base = career_team_map.setdefault(
                key,
                {
                    "team_id": key,
                    "team_name": item.get("team_name") or key,
                    "role": lol_role_from_stat_index(item.get("stat_index")),
                    "source": "lol_game_player_stats",
                },
            )
            base.update(
                {
                    "team_name": base.get("team_name") or item.get("team_name"),
                    "role": base.get("role") or lol_role_from_stat_index(item.get("stat_index")),
                    "games": games,
                    "firstSeen": item.get("first_seen") or "",
                    "lastSeen": item.get("last_seen") or "",
                }
            )
    career_teams = sorted(
        [
            {
                "teamId": item.get("team_id") or team_key,
                "teamName": item.get("team_name") or team_key,
                "role": normalize_lol_role(
                    item.get("role")
                    or lol_player_asset(row.get("player_name"), item.get("team_id") or team_key, item.get("team_name")).get("role")
                ),
                "games": safe_int(item.get("games")),
                "firstSeen": item.get("firstSeen") or "",
                "lastSeen": item.get("lastSeen") or "",
                "localFirstSeen": item.get("firstSeen") or "",
                "localLastSeen": item.get("lastSeen") or "",
                "tenureStart": item.get("firstSeen") or "",
                "tenureEnd": "至今" if item.get("isCurrent") else item.get("lastSeen") or "",
                "isCurrent": bool(item.get("isCurrent")),
                "gamesLabel": f"本站 {safe_int(item.get('games'))} 局" if safe_int(item.get("games")) else "本站暂无比赛记录",
                "sourceLabel": "当前队伍" if item.get("isCurrent") else "历史比赛记录",
                "teamLogo": lol_team_asset(item.get("team_id") or team_key, item.get("team_name")).get("image") or "",
            }
            for team_key, item in career_team_map.items()
        ],
        key=lambda item: (
            1 if item.get("isCurrent") else 0,
            str(item.get("lastSeen") or ""),
            safe_int(item.get("games")),
            str(item.get("teamName") or ""),
        ),
        reverse=True,
    )
    if career_profile.get("careerStart") and career_teams:
        for item in career_teams:
            if lol_gol_profile_matches_team(career_profile, item.get("teamId"), item.get("teamName")):
                item["tenureStart"] = career_profile.get("careerStart")
                item["tenureEnd"] = "至今"
                item["sourceLabel"] = career_profile.get("source") or "外部履历"
                item["careerEvidence"] = career_profile.get("oldestTournament") or career_profile.get("oldestMatchTitle") or ""
                break

    if team_id and has_stats:
        cur.execute(
            """
            WITH player_games AS (
                SELECT
                    gps.game_id,
                    gps.match_id,
                    gps.team_id,
                    gps.team_name,
                    gps.kills,
                    gps.deaths,
                    gps.assists,
                    gps.cs,
                    gps.champion,
                    SUM(team_gps.kills) AS team_kills,
                    SUM(team_gps.deaths) AS team_deaths,
                    SUM(team_gps.cs) AS team_cs,
                    mr.winner
                FROM lol_game_player_stats gps
                JOIN lol_game_player_stats team_gps
                  ON team_gps.game_id = gps.game_id
                 AND team_gps.team_side = gps.team_side
                LEFT JOIN lol_match_result mr ON mr.match_id = gps.match_id
                WHERE gps.player_id = %s
                  AND gps.team_id = %s
                GROUP BY
                    gps.game_id, gps.match_id, gps.team_id, gps.team_name,
                    gps.kills, gps.deaths, gps.assists, gps.cs, gps.champion, mr.winner
            )
            SELECT
                COUNT(*) AS games,
                SUM(CASE WHEN winner = team_name THEN 1 ELSE 0 END) AS wins,
                SUM(kills) AS kills,
                SUM(deaths) AS deaths,
                SUM(assists) AS assists,
                SUM(cs) AS cs,
                SUM(team_kills) AS team_kills,
                SUM(team_deaths) AS team_deaths,
                SUM(team_cs) AS team_cs,
                COUNT(DISTINCT champion) AS champion_pool
            FROM player_games
            """,
            (player_id, team_id),
        )
        advanced_row = json_row(cur.fetchone() or {})
        stat_games = safe_int(advanced_row.get("games"))
        stat_wins = safe_int(advanced_row.get("wins"))
        stat_kills = safe_int(advanced_row.get("kills"))
        stat_deaths = safe_int(advanced_row.get("deaths"))
        stat_assists = safe_int(advanced_row.get("assists"))
        stat_cs = safe_int(advanced_row.get("cs"))
        stat_team_kills = safe_int(advanced_row.get("team_kills"))
        stat_team_deaths = safe_int(advanced_row.get("team_deaths"))
        stat_team_cs = safe_int(advanced_row.get("team_cs"))
        champion_pool_count = safe_int(advanced_row.get("champion_pool"))

        win_rate = (stat_wins / stat_games * 100) if stat_games else 0
        kill_participation = ((stat_kills + stat_assists) / max(1, stat_team_kills) * 100) if stat_games else 0
        kill_share = (stat_kills / max(1, stat_team_kills) * 100) if stat_games else 0
        death_share = (stat_deaths / max(1, stat_team_deaths) * 100) if stat_games else 0
        cs_share = (stat_cs / max(1, stat_team_cs) * 100) if stat_games else 0
        avg_kills = format_metric(stat_kills / max(1, stat_games), 1) if stat_games else "-"
        avg_deaths = format_metric(stat_deaths / max(1, stat_games), 1) if stat_games else "-"
        avg_assists = format_metric(stat_assists / max(1, stat_games), 1) if stat_games else "-"
        win_rate_text = f"{win_rate:.1f}%" if stat_games else "-"
        kill_participation_text = f"{kill_participation:.1f}%" if stat_games else "-"
        kill_share_text = f"{kill_share:.1f}%" if stat_games else "-"
        death_share_text = f"{death_share:.1f}%" if stat_games else "-"

        advanced_stats = [
            {"key": "winRate", "label": "胜率", "value": win_rate_text, "hint": f"{stat_wins}/{stat_games} 局"},
            {"key": "kda", "label": "KDA", "value": f"{kda:.2f}" if stat_games else "-", "hint": f"{kills}/{raw_deaths}/{assists}"},
            {"key": "killParticipation", "label": "参团率", "value": kill_participation_text, "hint": "K+A / 队伍击杀"},
            {"key": "killShare", "label": "击杀占比", "value": kill_share_text, "hint": "个人击杀 / 队伍击杀"},
            {"key": "deathShare", "label": "死亡占比", "value": death_share_text, "hint": "个人死亡 / 队伍死亡"},
            {"key": "csShare", "label": "补刀占比", "value": f"{cs_share:.1f}%" if stat_games else "-", "hint": "个人 CS / 队伍 CS"},
            {"key": "championPool", "label": "英雄池", "value": str(champion_pool_count), "hint": "不同英雄数"},
            {"key": "avgKdaLine", "label": "场均 K/D/A", "value": f"{avg_kills}/{avg_deaths}/{avg_assists}", "hint": "单局均值"},
        ]

        performance_metrics = [
            {"metric": "WR", "value": win_rate_text, "avg_value": "50%", "good_end": "75%"},
            {"metric": "KDA", "value": f"{kda:.2f}" if stat_games else "-", "avg_value": "3.00", "good_end": "8.00"},
            {"metric": "KP", "value": kill_participation_text, "avg_value": "60%", "good_end": "90%"},
            {"metric": "KILL%", "value": kill_share_text, "avg_value": "22%", "good_end": "45%"},
            {"metric": "CS%", "value": f"{cs_share:.1f}%" if stat_games else "-", "avg_value": "20%", "good_end": "35%"},
            {"metric": "DTH%", "value": death_share_text, "avg_value": "20%", "good_end": "5%", "lower_better": "1"},
            {"metric": "POOL", "value": str(champion_pool_count), "avg_value": "5", "good_end": "12"},
            {"metric": "AVG KDA", "value": f"{avg_kills}/{avg_deaths}/{avg_assists}", "avg_value": "2.5", "good_end": "6"},
        ]

        champion_stats = lol_fetch_all(
            cur,
            """
            SELECT
                champion,
                COUNT(*) AS games,
                SUM(kills) AS kills,
                SUM(deaths) AS deaths,
                SUM(assists) AS assists,
                AVG(cs) AS avgCs
            FROM lol_game_player_stats
            WHERE player_id = %s
              AND team_id = %s
            GROUP BY champion
            ORDER BY games DESC, (SUM(kills) + SUM(assists)) / GREATEST(1, SUM(deaths)) DESC, champion
            LIMIT 12
            """,
            (player_id, team_id),
        )
        for champ in champion_stats:
            c_kills = safe_int(champ.get("kills"))
            c_deaths = safe_int(champ.get("deaths"))
            c_assists = safe_int(champ.get("assists"))
            champ["kda"] = f"{((c_kills + c_assists) / max(1, c_deaths)):.2f}"
            champ["avgCs"] = format_metric(champ.get("avgCs"), 1)

        recent_current = lol_fetch_all(
            cur,
            """
            SELECT
                gps.game_id,
                gps.match_id,
                mr.match_date AS ts_text,
                mr.event_name AS tournament_name,
                mr.stage,
                gps.game_number,
                gps.champion,
                gps.kills,
                gps.deaths,
                gps.assists,
                gps.cs,
                CASE
                    WHEN mr.team1_id = gps.team_id THEN mr.team2
                    ELSE mr.team1
                END AS opponent_team_name,
                CASE
                    WHEN mr.team1_id = gps.team_id THEN mr.score1
                    ELSE mr.score2
                END AS home_score,
                CASE
                    WHEN mr.team1_id = gps.team_id THEN mr.score2
                    ELSE mr.score1
                END AS opponent_score,
                CASE
                    WHEN mr.winner = gps.team_name THEN '胜'
                    WHEN mr.winner IS NULL OR mr.winner = '' THEN '-'
                    ELSE '负'
                END AS result
            FROM lol_game_player_stats gps
            LEFT JOIN lol_match_result mr ON mr.match_id = gps.match_id
            WHERE gps.player_id = %s
              AND gps.team_id = %s
            ORDER BY COALESCE(mr.match_time, mr.match_date) DESC, gps.game_number DESC, gps.game_id DESC
            LIMIT 20
            """,
            (player_id, team_id),
        )
        if recent_current:
            recent = recent_current
            recent_scope = "current_team"
        recent_sample = recent[:10]
        sample_games = len(recent_sample)
        sample_wins = sum(1 for item in recent_sample if item.get("result") == "胜")
        sample_kills = sum(safe_int(item.get("kills")) for item in recent_sample)
        sample_deaths = sum(safe_int(item.get("deaths")) for item in recent_sample)
        sample_assists = sum(safe_int(item.get("assists")) for item in recent_sample)
        sample_cs = sum(safe_int(item.get("cs")) for item in recent_sample)
        champion_counts: Dict[str, int] = {}
        for item in recent_sample:
            champion = str(item.get("champion") or "").strip()
            if champion:
                champion_counts[champion] = champion_counts.get(champion, 0) + 1
        recent_form = {
            "games": sample_games,
            "wins": sample_wins,
            "winRate": f"{(sample_wins / max(1, sample_games) * 100):.1f}%" if sample_games else "-",
            "kda": f"{((sample_kills + sample_assists) / max(1, sample_deaths)):.2f}" if sample_games else "-",
            "avgCs": format_metric(sample_cs / max(1, sample_games), 1) if sample_games else "-",
            "champions": [
                {"champion": champion, "games": count}
                for champion, count in sorted(champion_counts.items(), key=lambda item: (-item[1], item[0]))[:5]
            ],
        }

    honor_team_ids = [item["teamId"] for item in career_teams if item.get("teamId")]
    if honor_team_ids:
        placeholders = ", ".join(["%s"] * len(honor_team_ids))
        honors = lol_fetch_all(
            cur,
            f"""
            SELECT
                mr.event_id AS tt_id,
                mr.event_name AS tt_name,
                MAX(mr.match_date) AS start_time,
                CASE
                    WHEN mr.team1_id IN ({placeholders}) THEN mr.team1
                    ELSE mr.team2
                END AS team_name,
                CASE
                    WHEN mr.winner = CASE WHEN mr.team1_id IN ({placeholders}) THEN mr.team1 ELSE mr.team2 END THEN '冠军'
                    ELSE '亚军'
                END AS rank_desc,
                'S' AS grade
            FROM lol_match_result mr
            WHERE (mr.team1_id IN ({placeholders}) OR mr.team2_id IN ({placeholders}))
              AND LOWER(TRIM(COALESCE(mr.stage, ''))) IN ('final', 'finals', 'grand final', 'grand finals')
              AND mr.score1 IS NOT NULL
              AND mr.score2 IS NOT NULL
              AND mr.score1 + mr.score2 > 0
            GROUP BY mr.event_id, mr.event_name, team_name, rank_desc
            ORDER BY start_time DESC
            LIMIT 30
            """,
            (*honor_team_ids, *honor_team_ids, *honor_team_ids, *honor_team_ids),
        )
    if career_profile.get("honors"):
        matched_honor_team = ""
        for item in career_teams:
            if lol_gol_profile_matches_team(career_profile, item.get("teamId"), item.get("teamName")):
                matched_honor_team = item.get("teamName") or ""
                break
        merged_honors: List[Dict[str, Any]] = []
        seen_honor_keys: Set[str] = set()
        for honor in [*honors, *career_profile.get("honors", [])]:
            honor = json_row(honor)
            if honor.get("source") == "Games of Legends" and not honor.get("team_name"):
                honor["team_name"] = matched_honor_team or row.get("team_name") or ""
            key = "|".join(
                [
                    str(honor.get("start_time") or ""),
                    lol_asset_key(honor.get("tt_name")),
                    str(honor.get("rank_desc") or honor.get("rank") or ""),
                ]
            )
            if key in seen_honor_keys:
                continue
            seen_honor_keys.add(key)
            merged_honors.append(honor)
        honors = sorted(
            merged_honors,
            key=lambda item: (safe_int(str(item.get("start_time") or "")[:4]), str(item.get("tt_name") or "")),
            reverse=True,
        )[:80]
    normalized_teammates = []
    for mate in teammates:
        role = str(mate.get("role") or "").strip()
        if not role:
            role = lol_role_from_stat_index(mate.get("role_index"))
        asset = lol_player_asset(mate.get("teammate_name"), mate.get("team_id"), mate.get("team_name"))
        if asset.get("role"):
            role = normalize_lol_role(asset["role"])
        normalized_teammates.append(
            {
                **mate,
                "role": role,
                "region": region,
                "teamLogo": lol_team_asset(mate.get("team_id"), mate.get("team_name")).get("image") or "",
                "avatar": (
                    normalize_lol_image(mate.get("avatar"))
                    or normalize_lol_image(asset.get("avatar"))
                    or lol_player_avatar_override(mate.get("teammate_name"), mate.get("teammate_id"))
                    or lol_avatar_placeholder(mate.get("teammate_name") or mate.get("teammate_id"))
                ),
                "country_logo": "",
            }
        )

    return {
        "playerId": row.get("player_id"),
        "teammateScope": teammate_scope,
        "recentMatchesScope": recent_scope,
        "rank": rank_info,
        "basic": {
            "name": row.get("player_name"),
            "avatar": (
                normalize_lol_image(player_asset.get("avatar"))
                or lol_player_avatar_override(row.get("player_name"), row.get("player_id"))
                or lol_avatar_placeholder(row.get("player_name") or row.get("player_id"))
            ),
            "teamId": row.get("team_id"),
            "teamName": row.get("team_name"),
            "teamLogo": team_asset.get("image") or "",
            "region": region,
            "globalRank": rank_info.get("globalRank"),
            "regionRank": rank_info.get("regionRank"),
            "rankScore": rank_info.get("rankScore"),
            "position": normalize_lol_role(player_asset.get("role") or row.get("role") or "-"),
            "rating": f"{kda:.2f}" if games_played else "-",
            "kda": f"{kda:.2f}" if games_played else "-",
            "impact": games_played,
            "gamesPlayed": games_played,
            "kd": f"{kills}/{raw_deaths}/{assists}" if games_played else "-",
            "adr": "-",
        },
        "summary": {
            "games_played": games_played,
            "kills": kills,
            "deaths": raw_deaths,
            "assists": assists,
            "kda": f"{kda:.2f}" if games_played else "-",
            "avgCs": format_metric(row.get("avg_cs"), 1) if games_played else "-",
            "winRate": win_rate_text,
            "killParticipation": kill_participation_text,
            "killShare": kill_share_text,
            "deathShare": death_share_text,
            "championPool": champion_pool_count,
            "avgKills": avg_kills,
            "avgDeaths": avg_deaths,
            "avgAssists": avg_assists,
        },
        "advancedStats": advanced_stats,
        "championStats": champion_stats,
        "performanceMetrics": performance_metrics,
        "recentForm": recent_form,
        "careerProfile": career_profile,
        "careerTeams": career_teams,
        "honors": honors,
        "recentMatches": recent,
        "teammates": normalized_teammates,
        "maps": [],
        "equipment": [],
        "milestones": [],
    }


@router.get("/api/lol/dataset")
def lol_dataset() -> Dict[str, Any]:
    return {"success": True, "data": build_lol_dataset()}


@router.get("/api/lol/matches")
def lol_matches(
    view: str = Query("fixture"),
    date: str = Query(""),
    tier: str = Query("all"),
    limit: int = Query(3000, ge=1, le=10000),
    offset: int = Query(0, ge=0, le=1000000),
) -> Dict[str, Any]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            rows = build_lol_matches_filtered(
                cur,
                view=view,
                date_filter=date,
                limit=limit,
                offset=offset,
            )
    return {
        "success": True,
        "data": {
            "updatedAt": safe_datetime(datetime.now()),
            "matches": rows,
            "filters": {
                "view": view,
                "date": date,
                "tier": tier,
                "limit": limit,
                "offset": offset,
            },
        },
    }


@router.get("/api/lol/player/{player_id}")
def lol_player_detail(player_id: str) -> Dict[str, Any]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            detail = build_lol_player_detail(cur, player_id)
    return {"success": True, "data": detail}


@router.get("/api/lol/team/{team_key}")
def lol_team_detail(team_key: str) -> Dict[str, Any]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            detail = build_lol_team_detail(cur, team_key)
    return {"success": True, "data": detail}


@router.get("/api/lol/match/{match_id}")
def lol_match_detail(match_id: str) -> Dict[str, Any]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            detail = build_lol_match_detail(cur, match_id)
    return {"success": True, "data": detail}
