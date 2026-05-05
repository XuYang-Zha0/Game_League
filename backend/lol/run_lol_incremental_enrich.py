from __future__ import annotations

import argparse
import os
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import pymysql
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from lol_esports_gol import select_lolesports_roster_players

LOL_ESPORTS_API_BASE = "https://esports-api.lolesports.com/persisted/gw"
DEFAULT_LOL_ESPORTS_API_KEY = "0TvQnueqKa5mxJntVWt0w4LpLfEkrV1Ta8rQBb9Z"


def load_local_env() -> None:
    env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as env_file:
            for line in env_file:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
    except OSError:
        return


def build_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Incrementally enrich LOL MySQL data with official lolesports assets "
            "and stabilize player->team mapping using existing stats."
        )
    )
    parser.add_argument("--apply", action="store_true", help="Apply DB updates (default is dry-run)")
    parser.add_argument(
        "--stats-days",
        type=int,
        default=180,
        help="Recent stats window in days for stats-derived fallback mapping",
    )
    parser.add_argument(
        "--max-players",
        type=int,
        default=0,
        help="Optional cap for processed players (0 means unlimited)",
    )
    parser.add_argument(
        "--print-samples",
        type=int,
        default=15,
        help="How many sample mappings to print for quick sanity-check",
    )
    parser.add_argument(
        "--include-inactive",
        action="store_true",
        help="Include inactive teams from lolesports API (default only active teams)",
    )
    parser.add_argument(
        "--include-unmatched-teams",
        action="store_true",
        help="Include teams not found in local DB hints (default only matched teams)",
    )
    parser.add_argument(
        "--allow-stats-derived",
        action="store_true",
        help="Allow stats-derived fallback writes into lol_player_basic (default disabled)",
    )
    return parser.parse_args()


def quote_ident(name: str) -> str:
    return f"`{str(name).replace('`', '``')}`"


def normalize_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def slug_to_local_id(slug: str, name: str, code: str = "") -> str:
    candidates = [code, slug, name]
    for item in candidates:
        out = re.sub(r"[^A-Za-z0-9]+", "_", str(item or "").strip()).strip("_").lower()
        if out:
            return out
    return ""


def normalize_image(url: Any) -> str:
    text = str(url or "").strip()
    if not text:
        return ""
    if text.startswith("//"):
        return f"https:{text}"
    if text.startswith("http://"):
        return "https://" + text[len("http://") :]
    return text


def to_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    text = str(value).strip()
    if text == "":
        return default
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return default


def build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=32, pool_maxsize=32)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def fetch_lol_teams() -> List[Dict[str, Any]]:
    session = build_session()
    api_key = os.getenv("LOL_ESPORTS_API_KEY", DEFAULT_LOL_ESPORTS_API_KEY)
    response = session.get(
        f"{LOL_ESPORTS_API_BASE}/getTeams",
        params={"hl": "en-US"},
        headers={
            "Accept": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/141.0.0.0 Safari/537.36"
            ),
            "x-api-key": api_key,
        },
        timeout=(5, 25),
    )
    response.raise_for_status()
    return response.json().get("data", {}).get("teams", []) or []


def db_config() -> Dict[str, Any]:
    return {
        "host": os.getenv("CS_DB_HOST", "127.0.0.1"),
        "port": int(os.getenv("CS_DB_PORT", "3306")),
        "user": os.getenv("CS_DB_USER", "root"),
        "password": os.getenv("CS_DB_PASSWORD", ""),
        "database": os.getenv("CS_DB_NAME", "esports"),
        "charset": "utf8mb4",
        "autocommit": False,
        "cursorclass": pymysql.cursors.DictCursor,
    }


def table_exists(cur: pymysql.cursors.DictCursor, table_name: str) -> bool:
    cur.execute("SHOW TABLES LIKE %s", (table_name,))
    return cur.fetchone() is not None


