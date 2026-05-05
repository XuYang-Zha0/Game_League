from __future__ import annotations

import argparse
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Sequence, Set, Tuple

import pymysql
import requests


BASE_DIR = Path(__file__).resolve().parent
KONNECT_SEARCH_HOST = "https://search.konect.gg"
KONNECT_SEARCH_KEY = "9f61e50d759b49753cc8cb48b4f1ce2ac11df4e5776324d7d1be09a813ea4274"
KONNECT_INDEX = "User_production"
DEFAULT_USER_AGENT = "GameLeagueValorantKonectAvatarBot/0.1"
UNKNOWN_COUNTRIES = {"", "UN", "XX", "-"}
PLACEHOLDER_MARKERS = ("default", "placeholder", "silhouette", "noimage", "no-image")


def load_local_env() -> None:
    env_path = BASE_DIR.parent / ".env"
    if not env_path.exists():
        return
    with env_path.open("r", encoding="utf-8") as env_file:
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


@dataclass(frozen=True)
class TargetPlayer:
    player_id: str
    player_name: str
    country: str = ""
    team_name: str = ""
    team_abbrev: str = ""


@dataclass(frozen=True)
class KonectMatch:
    player_id: str
    player_name: str
    username: str
    nickname: str
    country: str
    avatar: str
    source_url: str
    score: float
    reason: str


def quote_ident(name: str) -> str:
    return f"`{str(name).replace('`', '``')}`"


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()


def norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", clean(value).lower())


def now_sql() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_conn() -> pymysql.Connection:
    return pymysql.connect(**DB_CONFIG)


def table_columns(cur: pymysql.cursors.DictCursor, table_name: str) -> Set[str]:
    cur.execute(f"SHOW COLUMNS FROM {quote_ident(table_name)}")
    return {str(row["Field"]) for row in cur.fetchall()}


def ensure_avatar_columns(cur: pymysql.cursors.DictCursor) -> None:
    columns = table_columns(cur, "valorant_player_basic")
    additions = [
        ("avatar_source", "VARCHAR(50) NULL AFTER `avatar`"),
        ("avatar_source_url", "VARCHAR(500) NULL AFTER `avatar_source`"),
        ("avatar_checked_at", "DATETIME NULL AFTER `avatar_source_url`"),
    ]
    for column, definition in additions:
        if column not in columns:
            cur.execute(
                f"ALTER TABLE {quote_ident('valorant_player_basic')} "
                f"ADD COLUMN {quote_ident(column)} {definition}"
            )


def fetch_db_targets(
    cur: pymysql.cursors.DictCursor,
    *,
    include_existing: bool,
    limit: int,
    player_ids: Sequence[str],
) -> List[TargetPlayer]:
    where: List[str] = []
    params: List[Any] = []
    if not include_existing:
        where.append("(avatar IS NULL OR avatar = '')")
    if player_ids:
        where.append(f"player_id IN ({', '.join(['%s'] * len(player_ids))})")
        params.extend(player_ids)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    limit_sql = "LIMIT %s" if limit > 0 else ""
    if limit > 0:
        params.append(limit)
    cur.execute(
        f"""
        SELECT player_id, player_name, country, current_team_name, current_team_abbrev
        FROM valorant_player_basic
        {where_sql}
        ORDER BY player_name
        {limit_sql}
        """,
        tuple(params),
    )
    return [
        TargetPlayer(
            player_id=clean(row.get("player_id")),
            player_name=clean(row.get("player_name")),
            country=clean(row.get("country")).upper(),
            team_name=clean(row.get("current_team_name")),
            team_abbrev=clean(row.get("current_team_abbrev")),
        )
        for row in cur.fetchall()
        if clean(row.get("player_id")) and clean(row.get("player_name"))
    ]


