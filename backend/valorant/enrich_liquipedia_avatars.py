from __future__ import annotations

import argparse
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

import pymysql
import requests


BASE_DIR = Path(__file__).resolve().parent
LIQUIPEDIA_API_URL = "https://liquipedia.net/valorant/api.php"
DEFAULT_USER_AGENT = (
    "GameLeagueValorantAvatarBot/0.1 "
    "(local Valorant avatar enrichment; set LIQUIPEDIA_USER_AGENT for contact)"
)

COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
PARAM_RE = re.compile(r"^\|\s*([A-Za-z0-9_ -]+)\s*=\s*(.*?)\s*$", re.M)
PLACEHOLDER_FILE_MARKERS = (
    "noimage",
    "no image",
    "placeholder",
    "default",
    "silhouette",
    "filler",
)


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

DATABASE_NAME = os.getenv("CS_DB_NAME", "esports")
DB_CONFIG = {
    "host": os.getenv("CS_DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("CS_DB_PORT", "3306")),
    "user": os.getenv("CS_DB_USER", "root"),
    "password": os.getenv("CS_DB_PASSWORD", ""),
    "database": DATABASE_NAME,
    "charset": "utf8mb4",
    "autocommit": True,
    "cursorclass": pymysql.cursors.DictCursor,
}


@dataclass(frozen=True)
class TargetPlayer:
    player_id: str
    player_name: str
    player_slug: str
    country: str
    team_name: str
    team_abbrev: str


@dataclass
class LiquipediaPlayer:
    title: str
    page_url: str
    image_file: str
    vlr_ids: Set[str]


def quote_ident(name: str) -> str:
    return f"`{str(name).replace('`', '``')}`"


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()


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


def fetch_targets(
    cur: pymysql.cursors.DictCursor,
    *,
    include_existing: bool,
    retry_checked: bool,
    limit: int,
    player_ids: Sequence[str],
) -> List[TargetPlayer]:
    where: List[str] = []
    params: List[Any] = []
    if not include_existing:
        where.append("(avatar IS NULL OR avatar = '')")
    if not retry_checked:
        where.append("avatar_checked_at IS NULL")
    if player_ids:
        where.append(f"player_id IN ({', '.join(['%s'] * len(player_ids))})")
        params.extend(player_ids)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    limit_sql = "LIMIT %s" if limit > 0 else ""
    if limit > 0:
        params.append(limit)
    cur.execute(
        f"""
        SELECT player_id, player_name, player_slug, country,
               current_team_name, current_team_abbrev
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
            player_slug=clean(row.get("player_slug")),
            country=clean(row.get("country")),
            team_name=clean(row.get("current_team_name")),
            team_abbrev=clean(row.get("current_team_abbrev")),
        )
        for row in cur.fetchall()
        if clean(row.get("player_id"))
    ]


class LiquipediaClient:
    def __init__(self, user_agent: str, min_delay: float) -> None:
        self.min_delay = max(0.0, min_delay)
        self.last_request_at = 0.0
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept-Encoding": "gzip",
            }
        )

    def api(self, params: Dict[str, Any]) -> Dict[str, Any]:
        elapsed = time.monotonic() - self.last_request_at
        if elapsed < self.min_delay:
            time.sleep(self.min_delay - elapsed)
        response = self.session.get(LIQUIPEDIA_API_URL, params=params, timeout=30)
        self.last_request_at = time.monotonic()
        response.raise_for_status()
        data = response.json()
        if "error" in data:
            code = data["error"].get("code", "unknown")
            info = data["error"].get("info", "")
            raise RuntimeError(f"Liquipedia API error {code}: {info}")
        return data


def batched(items: Sequence[Any], size: int) -> Iterator[List[Any]]:
    step = max(1, size)
    for idx in range(0, len(items), step):
        yield list(items[idx : idx + step])


def iter_player_category_members(
    client: LiquipediaClient,
    *,
    category: str,
) -> Iterator[Dict[str, Any]]:
    params: Dict[str, Any] = {
        "action": "query",
        "format": "json",
        "list": "categorymembers",
        "cmtitle": category,
        "cmnamespace": 0,
        "cmlimit": "500",
    }
    while True:
        data = client.api(params)
        for member in data.get("query", {}).get("categorymembers", []):
            yield member
        continuation = data.get("continue")
        if not continuation:
            break
        params.update(continuation)


def extract_wikitext(page: Dict[str, Any]) -> str:
    revisions = page.get("revisions") or []
    if not revisions:
        return ""
    revision = revisions[0] or {}
    slots = revision.get("slots") or {}
    main = slots.get("main") or {}
    return str(main.get("*") or main.get("content") or "")


def parse_template_params(wikitext: str) -> Dict[str, str]:
    params: Dict[str, str] = {}
    for match in PARAM_RE.finditer(wikitext):
        key = clean(match.group(1)).lower().replace(" ", "_")
        value = clean(COMMENT_RE.sub("", match.group(2)))
        if key and key not in params:
            params[key] = value
    return params


def extract_vlr_ids(value: Any) -> Set[str]:
    return set(re.findall(r"\d+", clean(COMMENT_RE.sub("", str(value or "")))))


def normalize_image_file(value: Any) -> str:
    text = clean(COMMENT_RE.sub("", str(value or "")))
    if not text:
        return ""
    link_match = re.match(r"\[\[\s*(?:File|Image)\s*:\s*([^|\]]+)", text, re.I)
    if link_match:
        text = clean(link_match.group(1))
    if "|" in text:
        text = clean(text.split("|", 1)[0])
    text = re.sub(r"^(?:File|Image)\s*:\s*", "", text, flags=re.I).strip()
    if not text or "{{" in text or "}}" in text:
        return ""
    lowered = text.lower()
    if any(marker in lowered for marker in PLACEHOLDER_FILE_MARKERS):
        return ""
    return text.replace("_", " ")


def file_key(value: Any) -> str:
    return normalize_image_file(value).lower()


def parse_liquipedia_player(page: Dict[str, Any]) -> Optional[LiquipediaPlayer]:
    wikitext = extract_wikitext(page)
    if "{{infobox player" not in wikitext.lower():
        return None
    params = parse_template_params(wikitext)
    vlr_ids = extract_vlr_ids(params.get("vlr"))
    image_file = normalize_image_file(params.get("image"))
    if not vlr_ids or not image_file:
        return None
    return LiquipediaPlayer(
        title=clean(page.get("title")),
        page_url=clean(page.get("fullurl")),
        image_file=image_file,
        vlr_ids=vlr_ids,
    )


def fetch_pages_by_id(
    client: LiquipediaClient,
    page_ids: Sequence[int],
) -> List[Dict[str, Any]]:
    if not page_ids:
        return []
    data = client.api(
        {
            "action": "query",
            "format": "json",
            "prop": "revisions|info",
            "rvprop": "content",
            "rvslots": "main",
            "inprop": "url",
            "pageids": "|".join(str(page_id) for page_id in page_ids),
            "redirects": 1,
        }
    )
    pages = data.get("query", {}).get("pages", {})
    return [page for page in pages.values() if isinstance(page, dict)]


def collect_matching_liquipedia_players(
    client: LiquipediaClient,
    *,
    target_ids: Set[str],
    category: str,
    batch_size: int,
) -> Dict[str, LiquipediaPlayer]:
    matched: Dict[str, LiquipediaPlayer] = {}
    pending_page_ids: List[int] = []
    scanned_pages = 0

    def flush() -> None:
        nonlocal pending_page_ids, scanned_pages
        if not pending_page_ids:
            return
        pages = fetch_pages_by_id(client, pending_page_ids)
        pending_page_ids = []
        scanned_pages += len(pages)
        for page in pages:
            parsed = parse_liquipedia_player(page)
            if not parsed:
                continue
            for vlr_id in parsed.vlr_ids:
                if vlr_id in target_ids and vlr_id not in matched:
                    matched[vlr_id] = parsed

    for member in iter_player_category_members(client, category=category):
        page_id = member.get("pageid")
        if page_id is None:
            continue
        pending_page_ids.append(int(page_id))
        if len(pending_page_ids) >= batch_size:
            flush()
            print(
                f"[SCAN] Liquipedia pages scanned: {scanned_pages}, matched: {len(matched)}/{len(target_ids)}",
                flush=True,
            )
            if target_ids and len(matched) >= len(target_ids):
                break
    flush()
    print(f"[DONE] Liquipedia pages scanned: {scanned_pages}, matched pages: {len(matched)}", flush=True)
    return matched


def fetch_image_urls(
    client: LiquipediaClient,
    image_files: Iterable[str],
    *,
    batch_size: int,
    thumb_width: int,
) -> Dict[str, str]:
    unique_files = sorted({normalize_image_file(name) for name in image_files if normalize_image_file(name)})
    urls: Dict[str, str] = {}
    for batch in batched(unique_files, batch_size):
        data = client.api(
            {
                "action": "query",
                "format": "json",
                "titles": "|".join(f"File:{name}" for name in batch),
                "prop": "imageinfo",
                "iiprop": "url|mime|size",
                "iiurlwidth": str(max(120, thumb_width)),
            }
        )
        pages = data.get("query", {}).get("pages", {})
        for page in pages.values():
            if not isinstance(page, dict):
                continue
            info_items = page.get("imageinfo") or []
            if not info_items:
                continue
            info = info_items[0] or {}
            mime = clean(info.get("mime")).lower()
            if mime and not mime.startswith("image/"):
                continue
            url = clean(info.get("thumburl") or info.get("url"))
            if not url:
                continue
            urls[file_key(page.get("title"))] = url
    return urls


def update_avatar(
    cur: pymysql.cursors.DictCursor,
    *,
    player_id: str,
    avatar: str,
    source: str,
    source_url: str = "",
    include_existing: bool,
) -> int:
    avatar_condition = "" if include_existing else "AND (avatar IS NULL OR avatar = '')"
    cur.execute(
        f"""
        UPDATE valorant_player_basic
        SET avatar = NULLIF(%s, ''),
            avatar_source = %s,
            avatar_source_url = NULLIF(%s, ''),
            avatar_checked_at = %s
        WHERE player_id = %s
        {avatar_condition}
        """,
        (avatar, source, source_url, now_sql(), player_id),
    )
    return int(cur.rowcount or 0)


def mark_checked_missing(cur: pymysql.cursors.DictCursor, player_id: str) -> int:
    cur.execute(
        """
        UPDATE valorant_player_basic
        SET avatar_source = 'liquipedia_missing',
            avatar_source_url = NULL,
            avatar_checked_at = %s
        WHERE player_id = %s
          AND (avatar IS NULL OR avatar = '')
        """,
        (now_sql(), player_id),
    )
    return int(cur.rowcount or 0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Enrich missing Valorant player avatars from Liquipedia by exact VLR player id."
    )
    parser.add_argument("--limit", type=int, default=0, help="Maximum target players. 0 means all.")
    parser.add_argument("--player-id", action="append", default=[], help="Only enrich a specific VLR player id. Repeatable.")
    parser.add_argument("--include-existing", action="store_true", help="Allow Liquipedia to replace existing avatars.")
    parser.add_argument("--retry-checked", action="store_true", help="Retry rows that were already checked before.")
    parser.add_argument("--dry-run", action="store_true", help="Print what would change without updating MySQL.")
    parser.add_argument("--category", default="Category:Players", help="Liquipedia category to scan.")
    parser.add_argument("--batch-size", type=int, default=50, help="Page revision batch size.")
    parser.add_argument("--image-batch-size", type=int, default=20, help="Imageinfo batch size.")
    parser.add_argument("--thumb-width", type=int, default=384, help="Liquipedia thumbnail width.")
    parser.add_argument(
        "--min-delay",
        type=float,
        default=2.1,
        help="Seconds between Liquipedia API requests. Keep >=2.0 for their public API terms.",
    )
    parser.add_argument(
        "--user-agent",
        default=os.getenv("LIQUIPEDIA_USER_AGENT", DEFAULT_USER_AGENT),
        help="Liquipedia API User-Agent. Prefer setting LIQUIPEDIA_USER_AGENT with contact info.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    with get_conn() as conn:
        with conn.cursor() as cur:
            ensure_avatar_columns(cur)
            targets = fetch_targets(
                cur,
                include_existing=args.include_existing,
                retry_checked=args.retry_checked,
                limit=max(0, args.limit),
                player_ids=[clean(item) for item in args.player_id if clean(item)],
            )

    numeric_targets = [target for target in targets if target.player_id.isdigit()]
    target_ids = {target.player_id for target in numeric_targets}
    if not target_ids:
        print("[DONE] No Valorant players need Liquipedia avatar enrichment.", flush=True)
        return 0

    print(f"[INFO] Target players: {len(target_ids)}", flush=True)
    print(f"[INFO] Liquipedia delay: {args.min_delay:.1f}s/request", flush=True)
    client = LiquipediaClient(args.user_agent, args.min_delay)
    matched = collect_matching_liquipedia_players(
        client,
        target_ids=target_ids,
        category=args.category,
        batch_size=max(1, min(args.batch_size, 50)),
    )
    image_urls = fetch_image_urls(
        client,
        (entry.image_file for entry in matched.values()),
        batch_size=max(1, min(args.image_batch_size, 20)),
        thumb_width=max(120, args.thumb_width),
    )

    updated = 0
    missing = 0
    dry_run_rows: List[Tuple[str, str, str]] = []
    with get_conn() as conn:
        with conn.cursor() as cur:
            for target in numeric_targets:
                entry = matched.get(target.player_id)
                image_url = image_urls.get(file_key(entry.image_file)) if entry else ""
                if entry and image_url:
                    if args.dry_run:
                        dry_run_rows.append((target.player_id, target.player_name, image_url))
                    else:
                        updated += update_avatar(
                            cur,
                            player_id=target.player_id,
                            avatar=image_url,
                            source="liquipedia",
                            source_url=entry.page_url,
                            include_existing=args.include_existing,
                        )
                else:
                    missing += 1
                    if not args.dry_run:
                        mark_checked_missing(cur, target.player_id)

    if args.dry_run:
        for player_id, player_name, image_url in dry_run_rows[:50]:
            print(f"[DRY] {player_id} {player_name}: {image_url}", flush=True)
        if len(dry_run_rows) > 50:
            print(f"[DRY] ... {len(dry_run_rows) - 50} more", flush=True)
        print(f"[DONE] Dry run matched avatars: {len(dry_run_rows)}, missing after Liquipedia: {missing}", flush=True)
    else:
        print(f"[DONE] Liquipedia avatars updated: {updated}, still missing: {missing}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