def table_columns(cur: pymysql.cursors.DictCursor, table_name: str) -> Set[str]:
    cur.execute(f"SHOW COLUMNS FROM {quote_ident(table_name)}")
    return {str(row.get("Field")) for row in cur.fetchall()}


def fetch_all(cur: pymysql.cursors.DictCursor, sql: str, params: Optional[Tuple[Any, ...]] = None) -> List[Dict[str, Any]]:
    cur.execute(sql, params or ())
    return list(cur.fetchall() or [])


def choose_col(cols: Set[str], candidates: Iterable[str]) -> Optional[str]:
    for c in candidates:
        if c in cols:
            return c
    return None


def ensure_enrich_columns(cur: pymysql.cursors.DictCursor, apply_changes: bool) -> None:
    migrations = {
        "lol_team_basic": {
            "team_logo": "ALTER TABLE lol_team_basic ADD COLUMN team_logo VARCHAR(500) AFTER region",
        },
        "lol_player_basic": {
            "avatar": "ALTER TABLE lol_player_basic ADD COLUMN avatar VARCHAR(500) AFTER role",
        },
    }
    for table_name, column_sql in migrations.items():
        if not table_exists(cur, table_name):
            continue
        existing = table_columns(cur, table_name)
        for column_name, sql in column_sql.items():
            if column_name in existing:
                continue
            if apply_changes:
                cur.execute(sql)
                print(f"[lol-enrich] added column {table_name}.{column_name}")
            else:
                print(f"[lol-enrich] would add column {table_name}.{column_name}")


def collect_local_team_hints(cur: pymysql.cursors.DictCursor) -> Dict[str, str]:
    hint_counter: Dict[str, Counter] = defaultdict(Counter)

    if table_exists(cur, "lol_team_basic"):
        for row in fetch_all(
            cur,
            """
            SELECT team_id, team_name, COUNT(*) AS cnt
            FROM lol_team_basic
            WHERE team_id IS NOT NULL AND team_id <> ''
            GROUP BY team_id, team_name
            """,
        ):
            key = normalize_key(row.get("team_name"))
            if key:
                hint_counter[key][str(row.get("team_id"))] += to_int(row.get("cnt"), 1)

    if table_exists(cur, "lol_game_player_stats"):
        for row in fetch_all(
            cur,
            """
            SELECT team_id, team_name, COUNT(*) AS cnt
            FROM lol_game_player_stats
            WHERE team_id IS NOT NULL AND team_id <> ''
            GROUP BY team_id, team_name
            """,
        ):
            key = normalize_key(row.get("team_name"))
            if key:
                hint_counter[key][str(row.get("team_id"))] += to_int(row.get("cnt"), 1)

    if table_exists(cur, "lol_match_result"):
        for row in fetch_all(
            cur,
            """
            SELECT team_id, team_name, SUM(cnt) AS cnt
            FROM (
              SELECT team1_id AS team_id, team1 AS team_name, COUNT(*) AS cnt
              FROM lol_match_result
              GROUP BY team1_id, team1
              UNION ALL
              SELECT team2_id AS team_id, team2 AS team_name, COUNT(*) AS cnt
              FROM lol_match_result
              GROUP BY team2_id, team2
            ) t
            WHERE team_id IS NOT NULL AND team_id <> ''
            GROUP BY team_id, team_name
            """,
        ):
            key = normalize_key(row.get("team_name"))
            if key:
                hint_counter[key][str(row.get("team_id"))] += to_int(row.get("cnt"), 1)

    resolved: Dict[str, str] = {}
    for key, counter in hint_counter.items():
        if not key or not counter:
            continue
        resolved[key] = counter.most_common(1)[0][0]
    return resolved