def fetch_ranked_missing_targets(top_missing: int) -> List[TargetPlayer]:
    import valorant_api_server as api  # pylint: disable=import-outside-toplevel

    with api.get_conn() as conn:
        with conn.cursor() as cur:
            leaderboard = api.build_leaderboard(cur, api.build_teams(cur))
            players = api.build_players(cur, team_rank_rows=leaderboard)
    targets: List[TargetPlayer] = []
    for player in players:
        if player.get("avatar"):
            continue
        targets.append(
            TargetPlayer(
                player_id=clean(player.get("playerId")),
                player_name=clean(player.get("name")),
                country=clean(player.get("country")).upper(),
                team_name=clean(player.get("teamName") or player.get("team")),
                team_abbrev=clean(player.get("teamAbbrev")),
            )
        )
        if top_missing > 0 and len(targets) >= top_missing:
            break
    return targets


class KonectClient:
    def __init__(self, user_agent: str, min_delay: float) -> None:
        self.min_delay = max(0.0, min_delay)
        self.last_request_at = 0.0
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Authorization": f"Bearer {KONNECT_SEARCH_KEY}",
                "Content-Type": "application/json",
            }
        )

    def search(self, query: str, *, limit: int) -> List[Dict[str, Any]]:
        elapsed = time.monotonic() - self.last_request_at
        if elapsed < self.min_delay:
            time.sleep(self.min_delay - elapsed)
        response = self.session.post(
            f"{KONNECT_SEARCH_HOST}/indexes/{KONNECT_INDEX}/search",
            json={
                "q": query,
                "limit": max(1, min(limit, 20)),
                "attributesToRetrieve": [
                    "id",
                    "username",
                    "nickname",
                    "bio",
                    "type",
                    "country",
                    "verified",
                    "mainGame",
                    "view_count_30d",
                    "avatar_url",
                    "avatar_98x98_url",
                    "avatar_254x254_url",
                ],
            },
            timeout=20,
        )
        self.last_request_at = time.monotonic()
        response.raise_for_status()
        data = response.json()
        return [hit for hit in data.get("hits", []) if isinstance(hit, dict)]


def usable_avatar(hit: Dict[str, Any]) -> str:
    avatar = clean(hit.get("avatar_254x254_url") or hit.get("avatar_98x98_url") or hit.get("avatar_url"))
    lowered = avatar.lower()
    if not avatar or any(marker in lowered for marker in PLACEHOLDER_MARKERS):
        return ""
    return avatar


def score_hit(target: TargetPlayer, hit: Dict[str, Any]) -> Tuple[float, str]:
    avatar = usable_avatar(hit)
    if not avatar:
        return -999.0, "no_avatar"

    target_name = norm(target.player_name)
    username = norm(hit.get("username"))
    nickname = norm(hit.get("nickname"))
    bio = norm(hit.get("bio"))
    if not target_name:
        return -999.0, "no_name"

    score = 0.0
    reasons: List[str] = []
    if username == target_name:
        score += 70
        reasons.append("username")
    elif nickname == target_name:
        score += 65
        reasons.append("nickname")
    elif len(target_name) >= 5 and target_name in bio:
        score += 58
        reasons.append("bio")
    elif len(target_name) >= 5 and username.startswith(target_name):
        score += 42
        reasons.append("username_prefix")
    elif len(target_name) >= 5 and nickname.startswith(target_name):
        score += 38
        reasons.append("nickname_prefix")
    else:
        return -999.0, "name_mismatch"

    target_country = clean(target.country).upper()
    hit_country = clean(hit.get("country")).upper()
    if target_country not in UNKNOWN_COUNTRIES and hit_country:
        if target_country == hit_country:
            score += 25
            reasons.append("country")
        elif "bio" in reasons:
            score -= 20
            reasons.append(f"country_mismatch:{target_country}/{hit_country}")
        else:
            return -999.0, f"country_mismatch:{target_country}/{hit_country}"

    team_tokens = [norm(target.team_name), norm(target.team_abbrev)]
    if bio and any(token and len(token) >= 3 and token in bio for token in team_tokens):
        score += 10
        reasons.append("team")
    if clean(hit.get("mainGame")).lower() == "valorant":
        score += 15
        reasons.append("valorant")
    if clean(hit.get("type")).lower() == "player":
        score += 5
        reasons.append("player")
    if hit.get("verified"):
        score += 15
        reasons.append("verified")
    score += min(8.0, max(0.0, float(hit.get("view_count_30d") or 0)) / 10.0)
    return score, ",".join(reasons)


