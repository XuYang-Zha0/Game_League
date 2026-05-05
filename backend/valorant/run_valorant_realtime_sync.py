from __future__ import annotations

import argparse
import csv
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Sequence, Set

from import_valorant_to_mysql import (
    DATABASE_NAME,
    TABLE_COLUMNS,
    create_database_and_tables,
    derive_agent_rows,
    derive_events,
    derive_players,
    derive_relations,
    derive_team_abbrev_lookup,
    derive_teams,
    enrich_match_tables,
    get_connection,
    insert_rows,
    normalize_team_display_names,
    purge_completed_schedule_rows,
    purge_placeholder_map_rows,
    quote_ident,
    read_csv_rows,
)


BASE_DIR = Path(__file__).resolve().parent
SCRAPER_SCRIPT = BASE_DIR / "vlr_experiment.py"
LIQUIPEDIA_AVATAR_SCRIPT = BASE_DIR / "enrich_liquipedia_avatars.py"
DEFAULT_OUTPUT_DIR = BASE_DIR / "vlr_realtime_data"


CSV_BASENAMES = {
    "schedule": "valorant_match_schedule_vlr_experiment.csv",
    "result": "valorant_match_result_vlr_experiment.csv",
    "detail": "valorant_match_detail_vlr_experiment.csv",
    "map_stats": "valorant_match_map_stats_vlr_experiment.csv",
    "player_stats": "valorant_match_player_stats_vlr_experiment.csv",
    "player_summary": "valorant_player_stats_vlr_experiment.csv",
    "player_profile": "valorant_player_profile_vlr_experiment.csv",
}


def build_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Valorant realtime incremental sync for recent VLR matches, player stats, and roster relations."
    )
    parser.add_argument("--interval-seconds", type=int, default=300)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--preset", choices=["quick", "daily", "full"], default="daily")
    parser.add_argument("--fixture-pages", type=int, default=1)
    parser.add_argument("--result-pages", type=int, default=2)
    parser.add_argument("--detail-limit", type=int, default=35)
    parser.add_argument("--stats-limit", type=int, default=120)
    parser.add_argument("--player-profile-limit", type=int, default=80)
    parser.add_argument("--sleep-seconds", type=float, default=0.25)
    parser.add_argument("--detail-workers", type=int, default=4)
    parser.add_argument("--profile-workers", type=int, default=2)
    parser.add_argument(
        "--stats-refresh-interval-minutes",
        type=float,
        default=30.0,
        help="Refresh VLR /stats at most once per N minutes. Match list/detail still runs every cycle.",
    )
    parser.add_argument(
        "--profile-refresh-interval-hours",
        type=float,
        default=6.0,
        help="Refresh player profile pages at most once per N hours.",
    )
    parser.add_argument(
        "--force-profile-refresh",
        action="store_true",
        help="Fetch player profile pages in the first cycle instead of waiting for the profile interval.",
    )
    parser.add_argument("--disable-stats-refresh", action="store_true")
    parser.add_argument("--disable-profile-refresh", action="store_true")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument(
        "--no-deactivate-seen-rosters",
        action="store_true",
        help="Do not mark old valorant_team_player_relation rows inactive for team abbreviations seen in the cycle.",
    )
    parser.add_argument(
        "--enable-liquipedia-avatars",
        action="store_true",
        help="Run the slower Liquipedia avatar fallback after each cycle.",
    )
    parser.add_argument("--liquipedia-limit", type=int, default=120)
    return parser.parse_args()


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def run_python(script_path: Path, args: Sequence[str]) -> None:
    print(f"[valorant-realtime] run: {script_path.name} {' '.join(args)}", flush=True)
    subprocess.run(
        [sys.executable, str(script_path), *args],
        cwd=str(BASE_DIR),
        check=True,
    )


def load_source_rows(output_dir: Path) -> Dict[str, List[Dict[str, Any]]]:
    return {
        key: read_csv_rows(output_dir / basename)
        for key, basename in CSV_BASENAMES.items()
    }


def index_names(cur: Any, table_name: str) -> Set[str]:
    cur.execute(f"SHOW INDEX FROM {quote_ident(table_name)}")
    return {str(row.get("Key_name") or row.get("key_name") or "") for row in cur.fetchall()}