def collect_local_player_hints(cur: pymysql.cursors.DictCursor) -> Dict[str, str]:
    hint_counter: Dict[str, Counter] = defaultdict(Counter)

    queries: List[str] = []
    if table_exists(cur, "lol_player_basic"):
        queries.append(
            """
            SELECT player_id, player_name, team_id, team_name, COUNT(*) AS cnt
            FROM lol_player_basic
            WHERE player_id IS NOT NULL AND player_id <> ''
            GROUP BY player_id, player_name, team_id, team_name
            """
        )
    if table_exists(cur, "lol_game_player_stats"):
        queries.append(
            """
            SELECT player_id, player_name, team_id, team_name, COUNT(*) AS cnt
            FROM lol_game_player_stats
            WHERE player_id IS NOT NULL AND player_id <> ''
            GROUP BY player_id, player_name, team_id, team_name
            """
        )

    for sql in queries:
        for row in fetch_all(cur, sql):
            player_id = str(row.get("player_id") or "").strip()
            player_name_key = normalize_key(row.get("player_name"))
            team_id_key = normalize_key(row.get("team_id"))
            team_name_key = normalize_key(row.get("team_name"))
            count = to_int(row.get("cnt"), 1)
            if not player_id or not player_name_key:
                continue
            hint_counter[f"name:{player_name_key}"][player_id] += count
            if team_id_key:
                hint_counter[f"team_id:{team_id_key}:{player_name_key}"][player_id] += count
            if team_name_key:
                hint_counter[f"team_name:{team_name_key}:{player_name_key}"][player_id] += count

    resolved: Dict[str, str] = {}
    for key, counter in hint_counter.items():
        if not counter:
            continue
        if key.startswith("name:") and len(counter) > 1:
            continue
        resolved[key] = counter.most_common(1)[0][0]
    return resolved


def resolve_team_id(team: Dict[str, Any], local_hints: Dict[str, str]) -> str:
    name = str(team.get("name") or "").strip()
    code = str(team.get("code") or "").strip()
    slug = str(team.get("slug") or "").strip()
    for candidate in (name, code, slug):
        key = normalize_key(candidate)
        if key and key in local_hints:
            return local_hints[key]
    return slug_to_local_id(slug, name, code)


def team_matches_local(team: Dict[str, Any], local_hints: Dict[str, str]) -> bool:
    for candidate in (team.get("name"), team.get("code"), team.get("slug")):
        key = normalize_key(candidate)
        if key and key in local_hints:
            return True
    return False


def resolve_player_id(
    player: Dict[str, Any],
    team_id: str,
    team_name: str,
    local_player_hints: Dict[str, str],
) -> str:
    official_id = str(player.get("id") or "").strip()
    names = [
        str(player.get("summonerName") or "").strip(),
        str(player.get("name") or "").strip(),
    ]
    team_id_key = normalize_key(team_id)
    team_name_key = normalize_key(team_name)
    for name in names:
        player_name_key = normalize_key(name)
        if not player_name_key:
            continue
        for key in (
            f"team_id:{team_id_key}:{player_name_key}",
            f"team_name:{team_name_key}:{player_name_key}",
            f"name:{player_name_key}",
        ):
            if key in local_player_hints:
                return local_player_hints[key]
    return official_id


