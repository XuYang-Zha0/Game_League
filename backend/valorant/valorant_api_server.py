from __future__ import annotations

import os
import math
import hashlib
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse

import pymysql
import requests
from fastapi import APIRouter, Query, Response


def load_local_env() -> None:
    env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as env_file:
        for line in env_file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


load_local_env()

DB_CONFIG = {
    "host": os.getenv("CS_DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("CS_DB_PORT", "3306")),
    "user": os.getenv("CS_DB_USER", "root"),
    "password": os.getenv("CS_DB_PASSWORD", ""),
    "database": os.getenv("CS_DB_NAME", "esports"),
    "charset": "utf8mb4",
    "autocommit": True,
    "cursorclass": pymysql.cursors.DictCursor,
}

router = APIRouter()

IMAGE_CACHE_DIR = Path(__file__).resolve().parent / "image_cache"
IMAGE_CACHE_CONTROL = "public, max-age=2592000, immutable"
IMAGE_PROXY_SESSION = requests.Session()
IMAGE_PROXY_SESSION.headers.update({"User-Agent": "GameLeagueValorantImageProxy/0.2"})
ALLOWED_IMAGE_HOSTS = {"owcdn.net", "api.konect.gg"}
ALLOWED_LIQUIPEDIA_HOSTS = {"liquipedia.net"}


def get_conn() -> pymysql.Connection:
    return pymysql.connect(**DB_CONFIG)


def json_scalar(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    return value


def json_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {key: json_scalar(value) for key, value in row.items()}


def fetch_all(
    cur: pymysql.cursors.DictCursor, sql: str, params: Tuple[Any, ...] = ()
) -> List[Dict[str, Any]]:
    cur.execute(sql, params)
    return [json_row(row) for row in cur.fetchall()]


def table_exists(cur: pymysql.cursors.DictCursor, table_name: str) -> bool:
    cur.execute("SHOW TABLES LIKE %s", (table_name,))
    return cur.fetchone() is not None


def active_relation_sql(cur: pymysql.cursors.DictCursor, alias: str = "tpr") -> str:
    if not table_exists(cur, "valorant_team_player_relation"):
        return ""
    try:
        cur.execute("SHOW COLUMNS FROM valorant_team_player_relation LIKE 'is_active'")
        if cur.fetchone() is None:
            return ""
    except Exception:
        return ""
    return f"AND COALESCE({alias}.is_active, 1) = 1"


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_float(value: Any, default: float = 0.0) -> float:
    text = str(value or "").replace("%", "").strip()
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


def safe_percent_ratio(value: Any, default: float = 0.0) -> float:
    text = str(value or "").strip()
    if not text:
        return default
    number = safe_float(text, default)
    if "%" in text or number > 1:
        return number / 100
    return number


def safe_datetime_now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def status_code(value: Any) -> int:
    text = str(value or "").strip().lower()
    if text in {"completed", "finished", "result"}:
        return 2
    if text in {"live", "in_progress", "ongoing"}:
        return 1
    return 0


def status_text(value: Any) -> str:
    code = status_code(value)
    if code == 2:
        return "已结束"
    if code == 1:
        return "进行中"
    return "未开始"


def format_percent(value: float) -> str:
    return f"{round(value * 100)}%"


def event_status(row: Dict[str, Any]) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    start = str(row.get("event_start_time") or "")
    end = str(row.get("event_end_time") or "")
    if start and start > now:
        return "即将开始"
    if end and end < now:
        return "已结束"
    return "进行中"


def resolve_score(score1: Any, score2: Any) -> str:
    a = "" if score1 is None else str(score1).strip()
    b = "" if score2 is None else str(score2).strip()
    if not a and not b:
        return "-:-"
    return f"{a or 0}:{b or 0}"


def normalize_view(value: str) -> str:
    text = str(value or "").strip().lower()
    return text if text in {"fixture", "result", "all"} else "fixture"


def fixture_visibility_sql(cur: pymysql.cursors.DictCursor, alias: str = "s") -> Tuple[str, List[str]]:
    joins: List[str] = []
    where = [
        f"LOWER(COALESCE({alias}.status, '')) NOT IN ('completed', 'finished')",
        f"({alias}.match_date IS NULL OR {alias}.match_date >= CURDATE())",
    ]
    if table_exists(cur, "valorant_match_result"):
        joins.append(f"LEFT JOIN valorant_match_result vmr ON vmr.match_id = {alias}.match_id")
        where.append("vmr.match_id IS NULL")
    if table_exists(cur, "valorant_match_detail"):
        joins.append(f"LEFT JOIN valorant_match_detail vmd ON vmd.match_id = {alias}.match_id")
        where.append("LOWER(COALESCE(vmd.status, '')) NOT IN ('completed', 'finished')")
    return "\n".join(joins), where


PLACEHOLDER_IMAGE_MARKERS = (
    "/img/vlr/tmp/vlr.png",
    "/img/base/ph/sil.png",
    "/null.png",
)


def is_placeholder_image(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    return any(marker in text for marker in PLACEHOLDER_IMAGE_MARKERS)


def proxied_image_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text or is_placeholder_image(text):
        return ""
    if text.startswith("/api/valorant/image?url="):
        return text
    if text.startswith("//"):
        text = f"https:{text}"
    parsed = urlparse(text)
    if is_allowed_proxy_image(parsed):
        return f"/api/valorant/image?url={quote(text, safe='')}"
    return text


def is_allowed_proxy_image(parsed: Any) -> bool:
    if parsed.scheme not in {"http", "https"}:
        return False
    host = parsed.netloc.lower()
    if host in ALLOWED_IMAGE_HOSTS:
        return True
    if host in ALLOWED_LIQUIPEDIA_HOSTS and parsed.path.startswith("/commons/images/"):
        return True
    return False


def image_cache_paths(url: str) -> Tuple[Path, Path]:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return IMAGE_CACHE_DIR / f"{digest}.img", IMAGE_CACHE_DIR / f"{digest}.type"


def read_cached_image(url: str) -> Optional[Tuple[bytes, str]]:
    data_path, type_path = image_cache_paths(url)
    if not data_path.exists() or not type_path.exists():
        return None
    try:
        content = data_path.read_bytes()
        content_type = type_path.read_text(encoding="utf-8").strip() or "image/png"
    except OSError:
        return None
    if not content or not content_type.startswith("image/"):
        return None
    return content, content_type


def write_cached_image(url: str, content: bytes, content_type: str) -> None:
    if not content or not content_type.startswith("image/"):
        return
    try:
        IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        data_path, type_path = image_cache_paths(url)
        tmp_path = data_path.with_suffix(".tmp")
        tmp_path.write_bytes(content)
        tmp_path.replace(data_path)
        type_path.write_text(content_type, encoding="utf-8")
    except OSError:
        return


VALORANT_AGENT_ROLE_MAP = {
    "astra": "烟位",
    "brimstone": "烟位",
    "clove": "烟位",
    "harbor": "烟位",
    "omen": "烟位",
    "viper": "烟位",
    "jett": "决斗",
    "neon": "决斗",
    "phoenix": "决斗",
    "raze": "决斗",
    "reyna": "决斗",
    "waylay": "决斗",
    "iso": "决斗",
    "yoru": "决斗",
    "breach": "先锋",
    "fade": "先锋",
    "gekko": "先锋",
    "kayo": "先锋",
    "kay/o": "先锋",
    "skye": "先锋",
    "sova": "先锋",
    "tejo": "先锋",
    "chamber": "哨位",
    "cypher": "哨位",
    "deadlock": "哨位",
    "killjoy": "哨位",
    "sage": "哨位",
    "vyse": "哨位",
    "veto": "哨位",
}


def split_agents(value: Any) -> List[str]:
    text = str(value or "").replace("|", ",")
    return [item.strip() for item in text.split(",") if item.strip()]


def agent_text(value: Any) -> str:
    return ", ".join(split_agents(value))


def agent_role_text(value: Any) -> str:
    roles: List[str] = []
    for agent in split_agents(value):
        role = VALORANT_AGENT_ROLE_MAP.get(agent.strip().lower())
        if role and role not in roles:
            roles.append(role)
    return " / ".join(roles)


def build_tournaments(cur: pymysql.cursors.DictCursor) -> List[Dict[str, Any]]:
    if not table_exists(cur, "valorant_event_basic"):
        return []
    rows = fetch_all(
        cur,
        """
        SELECT event_id, event_slug, event_name, region, tier, event_logo,
               event_start_time, event_end_time, source_event_url, fetched_at
        FROM valorant_event_basic
        ORDER BY COALESCE(event_start_time, fetched_at) DESC, event_name
        LIMIT 1000
        """,
    )
    return [
        {
            "eventId": row.get("event_id") or "",
            "name": row.get("event_name") or "-",
            "tier": row.get("tier") or "B",
            "region": row.get("region") or "International",
            "start": str(row.get("event_start_time") or "")[:10],
            "status": event_status(row),
            "prize": "-",
            "logo": row.get("event_logo") or "",
            "sourceUrl": row.get("source_event_url") or "",
        }
        for row in rows
    ]


def build_teams(cur: pymysql.cursors.DictCursor) -> List[Dict[str, Any]]:
    if not table_exists(cur, "valorant_team_basic"):
        return []
    rows = fetch_all(
        cur,
        """
        SELECT team_id, team_slug, team_name, country, region, team_logo, source_team_url
        FROM valorant_team_basic
        ORDER BY team_name
        LIMIT 5000
        """,
    )
    return [
        {
            "teamId": row.get("team_id") or "",
            "name": row.get("team_name") or "-",
            "region": row.get("region") or row.get("country") or "-",
            "country": row.get("country") or "",
            "logo": proxied_image_url(row.get("team_logo")),
            "teamLogo": proxied_image_url(row.get("team_logo")),
            "coach": "-",
            "style": "VALORANT",
            "form": "-",
            "sourceUrl": row.get("source_team_url") or "",
        }
        for row in rows
        if row.get("team_name")
    ]


def player_summary_map(cur: pymysql.cursors.DictCursor) -> Dict[str, Dict[str, Any]]:
    if not table_exists(cur, "valorant_player_stats_summary"):
        return {}
    rows = fetch_all(
        cur,
        """
        SELECT *
        FROM valorant_player_stats_summary
        """,
    )
    return {str(row.get("player_id") or ""): row for row in rows}


VALORANT_RANK_START_DATE = date(2023, 1, 1)
VALORANT_MAJOR_REGIONS = {"CN", "Pacific", "EMEA", "Americas", "International"}


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def parse_api_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:19] if " " in text else text[:10], fmt).date()
        except ValueError:
            continue
    return None


def recency_decay(days_ago: int, half_life_days: float, floor: float = 0.0) -> float:
    safe_days = max(0, int(days_ago))
    return floor + (1 - floor) * (0.5 ** (safe_days / max(1.0, half_life_days)))


def valorant_event_profile(event_name: Any, stage: Any = "") -> Dict[str, Any]:
    text = str(event_name or "").upper()
    stage_text = str(stage or "").upper()
    profile = {
        "weight": 0.72,
        "tier_bonus": 0.0,
        "tier_label": "Open",
        "stage_multiplier": 1.0,
    }

    is_game_changers = "GAME CHANGERS" in text
    is_challengers = "CHALLENGERS" in text and not is_game_changers
    if is_game_changers:
        profile.update({"weight": 0.74, "tier_bonus": 28.0, "tier_label": "Game Changers"})
    if is_challengers:
        profile.update({"weight": 0.84, "tier_bonus": 48.0, "tier_label": "Challengers"})
    if "ASCENSION" in text and not is_game_changers:
        profile.update({"weight": 1.02, "tier_bonus": 95.0, "tier_label": "Ascension"})
    if not is_game_changers and (text.startswith("VCT ") or " VCT " in text or "VALORANT CHINA EVOLUTION" in text):
        profile.update({"weight": 1.18, "tier_bonus": 170.0, "tier_label": "VCT"})
    is_global_international = (
        "VALORANT MASTERS" in text
        or "VCT MASTERS" in text
        or text.startswith("MASTERS ")
        or "VALORANT CHAMPIONS" in text
        or text.startswith("CHAMPIONS ")
        or "LOCK//IN" in text
    )
    if is_global_international and not is_game_changers and not is_challengers:
        profile.update({"weight": 1.42, "tier_bonus": 250.0, "tier_label": "International"})
    if any(token in text for token in ("OFF SEASON", "OFF-SEASON", "OFF//SEASON", "SHOWMATCH", "COLLEGIATE")):
        profile["weight"] = min(float(profile["weight"]) * 0.68, 0.78)
        profile["tier_bonus"] = min(float(profile["tier_bonus"]) * 0.35, 24.0)
        profile["tier_label"] = "Offseason"
    if "QUALIFIER" in text or "QUALIFIERS" in text or "OPEN QUAL" in text:
        profile["weight"] *= 0.78
        profile["tier_bonus"] *= 0.65
        if profile["tier_label"] == "Open":
            profile["tier_label"] = "Qualifier"

    if any(token in stage_text for token in ("GRAND FINAL", "FINAL")):
        profile["stage_multiplier"] = 1.18
    elif any(token in stage_text for token in ("PLAYOFF", "SEMIFINAL", "QUARTERFINAL", "LOWER", "UPPER")):
        profile["stage_multiplier"] = 1.1
    elif "GROUP" in stage_text or "SWISS" in stage_text:
        profile["stage_multiplier"] = 0.96
    return profile


def valorant_event_weight(event_name: Any) -> float:
    return float(valorant_event_profile(event_name).get("weight") or 0.72)


def player_rank_score(summary: Dict[str, Any]) -> float:
    if not summary:
        return 0.0
    rounds = max(0, safe_int(summary.get("rounds")))
    rating = safe_float(summary.get("rating"))
    acs = safe_float(summary.get("acs"))
    adr = safe_float(summary.get("adr"))
    kd = safe_float(summary.get("kd"))
    kpr = safe_float(summary.get("kpr"))
    fkpr = safe_float(summary.get("fkpr"))
    fdpr = safe_float(summary.get("fdpr"))
    kast = safe_percent_ratio(summary.get("kast"))
    sample_factor = min(1.0, math.sqrt(rounds / 420)) if rounds else 0.0
    entry_delta = (fkpr - fdpr) * 420
    base = (
        rating * 540
        + acs * 1.15
        + adr * 1.05
        + kd * 85
        + kpr * 115
        + (kast - 0.68) * 220
        + entry_delta
    )
    score = base * (0.58 + 0.42 * sample_factor) + min(rounds, 1600) * 0.045
    return round(score, 2) if score > 0 else 0.0


def build_players(
    cur: pymysql.cursors.DictCursor,
    limit: int = 10000,
    team_rank_rows: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    if not table_exists(cur, "valorant_player_basic"):
        return []
    summaries = player_summary_map(cur)
    team_strength_by_name = {
        str(row.get("name") or "").strip(): safe_float(row.get("rankScore") or row.get("points"))
        for row in team_rank_rows or []
        if row.get("name")
    }
    team_region_by_name = {
        str(row.get("name") or "").strip(): str(row.get("region") or "").strip()
        for row in team_rank_rows or []
        if row.get("name")
    }
    rows = fetch_all(
        cur,
        """
        SELECT player_id, player_slug, player_name, country, current_team_abbrev,
               current_team_name, agents, avatar, source_player_url
        FROM valorant_player_basic
        ORDER BY player_name
        LIMIT %s
        """,
        (max(1, limit),),
    )
    out = []
    for row in rows:
        summary = summaries.get(str(row.get("player_id") or ""), {})
        agents = agent_text(summary.get("agents") or row.get("agents"))
        role = agent_role_text(summary.get("agents") or row.get("agents"))
        team_name = row.get("current_team_name") or row.get("current_team_abbrev") or "-"
        team_strength = team_strength_by_name.get(str(team_name or "").strip(), 0.0)
        team_bonus = clamp((team_strength - 1500) * 0.1, -55.0, 75.0) if team_strength else 0.0
        rank_score = round(player_rank_score(summary) + team_bonus, 2)
        rounds = safe_int(summary.get("rounds"))
        out.append(
            {
                "playerId": row.get("player_id") or "",
                "name": row.get("player_name") or "-",
                "team": team_name,
                "teamName": row.get("current_team_name") or "",
                "teamAbbrev": row.get("current_team_abbrev") or "",
                "region": team_region_by_name.get(str(team_name or "").strip()) or "-",
                "role": role or "-",
                "rating": summary.get("rating") or "-",
                "impact": summary.get("acs") or "-",
                "rankScore": rank_score,
                "primaryMetricValue": summary.get("rating") or "-",
                "secondaryMetricValue": summary.get("acs") or "-",
                "highlight": f"回合 {rounds or '-'} · KAST {summary.get('kast') or '-'}",
                "country": row.get("country") or "",
                "avatar": proxied_image_url(row.get("avatar")),
                "agents": agents,
                "sourceUrl": row.get("source_player_url") or "",
            }
        )
    out.sort(
        key=lambda item: (
            -safe_float(item.get("rankScore")),
            -safe_float(item.get("rating")),
            str(item.get("name") or ""),
        )
    )
    for idx, item in enumerate(out, 1):
        item["rank"] = idx
        item["globalRank"] = idx
    region_counts: Dict[str, int] = defaultdict(int)
    for item in out:
        region = str(item.get("region") or "-").strip() or "-"
        region_counts[region] += 1
        item["regionRank"] = region_counts[region]
    return out


def build_leaderboard(cur: pymysql.cursors.DictCursor, teams: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    team_by_id = {str(team.get("teamId") or "").strip(): team for team in teams if team.get("teamId")}
    key_by_name = {
        str(team.get("name") or "").strip(): str(team.get("teamId") or team.get("name") or "").strip()
        for team in teams
        if team.get("name")
    }
    team_region_by_key: Dict[str, str] = {}
    for team in teams:
        key = str(team.get("teamId") or team.get("name") or "").strip()
        region = str(team.get("region") or "-").strip() or "-"
        if key:
            team_region_by_key[key] = region
        if team.get("name"):
            team_region_by_key[str(team.get("name") or "").strip()] = region

    def new_stat() -> Dict[str, Any]:
        return {
            "rating": 1500.0,
            "recent_rating": 1500.0,
            "wins": 0,
            "losses": 0,
            "matches": 0,
            "recent_wins": 0,
            "recent_losses": 0,
            "recent_matches": 0,
            "maps_won": 0,
            "maps_lost": 0,
            "recent": [],
            "form_weight": 0.0,
            "form_result": 0.0,
            "form_map_diff": 0.0,
            "top_tier_bonus": 0.0,
            "top_tier_label": "",
            "quality_wins": 0,
            "game_changers_matches": 0,
            "non_game_changers_matches": 0,
            "last_match_date": None,
        }

    stats: Dict[str, Dict[str, Any]] = defaultdict(new_stat)
    region_ratings: Dict[str, float] = defaultdict(lambda: 1500.0)
    today = datetime.now().date()

    for team in teams:
        key = str(team.get("teamId") or team.get("name") or "").strip()
        if key:
            stats[key]
            region = str(team.get("region") or "-").strip() or "-"
            if region in VALORANT_MAJOR_REGIONS:
                region_ratings[region]

    def row_team_key(team_id: Any, team_name: Any) -> str:
        tid = str(team_id or "").strip()
        name = str(team_name or "").strip()
        if tid and tid in team_by_id:
            return tid
        return key_by_name.get(name, tid or name)

    if table_exists(cur, "valorant_match_result"):
        rows = fetch_all(
            cur,
            """
            SELECT team1_id, team1, team2_id, team2, winner, score1, score2,
                   event_name, stage, bo, match_time, match_date
            FROM valorant_match_result
            WHERE status IN ('completed', 'finished')
              AND (match_date IS NULL OR match_date >= %s)
            ORDER BY COALESCE(match_time, match_date), match_id
            """,
            (VALORANT_RANK_START_DATE.strftime("%Y-%m-%d"),),
        )
        for row in rows:
            team1 = str(row.get("team1") or "").strip()
            team2 = str(row.get("team2") or "").strip()
            winner = str(row.get("winner") or "").strip()
            key1 = row_team_key(row.get("team1_id"), team1)
            key2 = row_team_key(row.get("team2_id"), team2)
            if not key1 or not key2 or key1 == key2:
                continue
            score1_int = safe_int(row.get("score1"), -1)
            score2_int = safe_int(row.get("score2"), -1)
            winner_lower = winner.lower()
            if winner_lower == team1.lower() or (not winner and score1_int > score2_int):
                score1, score2 = 1.0, 0.0
                win_key, lose_key = key1, key2
            elif winner_lower == team2.lower() or (not winner and score2_int > score1_int):
                score1, score2 = 0.0, 1.0
                win_key, lose_key = key2, key1
            else:
                continue

            match_date = parse_api_date(row.get("match_time")) or parse_api_date(row.get("match_date"))
            days_ago = (today - match_date).days if match_date else 999
            event_profile = valorant_event_profile(row.get("event_name"), row.get("stage"))
            event_weight = float(event_profile["weight"]) * float(event_profile["stage_multiplier"])
            event_recency = recency_decay(days_ago, 365, 0.18)
            recent_recency = recency_decay(days_ago, 150, 0.0)
            map_total = score1_int + score2_int if score1_int >= 0 and score2_int >= 0 else safe_int(row.get("bo"), 3)
            map_diff = abs(score1_int - score2_int) if score1_int >= 0 and score2_int >= 0 else 1
            margin = 1 + min(map_diff, 3) * 0.08 + (0.04 if map_total >= 5 and map_diff >= 2 else 0)

            rating1 = float(stats[key1]["rating"])
            rating2 = float(stats[key2]["rating"])
            expected1 = 1 / (1 + 10 ** ((rating2 - rating1) / 400))
            expected2 = 1 - expected1
            k_factor = 26 * event_weight * event_recency * margin
            stats[key1]["rating"] = rating1 + k_factor * (score1 - expected1)
            stats[key2]["rating"] = rating2 + k_factor * (score2 - expected2)

            if days_ago <= 365:
                recent_rating1 = float(stats[key1]["recent_rating"])
                recent_rating2 = float(stats[key2]["recent_rating"])
                recent_expected1 = 1 / (1 + 10 ** ((recent_rating2 - recent_rating1) / 400))
                recent_expected2 = 1 - recent_expected1
                recent_k = 30 * event_weight * max(0.08, recent_recency) * margin
                stats[key1]["recent_rating"] = recent_rating1 + recent_k * (score1 - recent_expected1)
                stats[key2]["recent_rating"] = recent_rating2 + recent_k * (score2 - recent_expected2)

            for key in (key1, key2):
                stats[key]["matches"] += 1
                if match_date and (
                    stats[key].get("last_match_date") is None
                    or match_date > stats[key]["last_match_date"]
                ):
                    stats[key]["last_match_date"] = match_date
                tier_bonus = float(event_profile.get("tier_bonus") or 0) * recency_decay(days_ago, 365, 0.12)
                if tier_bonus > float(stats[key].get("top_tier_bonus") or 0):
                    stats[key]["top_tier_bonus"] = tier_bonus
                    stats[key]["top_tier_label"] = event_profile.get("tier_label") or ""
                if days_ago <= 365:
                    stats[key]["recent_matches"] += 1
                    form_weight = event_weight * max(0.05, recent_recency)
                    stats[key]["form_weight"] += form_weight
                if event_profile.get("tier_label") == "Game Changers":
                    stats[key]["game_changers_matches"] += 1
                else:
                    stats[key]["non_game_changers_matches"] += 1
            stats[win_key]["wins"] += 1
            stats[lose_key]["losses"] += 1
            if score1_int >= 0 and score2_int >= 0:
                stats[key1]["maps_won"] += score1_int
                stats[key1]["maps_lost"] += score2_int
                stats[key2]["maps_won"] += score2_int
                stats[key2]["maps_lost"] += score1_int
                denominator = max(1, score1_int + score2_int)
                signed_diff1 = (score1_int - score2_int) / denominator
                signed_diff2 = -signed_diff1
            else:
                signed_diff1 = 1.0 if win_key == key1 else -1.0
                signed_diff2 = -signed_diff1
            if days_ago <= 365:
                if win_key == key1:
                    stats[key1]["recent_wins"] += 1
                    stats[key2]["recent_losses"] += 1
                else:
                    stats[key2]["recent_wins"] += 1
                    stats[key1]["recent_losses"] += 1
                form_weight1 = event_weight * max(0.05, recent_recency)
                stats[key1]["form_result"] += form_weight1 * (1 if win_key == key1 else -1)
                stats[key2]["form_result"] += form_weight1 * (1 if win_key == key2 else -1)
                stats[key1]["form_map_diff"] += form_weight1 * signed_diff1
                stats[key2]["form_map_diff"] += form_weight1 * signed_diff2
                if event_weight >= 1.1:
                    stats[win_key]["quality_wins"] += 1
            stats[win_key]["recent"].append("W")
            stats[lose_key]["recent"].append("L")

            region1 = team_region_by_key.get(key1) or team_region_by_key.get(team1) or ""
            region2 = team_region_by_key.get(key2) or team_region_by_key.get(team2) or ""
            if (
                region1
                and region2
                and region1 != region2
                and region1 in VALORANT_MAJOR_REGIONS
                and region2 in VALORANT_MAJOR_REGIONS
                and event_weight >= 1.05
            ):
                region_rating1 = region_ratings[region1]
                region_rating2 = region_ratings[region2]
                region_expected1 = 1 / (1 + 10 ** ((region_rating2 - region_rating1) / 400))
                region_expected2 = 1 - region_expected1
                region_k = 10 * event_weight * event_recency * margin
                region_ratings[region1] = region_rating1 + region_k * (score1 - region_expected1)
                region_ratings[region2] = region_rating2 + region_k * (score2 - region_expected2)
    rows = []
    for team in teams:
        name = str(team.get("name") or "").strip()
        if not name:
            continue
        key = str(team.get("teamId") or name).strip()
        stat = stats.get(
            key,
            stats.get(
                name,
                {
                    "rating": 1500.0,
                    "wins": 0,
                    "losses": 0,
                    "matches": 0,
                    "recent": [],
                    "tier_weight": 0.0,
                },
            ),
        )
        total = safe_int(stat.get("matches")) or safe_int(stat.get("wins")) + safe_int(stat.get("losses"))
        recent_total = safe_int(stat.get("recent_matches"))
        win_rate = stat["wins"] / total if total else 0.0
        recent_win_rate = stat["recent_wins"] / recent_total if recent_total else 0.0
        rating = float(stat.get("rating") or 1500.0)
        recent_rating = float(stat.get("recent_rating") or 1500.0)
        blended_rating = rating * 0.58 + recent_rating * 0.42 if recent_total else rating * 0.7 + 1500 * 0.3
        form_weight = float(stat.get("form_weight") or 0)
        form_result = float(stat.get("form_result") or 0) / form_weight if form_weight else 0.0
        form_map_diff = float(stat.get("form_map_diff") or 0) / form_weight if form_weight else 0.0
        sample_factor = min(1.0, math.sqrt(recent_total / 8)) if recent_total else 0.0
        form_bonus = (form_result * 92 + form_map_diff * 52 + (recent_win_rate - 0.5) * 70) * sample_factor
        sample_bonus = min(38.0, math.sqrt(recent_total) * 10) if recent_total else 0.0
        quality_bonus = min(42.0, safe_int(stat.get("quality_wins")) * 6.0)
        tier_bonus = float(stat.get("top_tier_bonus") or 0)
        last_match_date = stat.get("last_match_date")
        inactive_days = (today - last_match_date).days if isinstance(last_match_date, date) else 999
        if inactive_days > 540:
            inactivity_penalty = 430.0
        elif inactive_days > 365:
            inactivity_penalty = 260.0 + min(170.0, (inactive_days - 365) * 0.65)
        elif inactive_days > 180:
            inactivity_penalty = 80.0 + min(160.0, (inactive_days - 180) * 0.6)
        else:
            inactivity_penalty = 0.0
        region = str(team.get("region") or "-").strip() or "-"
        gc_matches = safe_int(stat.get("game_changers_matches"))
        non_gc_matches = safe_int(stat.get("non_game_changers_matches"))
        if gc_matches >= 3 and gc_matches >= non_gc_matches:
            region = "Game Changers"
        region_bonus = clamp((region_ratings[region] - 1500) * 0.24, -42.0, 42.0) if region in VALORANT_MAJOR_REGIONS else 0.0
        rank_score = (
            round(max(0.0, blended_rating + tier_bonus + form_bonus + sample_bonus + quality_bonus - inactivity_penalty), 1)
            if total
            else 0.0
        )
        global_score = round(max(0.0, rank_score + region_bonus), 1) if total else 0.0
        recent = "".join(stat.get("recent", [])[-5:])
        if not total:
            status = "暂无比赛"
        elif inactive_days > 180:
            status = "休眠"
        elif recent_total >= 8:
            status = "稳定样本"
        elif recent_total >= 3:
            status = "观察样本"
        else:
            status = "样本少"
        rows.append(
            {
                "rank": 0,
                "name": name,
                "region": region,
                "points": rank_score,
                "rankScore": rank_score,
                "globalScore": global_score,
                "rating": round(blended_rating, 1) if total else "-",
                "winRate": format_percent(win_rate),
                "winRateRaw": win_rate,
                "trend": f"近{len(recent)} {recent}" if recent else "-",
                "wins": stat["wins"],
                "losses": stat["losses"],
                "recentWins": stat["recent_wins"],
                "recentLosses": stat["recent_losses"],
                "matchesPlayed": recent_total or total,
                "totalMatches": total,
                "mapDiff": stat["maps_won"] - stat["maps_lost"],
                "tier": stat.get("top_tier_label") or "-",
                "model": "区域GPR",
                "status": status,
                "lastMatchDate": last_match_date.strftime("%Y-%m-%d") if isinstance(last_match_date, date) else "",
                "teamLogo": team.get("logo") or "",
                "teamId": team.get("teamId") or "",
            }
        )
    rows.sort(
        key=lambda item: (
            safe_int(item.get("totalMatches")) <= 0,
            -safe_float(item.get("globalScore")),
            -safe_float(str(item.get("winRate") or "").replace("%", "")),
            item.get("name") or "",
        )
    )
    for idx, row in enumerate(rows, 1):
        row["rank"] = idx
        row["globalRank"] = idx
    region_counts: Dict[str, int] = defaultdict(int)
    for row in rows:
        region = str(row.get("region") or "-").strip() or "-"
        region_counts[region] += 1
        row["regionRank"] = region_counts[region]
    return rows


def build_match_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    score1 = row.get("score1")
    score2 = row.get("score2")
    match_time = row.get("match_time") or row.get("match_time_utc") or ""
    return {
        "matchId": row.get("match_id") or "",
        "date": str(row.get("match_date") or match_time or "")[:10],
        "matchTime": match_time or "",
        "tournament": row.get("event_name") or "-",
        "stage": row.get("stage") or "-",
        "teamA": row.get("team1") or "-",
        "teamB": row.get("team2") or "-",
        "teamAId": row.get("team1_id") or "",
        "teamBId": row.get("team2_id") or "",
        "teamALogo": proxied_image_url(row.get("team1_logo")),
        "teamBLogo": proxied_image_url(row.get("team2_logo")),
        "score": resolve_score(score1, score2),
        "winner": row.get("winner") or "-",
        "statusCode": status_code(row.get("status")),
        "statusText": status_text(row.get("status")),
        "note": row.get("note") or "",
        "sourceUrl": row.get("match_url") or row.get("source_url") or "",
        "tier": row.get("tier") or "",
    }


def build_matches(cur: pymysql.cursors.DictCursor, limit: int = 500) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if table_exists(cur, "valorant_match_result"):
        rows.extend(
            fetch_all(
                cur,
                """
                SELECT *
                FROM valorant_match_result
                ORDER BY COALESCE(match_time, match_date) DESC
                LIMIT %s
                """,
                (max(1, limit),),
            )
        )
    if table_exists(cur, "valorant_match_schedule"):
        join_sql, where_parts = fixture_visibility_sql(cur, "s")
        where_sql = f"WHERE {' AND '.join(where_parts)}"
        rows.extend(
            fetch_all(
                cur,
                f"""
                SELECT s.*
                FROM valorant_match_schedule s
                {join_sql}
                {where_sql}
                ORDER BY COALESCE(s.match_time, s.match_date) ASC
                LIMIT %s
                """,
                (max(1, limit),),
            )
        )
    seen = set()
    out = []
    for row in rows:
        mid = str(row.get("match_id") or "")
        if mid and mid in seen:
            continue
        if mid:
            seen.add(mid)
        out.append(build_match_payload(row))
    return out[:limit]


def build_matches_filtered(
    cur: pymysql.cursors.DictCursor,
    *,
    view: str = "fixture",
    date_filter: str = "",
    limit: int = 20,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    safe_view = normalize_view(view)
    table_name = "valorant_match_schedule" if safe_view == "fixture" else "valorant_match_result"
    if safe_view == "all":
        return build_matches(cur, limit=max(1, limit + offset))[offset : offset + limit]
    if not table_exists(cur, table_name):
        return []
    where: List[str] = []
    join_sql = ""
    table_sql = table_name
    params: List[Any] = []
    if safe_view == "fixture":
        table_sql = f"{table_name} s"
        join_sql, where = fixture_visibility_sql(cur, "s")
    if date_filter:
        where.append("s.match_date = %s" if safe_view == "fixture" else "match_date = %s")
        params.append(date_filter)
    if safe_view == "fixture":
        order_sql = "ORDER BY COALESCE(s.match_time, s.match_date) ASC, s.match_id ASC"
        select_sql = "SELECT s.*"
    else:
        order_sql = "ORDER BY COALESCE(match_time, match_date) DESC, match_id DESC"
        select_sql = "SELECT *"
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    params.extend([max(1, min(limit, 10000)), max(0, offset)])
    rows = fetch_all(
        cur,
        f"""
        {select_sql}
        FROM {table_sql}
        {join_sql}
        {where_sql}
        {order_sql}
        LIMIT %s OFFSET %s
        """,
        tuple(params),
    )
    return [build_match_payload(row) for row in rows]


def build_dataset() -> Dict[str, Any]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            tournaments = build_tournaments(cur)
            teams = build_teams(cur)
            leaderboard = build_leaderboard(cur, teams)
            players = build_players(cur, team_rank_rows=leaderboard)
            matches = build_matches(cur, limit=500)
    updated_at = safe_datetime_now()
    return {
        "gameId": "valorant",
        "gameName": "无畏契约",
        "gameSubtitle": "VALORANT",
        "color": "#e63946",
        "updatedAt": updated_at,
        "leaderboard": leaderboard,
        "tournaments": tournaments,
        "matches": matches,
        "teams": teams,
        "players": players,
        "analysis": {
            "summary": "VALORANT dataset generated from MySQL valorant_* tables.",
            "turningPoints": [
                f"{matches[0]['teamA']} vs {matches[0]['teamB']}: {matches[0]['score']}"
            ] if matches else ["No Valorant matches imported yet."],
            "teamInsight": "Team records are built from VLR experiment import and match results.",
            "playerInsight": "Player stats include Rating, ACS, K/D/A, KAST, ADR, HS%, FK and FD.",
        },
        "mappingNotes": [
            {"title": "Data source", "desc": "Current Valorant data is loaded from local experiment CSV into MySQL."},
            {"title": "Ranking model", "desc": "Team rankings use region-first GPR scoring with event tier, recency, map differential and activity penalties."},
            {"title": "Schedule/result split", "desc": "Future matches use valorant_match_schedule; completed matches use valorant_match_result."},
            {"title": "Detail stats", "desc": "Map and player rows come from valorant_match_map_stats and valorant_match_player_stats."},
        ],
        "metrics": [
            {"label": "赛事总数", "value": str(len(tournaments)), "detail": "From valorant_event_basic"},
            {"label": "比赛总数", "value": str(len(matches)), "detail": "Merged schedule and results"},
            {"label": "战队总数", "value": str(len(teams)), "detail": "From valorant_team_basic"},
            {"label": "选手总数", "value": str(len(players)), "detail": "From valorant_player_basic"},
        ],
        "filters": {
            "regions": sorted({item["region"] for item in [*tournaments, *teams] if item.get("region")}),
            "tiers": sorted({item["tier"] for item in tournaments if item.get("tier")}),
        },
        "analysisOutput": [
            {"key": "当前项目", "value": "无畏契约"},
            {"key": "最新同步", "value": updated_at},
            {"key": "赛事总量", "value": str(len(tournaments))},
            {"key": "比赛总量", "value": str(len(matches))},
            {"key": "战队总量", "value": str(len(teams))},
            {"key": "选手总量", "value": str(len(players))},
        ],
    }


def player_stat_items(summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    metrics = [
        {
            "metric": "rating",
            "source_key": "rating",
            "avg": "1.00",
            "bad": ("0.75", "0.90"),
            "middle": ("0.90", "1.05"),
            "good": ("1.05", "1.30"),
        },
        {
            "metric": "adr",
            "source_key": "adr",
            "avg": "140",
            "bad": ("100", "125"),
            "middle": ("125", "150"),
            "good": ("150", "180"),
        },
        {
            "metric": "kast",
            "source_key": "kast",
            "avg": "70%",
            "bad": ("55%", "65%"),
            "middle": ("65%", "75%"),
            "good": ("75%", "85%"),
        },
        {
            "metric": "impact",
            "source_key": "acs",
            "avg": "200",
            "bad": ("150", "185"),
            "middle": ("185", "225"),
            "good": ("225", "280"),
        },
        {
            "metric": "kpr",
            "source_key": "kpr",
            "avg": "0.70",
            "bad": ("0.50", "0.65"),
            "middle": ("0.65", "0.80"),
            "good": ("0.80", "1.00"),
        },
        {
            "metric": "fdpr",
            "source_key": "fdpr",
            "avg": "0.10",
            "bad": ("0.18", "0.25"),
            "middle": ("0.10", "0.18"),
            "good": ("0.00", "0.10"),
            "lower_better": "1",
        },
        {
            "metric": "swing",
            "source_key": "kd",
            "avg": "1.00",
            "bad": ("0.70", "0.90"),
            "middle": ("0.90", "1.15"),
            "good": ("1.15", "1.50"),
        },
        {
            "metric": "hs%",
            "source_key": "hs_pct",
            "avg": "22%",
            "bad": ("10%", "18%"),
            "middle": ("18%", "26%"),
            "good": ("26%", "35%"),
        },
    ]
    return [
        {
            "metric": item["metric"],
            "value": summary.get(item["source_key"]) or "-",
            "avg_value": item["avg"],
            "good_start": item["good"][0],
            "good_end": item["good"][1],
            "middle_start": item["middle"][0],
            "middle_end": item["middle"][1],
            "bad_start": item["bad"][0],
            "bad_end": item["bad"][1],
            "lower_better": item.get("lower_better", "0"),
        }
        for item in metrics
    ]


def build_player_detail(cur: pymysql.cursors.DictCursor, player_id: str) -> Dict[str, Any]:
    if not table_exists(cur, "valorant_player_basic"):
        return {"exists": False}
    rows = fetch_all(
        cur,
        """
        SELECT *
        FROM valorant_player_basic
        WHERE player_id = %s OR player_slug = %s OR player_name = %s
        LIMIT 1
        """,
        (player_id, player_id, player_id),
    )
    if not rows:
        return {"exists": False}
    basic = rows[0]
    pid = str(basic.get("player_id") or "")
    summary = {}
    if table_exists(cur, "valorant_player_stats_summary"):
        stats = fetch_all(cur, "SELECT * FROM valorant_player_stats_summary WHERE player_id = %s LIMIT 1", (pid,))
        summary = stats[0] if stats else {}
    team_relation = {}
    team_abbrev = basic.get("current_team_abbrev") or summary.get("team_abbrev") or ""
    if table_exists(cur, "valorant_team_player_relation"):
        active_filter = active_relation_sql(cur, "tpr")
        relation_rows = fetch_all(
            cur,
            f"""
            SELECT team_id, team_name, team_abbrev
            FROM valorant_team_player_relation tpr
            WHERE tpr.player_id = %s
              {active_filter}
            ORDER BY (team_abbrev = %s) DESC, (team_name <> '') DESC
            LIMIT 1
            """,
            (pid, team_abbrev),
        )
        team_relation = relation_rows[0] if relation_rows else {}
    team_logo = ""
    if team_relation.get("team_id") and table_exists(cur, "valorant_team_basic"):
        team_rows = fetch_all(
            cur,
            "SELECT team_logo FROM valorant_team_basic WHERE team_id = %s LIMIT 1",
            (team_relation.get("team_id"),),
        )
        team_logo = team_rows[0].get("team_logo") if team_rows else ""
    teammates = []
    if table_exists(cur, "valorant_team_player_relation"):
        active_filter = active_relation_sql(cur, "tpr")
        teammates = fetch_all(
            cur,
            f"""
            SELECT tpr.player_id AS teammate_id, tpr.player_name AS teammate_name,
                   COALESCE(NULLIF(tpr.team_name, ''), tpr.team_abbrev) AS team_name,
                   vpb.avatar AS avatar
            FROM valorant_team_player_relation tpr
            LEFT JOIN valorant_player_basic vpb ON vpb.player_id = tpr.player_id
            WHERE tpr.team_abbrev = %s AND tpr.player_id <> %s
              {active_filter}
            ORDER BY tpr.player_name
            LIMIT 20
            """,
            (team_abbrev, pid),
        )
        for teammate in teammates:
            teammate["avatar"] = proxied_image_url(teammate.get("avatar"))
    maps = []
    recent = []
    if table_exists(cur, "valorant_match_player_stats"):
        maps = fetch_all(
            cur,
            """
            SELECT map_name, COUNT(*) AS use_num,
                   ROUND(AVG(CAST(NULLIF(rating, '') AS DECIMAL(8,3))), 2) AS map_rating,
                   CONCAT(SUM(kills), '/', SUM(deaths)) AS map_kd
            FROM valorant_match_player_stats
            WHERE player_id = %s
            GROUP BY map_name
            ORDER BY use_num DESC, map_name
            LIMIT 20
            """,
            (pid,),
        )
        recent = fetch_all(
            cur,
            """
            SELECT mps.match_id, mps.map_name, mps.kills AS home_score,
                   mps.deaths AS opponent_score, mps.fetched_at AS ts_text,
                   COALESCE(vmd.event_name, vmr.event_name, vms.event_name) AS tournament_name,
                   COALESCE(
                       CASE
                           WHEN tpr.team_name = COALESCE(vmd.team1, vmr.team1, vms.team1)
                               THEN COALESCE(vmd.team2, vmr.team2, vms.team2)
                           WHEN tpr.team_name = COALESCE(vmd.team2, vmr.team2, vms.team2)
                               THEN COALESCE(vmd.team1, vmr.team1, vms.team1)
                           ELSE NULL
                       END,
                       COALESCE(vmd.team1, vmr.team1, vms.team1)
                   ) AS opponent_team_name
            FROM valorant_match_player_stats mps
            LEFT JOIN valorant_team_player_relation tpr
                ON tpr.player_id = mps.player_id AND tpr.team_abbrev = mps.team_abbrev
            LEFT JOIN valorant_match_detail vmd ON vmd.match_id = mps.match_id
            LEFT JOIN valorant_match_result vmr ON vmr.match_id = mps.match_id
            LEFT JOIN valorant_match_schedule vms ON vms.match_id = mps.match_id
            WHERE mps.player_id = %s
            ORDER BY COALESCE(vmd.match_time_utc, vmr.match_time, vms.match_time) DESC
            LIMIT 20
            """,
            (pid,),
        )
    agents_value = basic.get("agents") or summary.get("agents")
    agents = agent_text(agents_value)
    role = agent_role_text(agents_value)
    return {
        "exists": True,
        "playerId": pid,
        "basic": {
            "playerId": pid,
            "name": basic.get("player_name") or "-",
            "teamId": team_relation.get("team_id") or "",
            "teamName": team_relation.get("team_name") or basic.get("current_team_name") or team_abbrev or "-",
            "teamAbbrev": team_relation.get("team_abbrev") or team_abbrev or "",
            "teamLogo": proxied_image_url(team_logo),
            "avatar": proxied_image_url(basic.get("avatar")),
            "country": basic.get("country") or "",
            "positions": role or "-",
            "position": role.split(" / ")[0] if role else "-",
            "agents": agents,
            "primaryRole": role or "-",
            "rating": summary.get("rating") or "-",
            "impact": summary.get("acs") or "-",
            "kd": summary.get("kd") or "-",
            "adr": summary.get("adr") or "-",
        },
        "summary": {
            "map_total": summary.get("rounds") or "-",
            "map_win_rate": summary.get("kast") or "-",
            "map_win": summary.get("kills") or "-",
            "map_loss": summary.get("deaths") or "-",
            "match_total": summary.get("rounds") or "-",
            "match_win_rate": summary.get("kast") or "-",
            "match_mvp_count": summary.get("kmax") or "-",
        },
        "performanceMetrics": player_stat_items(summary),
        "teammates": teammates,
        "maps": maps,
        "equipment": [],
        "mouseConfig": {},
        "monitorConfig": {},
        "recentMatches": recent,
        "honors": [],
        "milestones": [],
        "ratingChart": [],
    }


def build_team_detail(cur: pymysql.cursors.DictCursor, team_key: str) -> Dict[str, Any]:
    if not table_exists(cur, "valorant_team_basic"):
        return {"exists": False}
    rows = fetch_all(
        cur,
        """
        SELECT *
        FROM valorant_team_basic
        WHERE team_id = %s OR team_slug = %s OR team_name = %s
        LIMIT 1
        """,
        (team_key, team_key, team_key),
    )
    if not rows:
        return {"exists": False}
    team = rows[0]
    name = str(team.get("team_name") or "")
    ranked_row: Dict[str, Any] = {}
    try:
        leaderboard = build_leaderboard(cur, build_teams(cur))
        ranked_row = next(
            (
                item
                for item in leaderboard
                if str(item.get("teamId") or "") == str(team.get("team_id") or "")
                or str(item.get("name") or "") == name
            ),
            {},
        )
    except Exception:
        ranked_row = {}
    members = []
    if table_exists(cur, "valorant_team_player_relation"):
        active_filter = active_relation_sql(cur, "tpr")
        members = fetch_all(
            cur,
            f"""
            SELECT tpr.player_id AS playerId, tpr.player_name AS name, tpr.team_abbrev AS role,
                   COALESCE(NULLIF(vpb.agents, ''), tpr.team_abbrev) AS position,
                   vpb.avatar AS avatar, vpb.country AS country
            FROM valorant_team_player_relation tpr
            LEFT JOIN valorant_player_basic vpb ON vpb.player_id = tpr.player_id
            WHERE (tpr.team_name = %s OR tpr.team_id = %s OR tpr.team_abbrev = %s)
              {active_filter}
            ORDER BY tpr.player_name
            LIMIT 20
            """,
            (name, team.get("team_id") or "", name),
        )
        for member in members:
            member["avatar"] = proxied_image_url(member.get("avatar"))
    recent = []
    if table_exists(cur, "valorant_match_result"):
        for row in fetch_all(
            cur,
            """
            SELECT *
            FROM valorant_match_result
            WHERE team1 = %s OR team2 = %s
            ORDER BY match_time DESC
            LIMIT 20
            """,
            (name, name),
        ):
            item = build_match_payload(row)
            if item.get("statusCode") != 2 or item.get("score") in (None, "", "-") or item.get("winner") in (None, "", "-"):
                continue
            is_team_a = str(row.get("team1") or "") == name
            opponent = row.get("team2") if is_team_a else row.get("team1")
            item["teamName"] = name
            item["opponent"] = opponent or "-"
            item["result"] = "胜" if item.get("winner") == name else "负"
            recent.append(item)
    wins = sum(1 for row in recent if row.get("winner") == name)
    losses = max(0, len(recent) - wins)
    total = wins + losses
    ranked_wins = safe_int(ranked_row.get("wins"), wins)
    ranked_losses = safe_int(ranked_row.get("losses"), losses)
    ranked_total = safe_int(ranked_row.get("matchesPlayed"), total)
    return {
        "exists": True,
        "teamId": team.get("team_id") or "",
        "basic": {
            "teamId": team.get("team_id") or "",
            "name": name,
            "region": team.get("region") or team.get("country") or "-",
            "logo": proxied_image_url(team.get("team_logo")),
            "teamLogo": proxied_image_url(team.get("team_logo")),
            "country": team.get("country") or "",
        },
        "rank": {
            "globalRank": ranked_row.get("globalRank") or ranked_row.get("rank") or "-",
            "regionRank": ranked_row.get("regionRank") or "-",
            "score": ranked_row.get("rankScore") or ranked_row.get("points") or "-",
        },
        "stats": {
            "matchesPlayed": ranked_total,
            "wins": ranked_wins,
            "losses": ranked_losses,
            "winRate": ranked_row.get("winRate") or (format_percent(wins / total) if total else "-"),
            "rating": ranked_row.get("rating") or "-",
            "rankScore": ranked_row.get("rankScore") or "-",
            "globalScore": ranked_row.get("globalScore") or "-",
            "model": ranked_row.get("model") or "-",
            "tier": ranked_row.get("tier") or "-",
            "status": ranked_row.get("status") or "-",
            "lastMatchDate": ranked_row.get("lastMatchDate") or "-",
        },
        "members": members,
        "recentMatches": recent,
    }


def player_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "playerId": row.get("player_id") or "",
        "name": row.get("player_name") or "-",
        "avatar": proxied_image_url(row.get("avatar")),
        "country": row.get("country") or "",
        "champion": row.get("agents") or "",
        "rating": row.get("rating") or "-",
        "acs": row.get("acs") or "-",
        "adr": row.get("adr") or "-",
        "kast": row.get("kast") or "-",
        "kill": row.get("kills"),
        "death": row.get("deaths"),
        "assist": row.get("assists"),
        "kd": row.get("kd_diff") or "",
    }


def split_player_rows_by_team(items: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    abbrevs: List[str] = []
    for item in items:
        abbrev = str(item.get("team_abbrev") or "").strip()
        if abbrev and abbrev not in abbrevs:
            abbrevs.append(abbrev)
        if len(abbrevs) >= 2:
            break
    if len(abbrevs) >= 2:
        return (
            [item for item in items if str(item.get("team_abbrev") or "").strip() == abbrevs[0]],
            [item for item in items if str(item.get("team_abbrev") or "").strip() == abbrevs[1]],
        )
    return items[:5], items[5:10]


def is_placeholder_map_row(row: Dict[str, Any]) -> bool:
    map_name = str(row.get("map_name") or "").strip().lower()
    duration = str(row.get("duration") or "").strip()
    return (
        map_name in {"", "-", "tbd"}
        and safe_int(row.get("team1_score")) == 0
        and safe_int(row.get("team2_score")) == 0
        and not str(row.get("winner") or "").strip()
        and duration in {"", "-"}
    )


def is_empty_placeholder_player_stat(row: Dict[str, Any]) -> bool:
    map_name = str(row.get("map_name") or "").strip().lower()
    if map_name not in {"", "-", "tbd"}:
        return False
    for key in ("agents", "rating", "acs", "kills", "deaths", "assists", "kd_diff", "kast", "adr", "hs_pct", "first_kills", "first_deaths"):
        if str(row.get(key) or "").strip():
            return False
    return True


def build_match_detail(cur: pymysql.cursors.DictCursor, match_id: str) -> Dict[str, Any]:
    detail_rows: List[Dict[str, Any]] = []
    if table_exists(cur, "valorant_match_detail"):
        detail_rows = fetch_all(cur, "SELECT * FROM valorant_match_detail WHERE match_id = %s LIMIT 1", (match_id,))
    if not detail_rows and table_exists(cur, "valorant_match_result"):
        detail_rows = fetch_all(cur, "SELECT * FROM valorant_match_result WHERE match_id = %s LIMIT 1", (match_id,))
    if not detail_rows and table_exists(cur, "valorant_match_schedule"):
        detail_rows = fetch_all(cur, "SELECT * FROM valorant_match_schedule WHERE match_id = %s LIMIT 1", (match_id,))
    if not detail_rows:
        return {"exists": False}
    row = detail_rows[0]
    maps = []
    map_player_stats = []
    player_stats = {"teamA": [], "teamB": []}
    valid_game_ids: set[str] = set()
    saw_map_rows = False
    if table_exists(cur, "valorant_match_map_stats"):
        map_rows = fetch_all(
            cur,
            "SELECT * FROM valorant_match_map_stats WHERE match_id = %s ORDER BY map_index",
            (match_id,),
        )
        saw_map_rows = bool(map_rows)
        for map_row in map_rows:
            if is_placeholder_map_row(map_row):
                continue
            game_id = str(map_row.get("game_id") or "").strip()
            if game_id:
                valid_game_ids.add(game_id)
            maps.append(
                {
                    "index": map_row.get("map_index"),
                    "map": map_row.get("map_name") or "-",
                    "team1Score": map_row.get("team1_score"),
                    "team2Score": map_row.get("team2_score"),
                    "winner": "team1" if map_row.get("winner") == map_row.get("team1") else "team2" if map_row.get("winner") == map_row.get("team2") else "",
                    "duration": map_row.get("duration") or "",
                }
            )
    if table_exists(cur, "valorant_match_player_stats"):
        rows = fetch_all(
            cur,
            """
            SELECT mps.*, vpb.avatar AS avatar
            FROM valorant_match_player_stats mps
            LEFT JOIN valorant_player_basic vpb ON vpb.player_id = mps.player_id
            WHERE mps.match_id = %s
            ORDER BY mps.map_index, mps.id
            """,
            (match_id,),
        )
        grouped: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
        for stat in rows:
            game_id = str(stat.get("game_id") or "").strip()
            if saw_map_rows and game_id not in valid_game_ids:
                continue
            if is_empty_placeholder_player_stat(stat):
                continue
            grouped[safe_int(stat.get("map_index"))].append(stat)
        for map_index, items in sorted(grouped.items()):
            team_a_rows, team_b_rows = split_player_rows_by_team(items)
            team_a = [player_payload(item) for item in team_a_rows]
            team_b = [player_payload(item) for item in team_b_rows]
            map_player_stats.append(
                {
                    "mapIndex": map_index,
                    "mapName": items[0].get("map_name") if items else "",
                    "teamA": team_a,
                    "teamB": team_b,
                }
            )
        first_map_rows = next((items for _map_index, items in sorted(grouped.items()) if items), [])
        if first_map_rows:
            team_a_rows, team_b_rows = split_player_rows_by_team(first_map_rows)
            player_stats = {
                "teamA": [player_payload(item) for item in team_a_rows],
                "teamB": [player_payload(item) for item in team_b_rows],
            }
    match_time = row.get("match_time_utc") or row.get("match_time") or ""
    score1 = safe_int(row.get("score1"))
    score2 = safe_int(row.get("score2"))
    winner = str(row.get("winner") or "").strip()
    if not winner and score1 != score2:
        winner = row.get("team1") if score1 > score2 else row.get("team2")
    return {
        "exists": True,
        "matchId": row.get("match_id") or "",
        "date": str(match_time or "")[:10],
        "matchTime": match_time,
        "bo": row.get("bo"),
        "tournament": {
            "eventId": row.get("event_id") or "",
            "name": row.get("event_name") or "-",
            "tier": "S" if "VCT" in str(row.get("event_name") or "").upper() else "A",
        },
        "teamA": {
            "teamId": row.get("team1_id") or "",
            "name": row.get("team1") or "-",
            "logo": proxied_image_url(row.get("team1_logo")),
            "score": row.get("score1"),
        },
        "teamB": {
            "teamId": row.get("team2_id") or "",
            "name": row.get("team2") or "-",
            "logo": proxied_image_url(row.get("team2_logo")),
            "score": row.get("score2"),
        },
        "score": row.get("score") or resolve_score(row.get("score1"), row.get("score2")),
        "winner": winner or "-",
        "statusText": status_text(row.get("status")),
        "maps": maps,
        "playerStats": player_stats,
        "mapPlayerStats": map_player_stats,
        "note": row.get("source_url") or row.get("match_url") or "",
    }


@router.get("/api/valorant/image")
def valorant_image(url: str = Query("")) -> Response:
    text = str(url or "").strip()
    if text.startswith("//"):
        text = f"https:{text}"
    parsed = urlparse(text)
    if not is_allowed_proxy_image(parsed):
        return Response(status_code=400)
    cached = read_cached_image(text)
    if cached:
        content, content_type = cached
        return Response(
            content=content,
            media_type=content_type,
            headers={"Cache-Control": IMAGE_CACHE_CONTROL, "X-Image-Cache": "HIT"},
        )
    try:
        upstream = IMAGE_PROXY_SESSION.get(
            text,
            timeout=12,
        )
        upstream.raise_for_status()
    except requests.RequestException:
        return Response(status_code=502)
    content_type = upstream.headers.get("content-type", "image/png").split(";", 1)[0]
    if not content_type.startswith("image/"):
        return Response(status_code=502)
    write_cached_image(text, upstream.content, content_type)
    return Response(
        content=upstream.content,
        media_type=content_type,
        headers={"Cache-Control": IMAGE_CACHE_CONTROL, "X-Image-Cache": "MISS"},
    )


@router.get("/api/valorant/dataset")
def valorant_dataset() -> Dict[str, Any]:
    return {"success": True, "data": build_dataset()}


@router.get("/api/valorant/matches")
def valorant_matches(
    view: str = Query("fixture"),
    date: str = Query(""),
    tier: str = Query("all"),
    limit: int = Query(20, ge=1, le=10000),
    offset: int = Query(0, ge=0, le=1000000),
) -> Dict[str, Any]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            rows = build_matches_filtered(
                cur,
                view=view,
                date_filter=date,
                limit=limit,
                offset=offset,
            )
    return {
        "success": True,
        "data": {
            "updatedAt": safe_datetime_now(),
            "matches": rows,
            "filters": {
                "view": normalize_view(view),
                "date": date,
                "tier": tier,
                "limit": limit,
                "offset": offset,
            },
        },
    }


@router.get("/api/valorant/player/{player_id}")
def valorant_player_detail(player_id: str) -> Dict[str, Any]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            detail = build_player_detail(cur, player_id)
    return {"success": True, "data": detail}


@router.get("/api/valorant/team/{team_key}")
def valorant_team_detail(team_key: str) -> Dict[str, Any]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            detail = build_team_detail(cur, team_key)
    return {"success": True, "data": detail}


@router.get("/api/valorant/match/{match_id}")
def valorant_match_detail(match_id: str) -> Dict[str, Any]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            detail = build_match_detail(cur, match_id)
    return {"success": True, "data": detail}