def dedupe_by_id(cur: Any, table_name: str, key_columns: Sequence[str]) -> None:
    cur.execute(f"SHOW COLUMNS FROM {quote_ident(table_name)} LIKE 'id'")
    if cur.fetchone() is None:
        return
    join_sql = " AND ".join(
        f"a.{quote_ident(col)} <=> b.{quote_ident(col)}" for col in key_columns
    )
    not_null_sql = " AND ".join(
        f"a.{quote_ident(col)} IS NOT NULL AND a.{quote_ident(col)} <> ''" for col in key_columns
    )
    cur.execute(
        f"""
        DELETE a
        FROM {quote_ident(table_name)} a
        JOIN {quote_ident(table_name)} b
          ON {join_sql}
         AND a.id > b.id
        WHERE {not_null_sql}
        """
    )


def ensure_incremental_indexes() -> None:
    with get_connection(DATABASE_NAME) as conn:
        with conn.cursor() as cur:
            if "uk_match_game_player" not in index_names(cur, "valorant_match_player_stats"):
                dedupe_by_id(cur, "valorant_match_player_stats", ["match_id", "game_id", "player_id"])
                cur.execute(
                    """
                    ALTER TABLE valorant_match_player_stats
                    ADD UNIQUE KEY uk_match_game_player (match_id, game_id, player_id)
                    """
                )


def deactivate_seen_relations(relations: List[Dict[str, Any]]) -> int:
    team_abbrevs = sorted(
        {
            str(row.get("team_abbrev") or "").strip()
            for row in relations
            if str(row.get("team_abbrev") or "").strip()
        }
    )
    if not team_abbrevs:
        return 0
    with get_connection(DATABASE_NAME) as conn:
        with conn.cursor() as cur:
            for idx in range(0, len(team_abbrevs), 500):
                chunk = team_abbrevs[idx : idx + 500]
                placeholders = ", ".join(["%s"] * len(chunk))
                cur.execute(
                    f"""
                    UPDATE valorant_team_player_relation
                    SET is_active = 0
                    WHERE team_abbrev IN ({placeholders})
                    """,
                    tuple(chunk),
                )
    return len(team_abbrevs)


def incremental_import(output_dir: Path, *, deactivate_seen_rosters: bool) -> None:
    create_database_and_tables()
    ensure_incremental_indexes()
    rows_by_name = load_source_rows(output_dir)
    enrich_match_tables(rows_by_name)
    normalize_team_display_names(rows_by_name)
    team_abbrev_lookup = derive_team_abbrev_lookup(rows_by_name)

    insert_rows(
        "valorant_event_basic",
        derive_events(rows_by_name),
        [
            "event_id", "event_slug", "event_name", "region", "tier", "event_logo",
            "event_start_time", "event_end_time", "source", "source_event_url",
            "fetched_at",
        ],
    )
    insert_rows(
        "valorant_team_basic",
        derive_teams(rows_by_name),
        [
            "team_id", "team_slug", "team_name", "country", "region", "team_logo",
            "source", "source_team_url", "fetched_at",
        ],
    )
    insert_rows(
        "valorant_player_basic",
        derive_players(rows_by_name, team_abbrev_lookup),
        [
            "player_id", "player_slug", "player_name", "country",
            "current_team_abbrev", "current_team_name", "agents", "avatar", "source",
            "source_player_url", "fetched_at",
        ],
    )

    relations = derive_relations(rows_by_name, team_abbrev_lookup)
    if deactivate_seen_rosters:
        count = deactivate_seen_relations(relations)
        print(f"[valorant-realtime] inactive roster scopes={count}", flush=True)
    insert_rows(
        "valorant_team_player_relation",
        relations,
        [
            "team_id", "team_name", "team_abbrev", "player_id", "player_name",
            "is_active", "source", "fetched_at",
        ],
    )

    insert_rows("valorant_match_schedule", rows_by_name["schedule"], TABLE_COLUMNS["valorant_match_schedule"])
    insert_rows("valorant_match_result", rows_by_name["result"], TABLE_COLUMNS["valorant_match_result"])
    insert_rows("valorant_match_detail", rows_by_name["detail"], TABLE_COLUMNS["valorant_match_detail"])
    purge_completed_schedule_rows()
    insert_rows("valorant_match_map_stats", rows_by_name["map_stats"], TABLE_COLUMNS["valorant_match_map_stats"])
    insert_rows("valorant_match_player_stats", rows_by_name["player_stats"], TABLE_COLUMNS["valorant_match_player_stats"])
    purge_placeholder_map_rows()
    insert_rows("valorant_player_stats_summary", rows_by_name["player_summary"], TABLE_COLUMNS["valorant_player_stats_summary"])
    insert_rows(
        "valorant_player_agent_stats",
        derive_agent_rows(rows_by_name),
        ["player_id", "player_name", "team_abbrev", "agent", "source", "fetched_at"],
    )