def upsert_team_basic(
    cur: pymysql.cursors.DictCursor,
    cols: Set[str],
    team_id: str,
    team_name: str,
    region: str,
    image: str,
    apply_changes: bool,
) -> str:
    if not team_id:
        return "skip"

    logo_col = choose_col(cols, ("team_logo", "logo", "image", "icon", "team_image"))
    set_pairs = []
    params: List[Any] = []
    for key, value in (
        ("team_name", team_name),
        ("region", region),
        ("source", "lolesports"),
    ):
        if key in cols:
            set_pairs.append(f"{quote_ident(key)}=%s")
            params.append(value)
    if logo_col and image:
        set_pairs.append(f"{quote_ident(logo_col)}=%s")
        params.append(image)
    if "updated_at" in cols:
        set_pairs.append(f"{quote_ident('updated_at')}=%s")
        params.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    if not set_pairs:
        return "skip"

    if not apply_changes:
        return "would_update"

    cur.execute(f"SELECT 1 FROM {quote_ident('lol_team_basic')} WHERE {quote_ident('team_id')}=%s LIMIT 1", (team_id,))
    exists = cur.fetchone() is not None
    params.append(team_id)
    cur.execute(
        f"UPDATE {quote_ident('lol_team_basic')} SET {', '.join(set_pairs)} WHERE {quote_ident('team_id')}=%s",
        tuple(params),
    )
    if exists:
        return "updated"

    insert_cols = [c for c in ("team_id", "team_name", "region", "source", logo_col, "created_at", "updated_at") if c and c in cols]
    insert_values: List[Any] = []
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for col in insert_cols:
        if col == "team_id":
            insert_values.append(team_id)
        elif col == "team_name":
            insert_values.append(team_name)
        elif col == "region":
            insert_values.append(region)
        elif col == "source":
            insert_values.append("lolesports")
        elif col == logo_col:
            insert_values.append(image)
        elif col in ("created_at", "updated_at"):
            insert_values.append(now_text)
        else:
            insert_values.append("")
    cur.execute(
        f"INSERT INTO {quote_ident('lol_team_basic')} ({', '.join(quote_ident(c) for c in insert_cols)}) "
        f"VALUES ({', '.join(['%s'] * len(insert_cols))})",
        tuple(insert_values),
    )
    return "inserted"


def upsert_player_basic(
    cur: pymysql.cursors.DictCursor,
    cols: Set[str],
    player: Dict[str, Any],
    player_id: str,
    team_id: str,
    team_name: str,
    apply_changes: bool,
) -> str:
    player_id = str(player_id or "").strip()
    if not player_id:
        return "skip"
    player_name = str(player.get("summonerName") or player.get("name") or "").strip()
    role = str(player.get("role") or "").strip().upper()
    avatar = normalize_image(player.get("image"))
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    avatar_cols = [c for c in ("portrait", "half_portrait", "avatar", "image", "headshot", "photo") if c in cols]
    source_exists = "source" in cols

    update_pairs = []
    update_params: List[Any] = []
    for key, value in (
        ("player_name", player_name),
        ("team_id", team_id),
        ("team_name", team_name),
        ("role", role),
    ):
        if key in cols and value:
            update_pairs.append(f"{quote_ident(key)}=%s")
            update_params.append(value)
    if "is_active" in cols:
        update_pairs.append(f"{quote_ident('is_active')}=%s")
        update_params.append(1)
    if source_exists:
        update_pairs.append(f"{quote_ident('source')}=%s")
        update_params.append("lolesports")
    if avatar:
        for col in avatar_cols:
            update_pairs.append(f"{quote_ident(col)}=%s")
            update_params.append(avatar)
    if "updated_at" in cols:
        update_pairs.append(f"{quote_ident('updated_at')}=%s")
        update_params.append(now_text)
    if not update_pairs:
        return "skip"

    if not apply_changes:
        return "would_update"

    target_player_id = player_id
    target_team_id = team_id

    # Merge rule: same player name should be merged first, regardless of source.
    cur.execute(
        f"SELECT {quote_ident('player_id')} AS player_id, {quote_ident('team_id')} AS team_id "
        f"FROM {quote_ident('lol_player_basic')} "
        f"WHERE LOWER({quote_ident('player_name')})=LOWER(%s) AND {quote_ident('team_id')}=%s LIMIT 1",
        (player_name, team_id),
    )
    same_name_same_team = cur.fetchone()
    if same_name_same_team:
        target_player_id = str(same_name_same_team.get("player_id") or target_player_id).strip()
        target_team_id = str(same_name_same_team.get("team_id") or target_team_id).strip()
    else:
        cur.execute(
            f"SELECT {quote_ident('player_id')} AS player_id, {quote_ident('team_id')} AS team_id "
            f"FROM {quote_ident('lol_player_basic')} "
            f"WHERE LOWER({quote_ident('player_name')})=LOWER(%s) LIMIT 1",
            (player_name,),
        )
        same_name_any_team = cur.fetchone()
        if same_name_any_team:
            target_player_id = str(same_name_any_team.get("player_id") or target_player_id).strip()
            target_team_id = team_id

    cur.execute(
        f"SELECT 1 FROM {quote_ident('lol_player_basic')} "
        f"WHERE {quote_ident('player_id')}=%s AND {quote_ident('team_id')}=%s LIMIT 1",
        (target_player_id, target_team_id),
    )
    exists = cur.fetchone() is not None
    cur.execute(
        f"UPDATE {quote_ident('lol_player_basic')} "
        f"SET {', '.join(update_pairs)} "
        f"WHERE {quote_ident('player_id')}=%s AND {quote_ident('team_id')}=%s",
        tuple(update_params + [target_player_id, target_team_id]),
    )
    if exists:
        return "updated"

    insert_cols = [c for c in ("player_id", "player_name", "team_id", "team_name", "role", "is_active", "source", "created_at", "updated_at") if c in cols]
    insert_cols.extend([c for c in avatar_cols if c not in insert_cols])
    insert_cols = list(dict.fromkeys(insert_cols))

    insert_values: List[Any] = []
    for col in insert_cols:
        if col == "player_id":
            insert_values.append(player_id)
        elif col == "player_name":
            insert_values.append(player_name)
        elif col == "team_id":
            insert_values.append(team_id)
        elif col == "team_name":
            insert_values.append(team_name)
        elif col == "role":
            insert_values.append(role)
        elif col == "is_active":
            insert_values.append(1)
        elif col == "source":
            insert_values.append("lolesports")
        elif col in ("created_at", "updated_at"):
            insert_values.append(now_text)
        elif col in avatar_cols:
            insert_values.append(avatar)
        else:
            insert_values.append("")

    cur.execute(
        f"INSERT INTO {quote_ident('lol_player_basic')} ({', '.join(quote_ident(c) for c in insert_cols)}) "
        f"VALUES ({', '.join(['%s'] * len(insert_cols))})",
        tuple(insert_values),
    )
    return "inserted"