def best_match(target: TargetPlayer, hits: List[Dict[str, Any]], min_score: float) -> KonectMatch | None:
    best: KonectMatch | None = None
    for hit in hits:
        score, reason = score_hit(target, hit)
        if score < min_score:
            continue
        avatar = usable_avatar(hit)
        username = clean(hit.get("username"))
        match = KonectMatch(
            player_id=target.player_id,
            player_name=target.player_name,
            username=username,
            nickname=clean(hit.get("nickname")),
            country=clean(hit.get("country")).upper(),
            avatar=avatar,
            source_url=f"https://konect.gg/{username}" if username else "",
            score=round(score, 2),
            reason=reason,
        )
        if best is None or match.score > best.score:
            best = match
    return best


def update_avatar(
    cur: pymysql.cursors.DictCursor,
    *,
    match: KonectMatch,
    include_existing: bool,
) -> int:
    avatar_condition = "" if include_existing else "AND (avatar IS NULL OR avatar = '')"
    cur.execute(
        f"""
        UPDATE valorant_player_basic
        SET avatar = NULLIF(%s, ''),
            avatar_source = 'konect',
            avatar_source_url = NULLIF(%s, ''),
            avatar_checked_at = %s
        WHERE player_id = %s
        {avatar_condition}
        """,
        (match.avatar, match.source_url, now_sql(), match.player_id),
    )
    return int(cur.rowcount or 0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preview or enrich missing Valorant avatars from Konect high-confidence profile matches."
    )
    parser.add_argument("--apply", action="store_true", help="Write matched avatars to MySQL. Default is preview only.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum DB targets by player name. 0 means all.")
    parser.add_argument("--top-missing", type=int, default=120, help="Use top ranked missing-avatar players. 0 disables ranked targeting.")
    parser.add_argument("--player-id", action="append", default=[], help="Only enrich a specific VLR player id. Repeatable.")
    parser.add_argument("--include-existing", action="store_true", help="Allow Konect to replace existing avatars.")
    parser.add_argument("--min-score", type=float, default=95.0, help="Minimum confidence score. Lower is riskier.")
    parser.add_argument("--search-limit", type=int, default=8, help="Konect search hits per player.")
    parser.add_argument("--min-delay", type=float, default=0.25, help="Seconds between Konect search requests.")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    player_ids = [clean(item) for item in args.player_id if clean(item)]
    with get_conn() as conn:
        with conn.cursor() as cur:
            ensure_avatar_columns(cur)
            if args.top_missing > 0 and not player_ids:
                targets = fetch_ranked_missing_targets(args.top_missing)
            else:
                targets = fetch_db_targets(
                    cur,
                    include_existing=args.include_existing,
                    limit=max(0, args.limit),
                    player_ids=player_ids,
                )

    if not targets:
        print("[DONE] No Valorant players need Konect avatar enrichment.", flush=True)
        return 0

    print(
        f"[INFO] Targets: {len(targets)} min_score={args.min_score} apply={bool(args.apply)}",
        flush=True,
    )
    client = KonectClient(args.user_agent, args.min_delay)
    matches: List[KonectMatch] = []
    for idx, target in enumerate(targets, 1):
        try:
            hits = client.search(target.player_name, limit=args.search_limit)
        except requests.RequestException as exc:
            print(f"[WARN] search failed {target.player_id} {target.player_name}: {exc}", flush=True)
            continue
        match = best_match(target, hits, args.min_score)
        if match:
            matches.append(match)
            print(
                "[MATCH] "
                f"{target.player_id} {target.player_name} -> konect/{match.username} "
                f"score={match.score} reason={match.reason}",
                flush=True,
            )
        if idx % 25 == 0:
            print(f"[SCAN] {idx}/{len(targets)} matched={len(matches)}", flush=True)

    if args.apply and matches:
        updated = 0
        with get_conn() as conn:
            with conn.cursor() as cur:
                for match in matches:
                    updated += update_avatar(cur, match=match, include_existing=args.include_existing)
        print(f"[DONE] Konect avatars updated: {updated}/{len(matches)}", flush=True)
    else:
        print(f"[DONE] Preview matches: {len(matches)}. Add --apply to write them.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