def scraper_args(
    args: argparse.Namespace,
    output_dir: Path,
    *,
    refresh_stats: bool,
    refresh_profiles: bool,
) -> List[str]:
    out = [
        "--preset", args.preset,
        "--fixture-pages", str(max(1, args.fixture_pages)),
        "--result-pages", str(max(1, args.result_pages)),
        "--detail-limit", str(max(0, args.detail_limit)),
        "--stats-limit", str(max(0, args.stats_limit)),
        "--player-profile-limit", str(max(0, args.player_profile_limit)),
        "--sleep-seconds", str(max(0.1, args.sleep_seconds)),
        "--detail-workers", str(max(1, args.detail_workers)),
        "--profile-workers", str(max(1, args.profile_workers)),
        "--output-dir", str(output_dir),
    ]
    if not refresh_stats:
        out.append("--skip-stats")
    if not refresh_profiles:
        out.append("--skip-player-profiles")
    return out


def is_due(last_ts: float, interval_seconds: float) -> bool:
    if interval_seconds <= 0:
        return True
    return last_ts <= 0 or time.monotonic() - last_ts >= interval_seconds


def run_cycle(
    args: argparse.Namespace,
    *,
    last_stats_refresh_ts: float,
    last_profile_refresh_ts: float,
) -> tuple[float, float]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"[valorant-realtime] cycle start {now_text()}", flush=True)
    refresh_stats = (not args.disable_stats_refresh) and is_due(
        last_stats_refresh_ts,
        max(0.0, args.stats_refresh_interval_minutes) * 60,
    )
    refresh_profiles = (not args.disable_profile_refresh) and is_due(
        last_profile_refresh_ts,
        max(0.0, args.profile_refresh_interval_hours) * 3600,
    )
    print(
        "[valorant-realtime] mode "
        f"stats={'yes' if refresh_stats else 'skip'} "
        f"profiles={'yes' if refresh_profiles else 'skip'}",
        flush=True,
    )
    run_python(
        SCRAPER_SCRIPT,
        scraper_args(
            args,
            output_dir,
            refresh_stats=refresh_stats,
            refresh_profiles=refresh_profiles,
        ),
    )
    incremental_import(output_dir, deactivate_seen_rosters=not args.no_deactivate_seen_rosters)
    now_ts = time.monotonic()
    if refresh_stats:
        last_stats_refresh_ts = now_ts
    if refresh_profiles:
        last_profile_refresh_ts = now_ts
    if args.enable_liquipedia_avatars:
        run_python(
            LIQUIPEDIA_AVATAR_SCRIPT,
            ["--limit", str(max(0, args.liquipedia_limit))],
        )
    print(f"[valorant-realtime] cycle done {now_text()}", flush=True)
    return last_stats_refresh_ts, last_profile_refresh_ts


def main() -> int:
    args = build_args()
    last_stats_refresh_ts = 0.0
    last_profile_refresh_ts = 0.0 if args.force_profile_refresh else time.monotonic()
    while True:
        try:
            last_stats_refresh_ts, last_profile_refresh_ts = run_cycle(
                args,
                last_stats_refresh_ts=last_stats_refresh_ts,
                last_profile_refresh_ts=last_profile_refresh_ts,
            )
        except subprocess.CalledProcessError as exc:
            print(f"[valorant-realtime] cycle failed command exit={exc.returncode}", flush=True)
            if args.once:
                return exc.returncode
        except Exception as exc:
            print(f"[valorant-realtime] cycle failed: {exc}", flush=True)
            if args.once:
                return 1
        if args.once:
            return 0
        time.sleep(max(30, args.interval_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