def upsert_stats_derived_fallback(
    cur: pymysql.cursors.DictCursor,
    cols: Set[str],
    stats_days: int,
    official_player_ids: Set[str],
    apply_changes: bool,
    max_players: int,
) -> Dict[str, int]:
    out = {"inserted": 0, "updated": 0, "skipped": 0}
    if "player_id" not in cols or "team_id" not in cols:
        return out
    if not table_exists(cur, "lol_game_player_stats"):
        return out

    rows = fetch_all(
        cur,
        """
        SELECT
            gps.player_id,
            MAX(NULLIF(gps.player_name, '')) AS player_name,
            gps.team_id,
            MAX(NULLIF(gps.team_name, '')) AS team_name,
            COUNT(*) AS games
        FROM lol_game_player_stats gps
        LEFT JOIN lol_match_result mr ON mr.match_id = gps.match_id
        WHERE gps.player_id IS NOT NULL
          AND gps.player_id <> ''
          AND (
            mr.match_time IS NULL
            OR mr.match_time >= DATE_SUB(NOW(), INTERVAL %s DAY)
          )
        GROUP BY gps.player_id, gps.team_id
        ORDER BY gps.player_id, games DESC
        """,
        (max(30, stats_days),),
    )

    best_by_player: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        player_id = str(row.get("player_id") or "").strip()
        if not player_id or player_id in official_player_ids:
            continue
        old = best_by_player.get(player_id)
        if old is None or to_int(row.get("games")) > to_int(old.get("games")):
            best_by_player[player_id] = row

    source_exists = "source" in cols
    timestamp_col = choose_col(cols, ("updated_at", "fetched_at", "created_at"))
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    processed = 0

    for player_id, row in best_by_player.items():
        if max_players > 0 and processed >= max_players:
            break
        processed += 1

        player_name = str(row.get("player_name") or "").strip()
        team_id = str(row.get("team_id") or "").strip()
        team_name = str(row.get("team_name") or "").strip()
        if not team_id:
            out["skipped"] += 1
            continue

        if not apply_changes:
            out["updated"] += 1
            continue

        update_pairs = [
            f"{quote_ident('player_name')}=%s",
            f"{quote_ident('team_name')}=%s",
        ]
        update_params: List[Any] = [player_name, team_name]
        if source_exists:
            update_pairs.append(f"{quote_ident('source')}=%s")
            update_params.append("stats-derived")
        if timestamp_col:
            update_pairs.append(f"{quote_ident(timestamp_col)}=%s")
            update_params.append(now_text)

        target_player_id = player_id
        target_team_id = team_id

        cur.execute(
            f"SELECT {quote_ident('player_id')} AS player_id, {quote_ident('team_id')} AS team_id "
            f"FROM {quote_ident('lol_player_basic')} "
            f"WHERE {quote_ident('player_id')}=%s AND {quote_ident('team_id')}=%s LIMIT 1",
            (player_id, team_id),
        )
        row_by_id_team = cur.fetchone()
        if row_by_id_team:
            target_player_id = str(row_by_id_team.get("player_id") or target_player_id).strip()
            target_team_id = str(row_by_id_team.get("team_id") or target_team_id).strip()
        else:
            cur.execute(
                f"SELECT {quote_ident('player_id')} AS player_id, {quote_ident('team_id')} AS team_id "
                f"FROM {quote_ident('lol_player_basic')} "
                f"WHERE LOWER({quote_ident('player_name')})=LOWER(%s) LIMIT 1",
                (player_name,),
            )
            row_by_name = cur.fetchone()
            if row_by_name:
                target_player_id = str(row_by_name.get("player_id") or target_player_id).strip()
                target_team_id = str(row_by_name.get("team_id") or target_team_id).strip()

        cur.execute(
            f"SELECT 1 FROM {quote_ident('lol_player_basic')} "
            f"WHERE {quote_ident('player_id')}=%s AND {quote_ident('team_id')}=%s LIMIT 1",
            (target_player_id, target_team_id),
        )
        exists = cur.fetchone() is not None
        if exists:
            cur.execute(
                f"UPDATE {quote_ident('lol_player_basic')} "
                f"SET {', '.join(update_pairs)} "
                f"WHERE {quote_ident('player_id')}=%s AND {quote_ident('team_id')}=%s",
                tuple(update_params + [target_player_id, target_team_id]),
            )
            out["updated"] += 1
            continue

        # Do not insert new rows from stats-derived fallback.
        # If no existing same-name/same-id record is found, skip to avoid polluting player_basic.
        out["skipped"] += 1

    return out


