from __future__ import annotations

import argparse
import math
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Set, Tuple

import pandas as pd

from import_lol_to_mysql import (
    CSV_FILES,
    DATABASE_NAME,
    create_database_and_tables,
    get_connection,
    normalize_date_column,
    normalize_datetime_column,
    prepare_dataframe,
    quote_ident,
)


BASE_DIR = Path(__file__).resolve().parent
CRAWLER_SCRIPT = BASE_DIR / "lol_esports_gol.py"
ROSTER_ENRICH_SCRIPT = BASE_DIR / "run_lol_incremental_enrich.py"


def build_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run LoL realtime incremental sync for recent matches, player stats, and rosters."
    )
    parser.add_argument("--lookback-days", type=int, default=10, help="How many past days to resync.")
    parser.add_argument("--future-days", type=int, default=21, help="How many future fixture days to keep fresh.")
    parser.add_argument("--interval-seconds", type=int, default=300, help="Loop interval in seconds.")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit.")
    parser.add_argument(
        "--leagues",
        default=os.getenv(
            "LOL_SCHEDULE_LEAGUES",
            "worlds,msi,first_stand,lck,lpl,lec,lcp,lcs,cblol-brazil,vcs,pcs,lla",
        ),
        help="Comma-separated LoL Esports league slugs.",
    )
    parser.add_argument("--event-detail-workers", type=int, default=8)
    parser.add_argument(
        "--livestats-limit",
        type=int,
        default=160,
        help="Max games per cycle for LoL live stats. 0 means all games in the window.",
    )
    parser.add_argument(
        "--disable-roster-enrich",
        action="store_true",
        help="Skip periodic official roster enrichment after CSV upsert.",
    )
    parser.add_argument(
        "--roster-enrich-interval-hours",
        type=float,
        default=6.0,
        help="Run run_lol_incremental_enrich.py at most once per N hours.",
    )
    parser.add_argument(
        "--no-deactivate-seen-rosters",
        action="store_true",
        help="Do not mark old lol_player_basic rows inactive for teams seen in the current roster window.",
    )
    return parser.parse_args()


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def run_python(script_path: Path, args: Sequence[str], env: Dict[str, str]) -> None:
    print(f"[lol-realtime] run: {script_path.name} {' '.join(args)}", flush=True)
    subprocess.run(
        [sys.executable, str(script_path), *args],
        cwd=str(BASE_DIR),
        env=env,
        check=True,
    )


def schema_info(cursor: Any, table_name: str) -> Tuple[Set[str], Set[str], Set[str]]:
    cursor.execute(f"SHOW COLUMNS FROM {quote_ident(table_name)}")
    schema = cursor.fetchall()
    columns = {row[0] for row in schema}
    date_columns = {row[0] for row in schema if str(row[1]).lower().startswith("date")}
    datetime_columns = {
        row[0]
        for row in schema
        if str(row[1]).lower().startswith("datetime")
        or str(row[1]).lower().startswith("timestamp")
    }
    return columns, date_columns, datetime_columns


def index_names(cursor: Any, table_name: str) -> Set[str]:
    cursor.execute(f"SHOW INDEX FROM {quote_ident(table_name)}")
    return {str(row[2]) for row in cursor.fetchall()}


def dedupe_by_id(cursor: Any, table_name: str, key_columns: Sequence[str]) -> None:
    if not key_columns:
        return
    cursor.execute(f"SHOW COLUMNS FROM {quote_ident(table_name)} LIKE 'id'")
    if cursor.fetchone() is None:
        return
    join_sql = " AND ".join(
        f"a.{quote_ident(col)} <=> b.{quote_ident(col)}" for col in key_columns
    )
    not_null_sql = " AND ".join(
        f"a.{quote_ident(col)} IS NOT NULL AND a.{quote_ident(col)} <> ''" for col in key_columns
    )
    cursor.execute(
        f"""
        DELETE a
        FROM {quote_ident(table_name)} a
        JOIN {quote_ident(table_name)} b
          ON {join_sql}
         AND a.id > b.id
        WHERE {not_null_sql}
        """
    )


def ensure_incremental_indexes(cursor: Any) -> None:
    if "uk_game_stat" not in index_names(cursor, "lol_game_player_stats"):
        dedupe_by_id(cursor, "lol_game_player_stats", ["game_id", "stat_index"])
        cursor.execute(
            "ALTER TABLE lol_game_player_stats ADD UNIQUE KEY uk_game_stat (game_id, stat_index)"
        )


def normalize_for_table(df: pd.DataFrame, date_columns: Set[str], datetime_columns: Set[str]) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        if col in date_columns:
            out[col] = normalize_date_column(out[col])
        elif col in datetime_columns:
            out[col] = normalize_datetime_column(out[col])
    return out


def values_for_rows(df: pd.DataFrame, columns: Sequence[str]) -> List[Tuple[Any, ...]]:
    return [
        tuple(None if pd.isna(value) else value for value in row)
        for row in df[list(columns)].itertuples(index=False, name=None)
    ]


def deactivate_seen_lol_rosters(cursor: Any, df: pd.DataFrame, table_columns: Set[str]) -> int:
    if "is_active" not in table_columns or "team_id" not in df.columns:
        return 0
    team_ids = sorted(
        {
            str(value or "").strip()
            for value in df["team_id"].dropna().tolist()
            if str(value or "").strip()
        }
    )
    if not team_ids:
        return 0
    for chunk_start in range(0, len(team_ids), 500):
        chunk = team_ids[chunk_start : chunk_start + 500]
        placeholders = ", ".join(["%s"] * len(chunk))
        cursor.execute(
            f"UPDATE lol_player_basic SET is_active = 0 WHERE team_id IN ({placeholders})",
            tuple(chunk),
        )
    return len(team_ids)