def main() -> int:
    args = build_args()
    load_local_env()

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(
        f"[lol-enrich] mode={mode} stats_days={max(30, args.stats_days)} max_players={max(0, args.max_players)} "
        f"include_inactive={bool(args.include_inactive)} "
        f"include_unmatched={bool(args.include_unmatched_teams)} "
        f"time={datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    all_teams = fetch_lol_teams()
    teams = all_teams if args.include_inactive else [t for t in all_teams if str(t.get("status") or "").strip().lower() == "active"]
    print(f"[lol-enrich] fetched teams={len(all_teams)} active_filter={len(teams)} from lolesports API")

    conn = pymysql.connect(**db_config())
    try:
        with conn.cursor() as cur:
            if not table_exists(cur, "lol_player_basic"):
                raise RuntimeError("lol_player_basic table not found")
            if not table_exists(cur, "lol_team_basic"):
                raise RuntimeError("lol_team_basic table not found")

            ensure_enrich_columns(cur, args.apply)
            team_cols = table_columns(cur, "lol_team_basic")
            player_cols = table_columns(cur, "lol_player_basic")
            local_team_hints = collect_local_team_hints(cur)
            local_player_hints = collect_local_player_hints(cur)
            print(f"[lol-enrich] local team hints={len(local_team_hints)}")
            print(f"[lol-enrich] local player hints={len(local_player_hints)}")
            if not args.include_unmatched_teams:
                teams = [t for t in teams if team_matches_local(t, local_team_hints)]
                print(f"[lol-enrich] matched_teams={len(teams)} (after local hint filter)")

            team_stats = Counter()
            player_stats = Counter()
            official_player_ids: Set[str] = set()
            sample_pairs: List[Tuple[str, str, str]] = []
            processed_players = 0

            for team in teams:
                team_name = str(team.get("name") or "").strip()
                region = str((team.get("homeLeague") or {}).get("name") or (team.get("homeLeague") or {}).get("region") or "").strip()
                image = normalize_image(team.get("image") or team.get("alternativeImage"))
                resolved_team_id = resolve_team_id(team, local_team_hints)
                if not resolved_team_id:
                    continue
                team_stats[upsert_team_basic(cur, team_cols, resolved_team_id, team_name, region, image, args.apply)] += 1
                if args.apply and "is_active" in player_cols:
                    cur.execute(
                        f"UPDATE {quote_ident('lol_player_basic')} "
                        f"SET {quote_ident('is_active')}=0 "
                        f"WHERE {quote_ident('team_id')}=%s",
                        (resolved_team_id,),
                    )

                for player in select_lolesports_roster_players(team):
                    player_id = str(player.get("id") or "").strip()
                    if not player_id:
                        continue
                    if args.max_players > 0 and processed_players >= args.max_players:
                        break
                    processed_players += 1
                    resolved_player_id = resolve_player_id(player, resolved_team_id, team_name, local_player_hints)
                    official_player_ids.add(resolved_player_id)
                    player_stats[
                        upsert_player_basic(cur, player_cols, player, resolved_player_id, resolved_team_id, team_name, args.apply)
                    ] += 1
                    if len(sample_pairs) < max(1, args.print_samples):
                        sample_pairs.append((resolved_player_id, str(player.get("summonerName") or ""), team_name))
                if args.max_players > 0 and processed_players >= args.max_players:
                    break

            fallback_stats = (
                upsert_stats_derived_fallback(
                    cur,
                    player_cols,
                    stats_days=max(30, args.stats_days),
                    official_player_ids=official_player_ids,
                    apply_changes=args.apply,
                    max_players=max(0, args.max_players),
                )
                if args.allow_stats_derived
                else {"inserted": 0, "updated": 0, "skipped": 0}
            )

            if args.apply:
                conn.commit()
            else:
                conn.rollback()

            print(
                "[lol-enrich] team_upsert "
                f"updated={team_stats.get('updated', 0)} inserted={team_stats.get('inserted', 0)} "
                f"would_update={team_stats.get('would_update', 0)} skipped={team_stats.get('skip', 0)}"
            )
            print(
                "[lol-enrich] player_upsert "
                f"updated={player_stats.get('updated', 0)} inserted={player_stats.get('inserted', 0)} "
                f"would_update={player_stats.get('would_update', 0)} skipped={player_stats.get('skip', 0)}"
            )
            print(
                "[lol-enrich] stats_derived "
                f"updated={fallback_stats.get('updated', 0)} inserted={fallback_stats.get('inserted', 0)} "
                f"skipped={fallback_stats.get('skipped', 0)}"
            )

            if sample_pairs:
                print("[lol-enrich] sample_mappings:")
                for pid, pname, tname in sample_pairs:
                    print(f"  - {pid} | {pname} -> {tname}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