def upsert_dataframe(
    cursor: Any,
    table_name: str,
    df: pd.DataFrame,
    *,
    deactivate_seen_rosters: bool,
) -> int:
    if df.empty:
        print(f"[lol-realtime] {table_name}: 0 rows", flush=True)
        return 0

    table_columns, date_columns, datetime_columns = schema_info(cursor, table_name)
    if table_name == "lol_player_basic" and "is_active" in table_columns and "is_active" not in df.columns:
        df = df.copy()
        df["is_active"] = "1"
        if deactivate_seen_rosters:
            deactivated = deactivate_seen_lol_rosters(cursor, df, table_columns)
            print(f"[lol-realtime] lol_player_basic inactive team scopes={deactivated}", flush=True)

    selected = [col for col in df.columns if col in table_columns]
    if not selected:
        raise ValueError(f"No matching columns for {table_name}")
    df = normalize_for_table(df[selected], date_columns, datetime_columns)
    rows = values_for_rows(df, selected)
    if not rows:
        return 0

    placeholders = ", ".join(["%s"] * len(selected))
    column_sql = ", ".join(quote_ident(col) for col in selected)
    update_parts = []
    for col in selected:
        if col == "id":
            continue
        if col == "fetched_at" or col == "is_active":
            update_parts.append(f"{quote_ident(col)}=VALUES({quote_ident(col)})")
        else:
            update_parts.append(f"{quote_ident(col)}=COALESCE(VALUES({quote_ident(col)}), {quote_ident(col)})")
    sql = (
        f"INSERT INTO {quote_ident(table_name)} ({column_sql}) VALUES ({placeholders}) "
        f"ON DUPLICATE KEY UPDATE {', '.join(update_parts)}"
    )
    cursor.executemany(sql, rows)
    print(f"[lol-realtime] {table_name}: upserted {len(rows)} rows", flush=True)
    return len(rows)


def upsert_lol_csvs(*, deactivate_seen_rosters: bool) -> None:
    create_database_and_tables()
    with get_connection(DATABASE_NAME) as conn:
        with conn.cursor() as cursor:
            ensure_incremental_indexes(cursor)
            for table_name, csv_path in CSV_FILES.items():
                if not csv_path.exists():
                    print(f"[lol-realtime] missing CSV skipped: {csv_path}", flush=True)
                    continue
                upsert_dataframe(
                    cursor,
                    table_name,
                    prepare_dataframe(csv_path),
                    deactivate_seen_rosters=deactivate_seen_rosters,
                )


def crawler_env(args: argparse.Namespace) -> Dict[str, str]:
    env = os.environ.copy()
    start_date = (datetime.now(timezone.utc) - timedelta(days=max(1, args.lookback_days))).date().isoformat()
    future_months = max(1, math.ceil(max(1, args.future_days) / 31))
    env.update(
        {
            "LOL_GOL_ENABLED": "0",
            "LOL_SCHEDULE_START_DATE": start_date,
            "LOL_SCHEDULE_FUTURE_MONTHS": str(future_months),
            "LOL_SCHEDULE_LEAGUES": args.leagues,
            "LOL_EVENT_DETAILS_ENABLED": "1",
            "LOL_EVENT_DETAILS_WORKERS": str(max(1, args.event_detail_workers)),
            "LOL_LIVESTATS_ENABLED": "1",
            "LOL_LIVESTATS_LIMIT": str(max(0, args.livestats_limit)),
        }
    )
    return env


def run_cycle(args: argparse.Namespace, *, last_roster_enrich_ts: float) -> float:
    print(f"[lol-realtime] cycle start {now_text()}", flush=True)
    env = crawler_env(args)
    run_python(CRAWLER_SCRIPT, [], env)
    upsert_lol_csvs(deactivate_seen_rosters=not args.no_deactivate_seen_rosters)

    now_ts = time.monotonic()
    should_enrich = (
        not args.disable_roster_enrich
        and (
            last_roster_enrich_ts <= 0
            or now_ts - last_roster_enrich_ts >= max(0.1, args.roster_enrich_interval_hours) * 3600
        )
    )
    if should_enrich:
        run_python(
            ROSTER_ENRICH_SCRIPT,
            ["--apply", "--stats-days", str(max(30, args.lookback_days))],
            os.environ.copy(),
        )
        last_roster_enrich_ts = now_ts

    print(f"[lol-realtime] cycle done {now_text()}", flush=True)
    return last_roster_enrich_ts


def main() -> int:
    args = build_args()
    last_roster_enrich_ts = 0.0
    while True:
        try:
            last_roster_enrich_ts = run_cycle(args, last_roster_enrich_ts=last_roster_enrich_ts)
        except subprocess.CalledProcessError as exc:
            print(f"[lol-realtime] cycle failed command exit={exc.returncode}", flush=True)
            if args.once:
                return exc.returncode
        except Exception as exc:
            print(f"[lol-realtime] cycle failed: {exc}", flush=True)
            if args.once:
                return 1
        if args.once:
            return 0
        time.sleep(max(30, args.interval_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
