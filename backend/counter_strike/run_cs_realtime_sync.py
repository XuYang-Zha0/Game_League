from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import pymysql

SCRIPT_VERSION = "realtime-sync-v2-quoted-columns"
PLAYER_REFRESH_SEEN: Dict[str, float] = {}
TEAM_ROSTER_LAST_REFRESH_TS = 0.0
TEAM_SNAPSHOT_LAST_REFRESH_TS = 0.0


def build_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run CS2 realtime sync (scores + full match details) in a rolling window."
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=20,
        help="Rolling lookback days from now (ignored when --start-date is provided)",
    )
    parser.add_argument(
        "--start-date",
        default="",
        help="Optional fixed window start date, format: YYYY-MM-DD",
    )
    parser.add_argument(
        "--future-days",
        type=int,
        default=20,
        help="How many future days of fixtures to keep synced",
    )
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=30,
        help="Realtime sync interval in seconds",
    )
    parser.add_argument(
        "--schedule-pages",
        type=int,
        default=30,
        help="How many schedule pages to poll each cycle",
    )
    parser.add_argument(
        "--result-pages",
        type=int,
        default=30,
        help="How many result pages to poll each cycle",
    )
    parser.add_argument(
        "--detail-max-matches",
        type=int,
        default=2000,
        help="Max finished matches to evaluate for detail refresh each cycle",
    )
    parser.add_argument(
        "--detail-workers",
        type=int,
        default=16,
        help="Concurrent workers for detail API fetching",
    )
    parser.add_argument(
        "--detail-stale-hours",
        type=int,
        default=6,
        help="Refresh detail rows older than this many hours",
    )
    parser.add_argument(
        "--detail-force-refresh-incomplete",
        action="store_true",
        help="Force refresh incomplete detail rows in window regardless of fetched_at freshness",
    )
    parser.add_argument(
        "--stale-live-hours",
        type=int,
        default=8,
        help="Auto-fix status=1 matches older than this many hours to finished status",
    )
    parser.add_argument(
        "--disable-detail-sync",
        action="store_true",
        help="Only sync schedule/result, skip detail table sync",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one sync cycle and exit",
    )
    parser.add_argument(
        "--disable-player-refresh",
        action="store_true",
        help="Skip player profile/stat refresh for players seen in freshly synced matches.",
    )
    parser.add_argument(
        "--player-refresh-max-per-cycle",
        type=int,
        default=50,
        help="Max distinct players to refresh after each detail sync cycle.",
    )
    parser.add_argument(
        "--player-refresh-workers",
        type=int,
        default=8,
        help="Concurrent workers for player profile/stat API refresh.",
    )
    parser.add_argument(
        "--player-refresh-ttl-hours",
        type=int,
        default=12,
        help="Do not refresh the same player again within this running process for N hours.",
    )
    parser.add_argument(
        "--disable-player-refresh-window-scan",
        action="store_true",
        help="Only refresh players from this cycle; skip scanning all players in the lookback window.",
    )
    parser.add_argument(
        "--disable-team-roster-refresh",
        action="store_true",
        help="Skip periodic current team-player roster refresh.",
    )
    parser.add_argument(
        "--team-roster-refresh-interval-hours",
        type=int,
        default=6,
        help="Refresh current team-player relation at most once per N hours.",
    )
    parser.add_argument(
        "--team-roster-refresh-pages",
        type=int,
        default=120,
        help="Max team-list pages to crawl when refreshing current rosters.",
    )
    parser.add_argument(
        "--team-roster-refresh-workers",
        type=int,
        default=16,
        help="Concurrent workers for current roster refresh.",
    )
    parser.add_argument(
        "--disable-team-snapshot-refresh",
        action="store_true",
        help="Skip periodic team rank/stat snapshot refresh.",
    )
    parser.add_argument(
        "--team-snapshot-refresh-interval-hours",
        type=int,
        default=6,
        help="Refresh team_rank_snapshot/team_stat_snapshot at most once per N hours.",
    )
    parser.add_argument(
        "--team-snapshot-refresh-pages",
        type=int,
        default=120,
        help="Max team-list pages to crawl when refreshing rank/stat snapshots.",
    )
    parser.add_argument(
        "--team-snapshot-refresh-workers",
        type=int,
        default=16,
        help="Concurrent workers for team rank/stat snapshot refresh.",
    )
    return parser.parse_args()


def configure_env(args: argparse.Namespace) -> None:
    os.environ["CS_LIVE_SYNC_ENABLED"] = "1"
    os.environ["CS_LIVE_SYNC_LOOKBACK_DAYS"] = str(max(1, args.lookback_days))
    if args.start_date:
        os.environ["CS_LIVE_SYNC_START_DATE"] = args.start_date
    else:
        os.environ.pop("CS_LIVE_SYNC_START_DATE", None)
    os.environ["CS_LIVE_SYNC_FUTURE_DAYS"] = str(max(1, args.future_days))
    os.environ["CS_LIVE_SYNC_SCHEDULE_PAGES"] = str(max(1, args.schedule_pages))
    os.environ["CS_LIVE_SYNC_RESULT_PAGES"] = str(max(1, args.result_pages))
    os.environ["CS_LIVE_SYNC_MIN_GAP_SECONDS"] = "1"


def parse_start_dt(args: argparse.Namespace) -> datetime:
    text = str(args.start_date or "").strip()
    if text:
        try:
            return datetime.strptime(text, "%Y-%m-%d")
        except ValueError:
            pass
    return datetime.now() - timedelta(days=max(1, args.lookback_days))


def chunked(items: Sequence[str], size: int) -> Iterable[Sequence[str]]:
    step = max(1, size)
    for i in range(0, len(items), step):
        yield items[i : i + step]


def table_exists(cur: pymysql.cursors.DictCursor, table_name: str) -> bool:
    cur.execute("SHOW TABLES LIKE %s", (table_name,))
    return cur.fetchone() is not None


def to_nullable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text if text else None
    return value


def quote_ident(name: str) -> str:
    return f"`{str(name).replace('`', '``')}`"


def collect_detail_candidates(
    db_config: Dict[str, Any],
    *,
    start_dt: datetime,
    max_matches: int,
    stale_hours: int,
    force_refresh_incomplete: bool,
) -> List[Dict[str, Any]]:
    safe_limit = max(1, max_matches)
    stale_before = datetime.now() - timedelta(hours=max(1, stale_hours))
    with pymysql.connect(**db_config) as conn:
        with conn.cursor() as cur:
            if not table_exists(cur, "match_result"):
                return []
            has_detail = table_exists(cur, "match_result_detail")

            if has_detail:
                force_sql = "1 = 1" if force_refresh_incomplete else "0 = 1"
                cur.execute(
                    f"""
                    SELECT
                        mr.match_id, mr.match_time, mr.bo,
                        mr.team1_id, mr.team1, mr.team2_id, mr.team2,
                        mr.event_id, mr.event_name, mr.event_logo,
                        mr.event_start_time, mr.event_end_time,
                        mr.score1, mr.score2, mr.status,
                        mr.bout_count, mr.bout_details
                    FROM match_result mr
                    LEFT JOIN match_result_detail mrd
                      ON mrd.match_id = mr.match_id
                    LEFT JOIN (
                      SELECT match_id, COUNT(*) AS cnt
                      FROM match_result_player_stats
                      GROUP BY match_id
                    ) ps ON ps.match_id = mr.match_id
                    LEFT JOIN (
                      SELECT match_id, COUNT(*) AS cnt
                      FROM match_result_map_stats
                      GROUP BY match_id
                    ) ms ON ms.match_id = mr.match_id
                    LEFT JOIN (
                      SELECT
                        grouped.match_id,
                        SUM(grouped.cnt) AS cnt,
                        MAX(
                          CASE
                            WHEN grouped.cnt > 0 AND grouped.cnt < 5
                            THEN 1 ELSE 0
                          END
                        ) AS incomplete_players
                      FROM (
                        SELECT
                          match_id,
                          map_index,
                          team_side,
                          COUNT(*) AS cnt
                        FROM match_result_map_player_stats
                        GROUP BY match_id, map_index, team_side
                      ) grouped
                      GROUP BY grouped.match_id
                    ) mps ON mps.match_id = mr.match_id
                    WHERE mr.match_time IS NOT NULL
                      AND mr.match_time >= %s
                      AND mr.match_time <= NOW()
                      AND (
                        mr.status = 2
                        OR (
                          mr.score1 IS NOT NULL
                          AND mr.score2 IS NOT NULL
                          AND (mr.score1 <> 0 OR mr.score2 <> 0)
                        )
                        OR mr.match_time <= DATE_SUB(NOW(), INTERVAL 2 HOUR)
                      )
                      AND (
                        mrd.match_id IS NULL
                        OR mrd.analysis_success <> 1
                        OR mrd.data_success <> 1
                        OR mrd.fetched_at IS NULL
                        OR mrd.fetched_at < %s
                        OR COALESCE(ps.cnt, 0) = 0
                        OR COALESCE(ms.cnt, 0) = 0
                        OR COALESCE(mps.cnt, 0) = 0
                        OR COALESCE(mps.incomplete_players, 0) = 1
                        OR mr.score1 IS NULL
                        OR mr.score2 IS NULL
                        OR (mr.score1 = 0 AND mr.score2 = 0)
                        OR NULLIF(TRIM(COALESCE(mr.bout_details, '')), '') IS NULL
                        OR (
                          ({force_sql})
                          AND (
                            mrd.match_id IS NOT NULL
                          )
                        )
                      )
                    ORDER BY mr.match_time DESC
                    LIMIT %s
                    """,
                    (start_dt, stale_before, safe_limit),
                )
            else:
                cur.execute(
                    """
                    SELECT
                        mr.match_id, mr.match_time, mr.bo,
                        mr.team1_id, mr.team1, mr.team2_id, mr.team2,
                        mr.event_id, mr.event_name, mr.event_logo,
                        mr.event_start_time, mr.event_end_time,
                        mr.score1, mr.score2, mr.status,
                        mr.bout_count, mr.bout_details
                    FROM match_result mr
                    WHERE mr.match_time IS NOT NULL
                      AND mr.match_time >= %s
                      AND mr.match_time <= NOW()
                      AND (
                        mr.status = 2
                        OR (
                          mr.score1 IS NOT NULL
                          AND mr.score2 IS NOT NULL
                          AND (mr.score1 <> 0 OR mr.score2 <> 0)
                        )
                      )
                    ORDER BY mr.match_time DESC
                    LIMIT %s
                    """,
                    (start_dt, safe_limit),
                )
            return list(cur.fetchall() or [])


def build_detail_packages(
    candidates: List[Dict[str, Any]],
    *,
    workers: int,
    build_rows_for_match: Any,
) -> List[Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]]:
    if not candidates:
        return []
    packed: List[Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]] = []
    pool_size = max(1, min(workers, len(candidates)))
    with ThreadPoolExecutor(max_workers=pool_size) as executor:
        futures = {
            executor.submit(build_rows_for_match, row): str(row.get("match_id") or "").strip()
            for row in candidates
        }
        for future in as_completed(futures):
            try:
                item = future.result()
            except Exception as exc:
                print(
                    "[realtime-sync] detail build failed "
                    f"match_id={futures.get(future, '')}: {exc}"
                )
                item = None
            if not item:
                continue
            packed.append(item)
    return packed


def upsert_match_details_to_mysql(
    db_config: Dict[str, Any],
    packages: List[Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]],
    *,
    detail_columns: Sequence[str],
    player_columns: Sequence[str],
    map_columns: Sequence[str],
    map_player_columns: Sequence[str],
) -> Dict[str, int]:
    if not packages:
        return {
            "detail_upserted": 0,
            "player_rows": 0,
            "map_rows": 0,
            "map_player_rows": 0,
        }

    detail_rows: List[Dict[str, Any]] = []
    player_rows: List[Dict[str, Any]] = []
    map_rows: List[Dict[str, Any]] = []
    map_player_rows: List[Dict[str, Any]] = []
    derived_updates: List[Dict[str, Any]] = []
    player_replace_ids: Set[str] = set()
    map_replace_ids: Set[str] = set()
    map_player_replace_ids: Set[str] = set()

    for detail_row, players, maps, map_players in packages:
        match_id = str(detail_row.get("match_id") or "").strip()
        if not match_id:
            continue

        detail_rows.append(detail_row)
        derived_updates.append(
            {
                "match_id": match_id,
                "score1": detail_row.get("_derived_score1"),
                "score2": detail_row.get("_derived_score2"),
                "bout_count": detail_row.get("_derived_bout_count"),
                "bout_details": str(detail_row.get("_derived_bout_details") or "").strip(),
            }
        )

        if int(detail_row.get("analysis_success") or 0) == 1 and players:
            player_replace_ids.add(match_id)
            player_rows.extend(players)

        if maps:
            map_replace_ids.add(match_id)
            map_rows.extend(maps)

        if int(detail_row.get("data_success") or 0) == 1 and map_players:
            map_player_replace_ids.add(match_id)
            map_player_rows.extend(map_players)

    if not detail_rows:
        return {
            "detail_upserted": 0,
            "player_rows": 0,
            "map_rows": 0,
            "map_player_rows": 0,
        }

    with pymysql.connect(**db_config) as conn:
        with conn.cursor() as cur:
            col_sql = ", ".join(quote_ident(col) for col in detail_columns)
            placeholders = ", ".join(["%s"] * len(detail_columns))
            update_sql = ", ".join(
                [
                    f"{quote_ident(col)}=VALUES({quote_ident(col)})"
                    for col in detail_columns
                    if col != "match_id"
                ]
            )
            cur.executemany(
                f"""
                INSERT INTO match_result_detail ({col_sql})
                VALUES ({placeholders})
                ON DUPLICATE KEY UPDATE {update_sql}
                """,
                [
                    tuple(to_nullable(row.get(col)) for col in detail_columns)
                    for row in detail_rows
                ],
            )

            for ids, table_name in (
                (sorted(player_replace_ids), "match_result_player_stats"),
                (sorted(map_replace_ids), "match_result_map_stats"),
                (sorted(map_player_replace_ids), "match_result_map_player_stats"),
            ):
                if not ids:
                    continue
                for part in chunked(ids, 500):
                    in_sql = ", ".join(["%s"] * len(part))
                    cur.execute(
                        f"DELETE FROM {table_name} WHERE match_id IN ({in_sql})",
                        tuple(part),
                    )

            if player_rows:
                col_sql = ", ".join(quote_ident(col) for col in player_columns)
                placeholders = ", ".join(["%s"] * len(player_columns))
                cur.executemany(
                    f"""
                    INSERT INTO match_result_player_stats ({col_sql})
                    VALUES ({placeholders})
                    """,
                    [
                        tuple(to_nullable(row.get(col)) for col in player_columns)
                        for row in player_rows
                    ],
                )

            if map_rows:
                col_sql = ", ".join(quote_ident(col) for col in map_columns)
                placeholders = ", ".join(["%s"] * len(map_columns))
                cur.executemany(
                    f"""
                    INSERT INTO match_result_map_stats ({col_sql})
                    VALUES ({placeholders})
                    """,
                    [
                        tuple(to_nullable(row.get(col)) for col in map_columns)
                        for row in map_rows
                    ],
                )

            if map_player_rows:
                col_sql = ", ".join(quote_ident(col) for col in map_player_columns)
                placeholders = ", ".join(["%s"] * len(map_player_columns))
                cur.executemany(
                    f"""
                    INSERT INTO match_result_map_player_stats ({col_sql})
                    VALUES ({placeholders})
                    """,
                    [
                        tuple(to_nullable(row.get(col)) for col in map_player_columns)
                        for row in map_player_rows
                    ],
                )

            if derived_updates:
                result_updates = [
                    row
                    for row in derived_updates
                    if row.get("match_id")
                    and (
                        (row.get("score1") is not None and row.get("score2") is not None)
                        or row.get("bout_details")
                    )
                ]
                if result_updates:
                    cur.executemany(
                        """
                        UPDATE match_result
                        SET
                          score1 = CASE
                            WHEN %s IS NOT NULL AND %s IS NOT NULL
                              AND (score1 IS NULL OR score2 IS NULL OR (score1 = 0 AND score2 = 0))
                            THEN %s ELSE score1 END,
                          score2 = CASE
                            WHEN %s IS NOT NULL AND %s IS NOT NULL
                              AND (score1 IS NULL OR score2 IS NULL OR (score1 = 0 AND score2 = 0))
                            THEN %s ELSE score2 END,
                          bout_count = CASE
                            WHEN %s IS NOT NULL AND %s > 0 AND (bout_count IS NULL OR bout_count = 0)
                            THEN %s ELSE bout_count END,
                          bout_details = CASE
                            WHEN %s <> '' AND (bout_details IS NULL OR TRIM(bout_details) = '')
                            THEN %s ELSE bout_details END,
                          status = CASE
                            WHEN status IN (0, 1) AND (
                              (%s IS NOT NULL AND %s IS NOT NULL AND (%s <> 0 OR %s <> 0))
                              OR %s <> ''
                            )
                            THEN 2 ELSE status END
                        WHERE match_id = %s
                        """,
                        [
                            (
                                row.get("score1"),
                                row.get("score2"),
                                row.get("score1"),
                                row.get("score1"),
                                row.get("score2"),
                                row.get("score2"),
                                row.get("bout_count"),
                                row.get("bout_count"),
                                row.get("bout_count"),
                                row.get("bout_details"),
                                row.get("bout_details"),
                                row.get("score1"),
                                row.get("score2"),
                                row.get("score1"),
                                row.get("score2"),
                                row.get("bout_details"),
                                row.get("match_id"),
                            )
                            for row in result_updates
                        ],
                    )
                    cur.executemany(
                        """
                        UPDATE match_schedule ms
                        JOIN match_result mr ON mr.match_id = ms.match_id
                        SET
                          ms.score1 = CASE
                            WHEN mr.score1 IS NOT NULL AND (ms.score1 IS NULL OR (ms.score1 = 0 AND COALESCE(ms.score2, 0) = 0))
                            THEN mr.score1 ELSE ms.score1 END,
                          ms.score2 = CASE
                            WHEN mr.score2 IS NOT NULL AND (ms.score2 IS NULL OR (COALESCE(ms.score1, 0) = 0 AND ms.score2 = 0))
                            THEN mr.score2 ELSE ms.score2 END,
                          ms.status = CASE
                            WHEN mr.status = 2 THEN 2 ELSE ms.status END
                        WHERE ms.match_id = %s
                        """,
                        [(row.get("match_id"),) for row in result_updates],
                    )

    return {
        "detail_upserted": len(detail_rows),
        "player_rows": len(player_rows),
        "map_rows": len(map_rows),
        "map_player_rows": len(map_player_rows),
    }


def sync_detail_once(
    *,
    db_config: Dict[str, Any],
    start_dt: datetime,
    max_matches: int,
    stale_hours: int,
    workers: int,
    force_refresh_incomplete: bool,
    build_rows_for_match: Any,
    detail_columns: Sequence[str],
    player_columns: Sequence[str],
    map_columns: Sequence[str],
    map_player_columns: Sequence[str],
) -> Dict[str, int]:
    candidates = collect_detail_candidates(
        db_config,
        start_dt=start_dt,
        max_matches=max_matches,
        stale_hours=stale_hours,
        force_refresh_incomplete=force_refresh_incomplete,
    )
    packages = build_detail_packages(
        candidates,
        workers=workers,
        build_rows_for_match=build_rows_for_match,
    )
    stats = upsert_match_details_to_mysql(
        db_config,
        packages,
        detail_columns=detail_columns,
        player_columns=player_columns,
        map_columns=map_columns,
        map_player_columns=map_player_columns,
    )
    return {
        "candidates": len(candidates),
        "processed": len(packages),
        **stats,
    }


PLAYER_REFRESH_CSV_TABLES: Tuple[Tuple[str, str], ...] = (
    ("player_basic.csv", "player_basic"),
    ("player_teammates.csv", "player_teammates"),
    ("player_maps.csv", "player_maps"),
    ("player_rating_chart.csv", "player_rating_chart"),
    ("player_history_honor.csv", "player_history_honor"),
    ("player_milestones.csv", "player_milestones"),
    ("player_equipment.csv", "player_equipment"),
    ("player_mouse_config.csv", "player_mouse_config"),
    ("player_monitor_config.csv", "player_monitor_config"),
    ("player_stats_summary.csv", "player_stats_summary"),
    ("player_performance_metrics.csv", "player_performance_metrics"),
    ("player_recent_matches.csv", "player_recent_matches"),
)


def collect_recent_synced_player_ids(
    db_config: Dict[str, Any],
    *,
    since_dt: datetime,
    limit: int,
) -> List[str]:
    safe_limit = max(0, int(limit))
    if safe_limit <= 0:
        return []

    queries: List[str] = []
    with pymysql.connect(**db_config) as conn:
        with conn.cursor() as cur:
            if table_exists(cur, "match_result_player_stats"):
                queries.append(
                    """
                    SELECT player_id, MAX(fetched_at) AS latest_at
                    FROM match_result_player_stats
                    WHERE fetched_at >= %s
                      AND player_id IS NOT NULL
                      AND player_id <> ''
                    GROUP BY player_id
                    """
                )
            if table_exists(cur, "match_result_map_player_stats"):
                queries.append(
                    """
                    SELECT player_id, MAX(fetched_at) AS latest_at
                    FROM match_result_map_player_stats
                    WHERE fetched_at >= %s
                      AND player_id IS NOT NULL
                      AND player_id <> ''
                    GROUP BY player_id
                    """
                )
            if not queries:
                return []
            sql = (
                "SELECT player_id, MAX(latest_at) AS latest_at FROM ("
                + "\nUNION ALL\n".join(queries)
                + ") q GROUP BY player_id ORDER BY latest_at DESC LIMIT %s"
            )
            params: List[Any] = [since_dt for _ in queries]
            params.append(safe_limit)
            cur.execute(sql, tuple(params))
            rows = list(cur.fetchall() or [])

    player_ids: List[str] = []
    for row in rows:
        player_id = str(row.get("player_id") or "").strip()
        if player_id:
            player_ids.append(player_id)
    return player_ids


def collect_window_player_ids(
    db_config: Dict[str, Any],
    *,
    start_dt: datetime,
    limit: int,
) -> List[str]:
    safe_limit = max(0, int(limit))
    if safe_limit <= 0:
        return []

    queries: List[str] = []
    with pymysql.connect(**db_config) as conn:
        with conn.cursor() as cur:
            if table_exists(cur, "match_result_player_stats"):
                queries.append(
                    """
                    SELECT
                      mrps.player_id,
                      MAX(COALESCE(mr.match_time, mrps.fetched_at)) AS latest_at
                    FROM match_result_player_stats mrps
                    LEFT JOIN match_result mr ON mr.match_id = mrps.match_id
                    WHERE COALESCE(mr.match_time, mrps.fetched_at) >= %s
                      AND mrps.player_id IS NOT NULL
                      AND mrps.player_id <> ''
                    GROUP BY mrps.player_id
                    """
                )
            if table_exists(cur, "match_result_map_player_stats"):
                queries.append(
                    """
                    SELECT
                      mrmp.player_id,
                      MAX(COALESCE(mr.match_time, mrmp.fetched_at)) AS latest_at
                    FROM match_result_map_player_stats mrmp
                    LEFT JOIN match_result mr ON mr.match_id = mrmp.match_id
                    WHERE COALESCE(mr.match_time, mrmp.fetched_at) >= %s
                      AND mrmp.player_id IS NOT NULL
                      AND mrmp.player_id <> ''
                    GROUP BY mrmp.player_id
                    """
                )
            if not queries:
                return []
            sql = (
                "SELECT player_id, MAX(latest_at) AS latest_at FROM ("
                + "\nUNION ALL\n".join(queries)
                + ") q GROUP BY player_id ORDER BY latest_at DESC LIMIT %s"
            )
            params: List[Any] = [start_dt for _ in queries]
            params.append(safe_limit)
            cur.execute(sql, tuple(params))
            rows = list(cur.fetchall() or [])

    player_ids: List[str] = []
    for row in rows:
        player_id = str(row.get("player_id") or "").strip()
        if player_id:
            player_ids.append(player_id)
    return player_ids


def prioritize_missing_player_profiles(
    db_config: Dict[str, Any],
    player_ids: Sequence[str],
) -> List[str]:
    clean_ids = [str(pid or "").strip() for pid in player_ids if str(pid or "").strip()]
    if not clean_ids:
        return []

    seen: Set[str] = set()
    ordered_ids: List[str] = []
    for player_id in clean_ids:
        if player_id in seen:
            continue
        seen.add(player_id)
        ordered_ids.append(player_id)

    missing_basic: Set[str] = set()
    missing_roster: Set[str] = set()
    with pymysql.connect(**db_config) as conn:
        with conn.cursor() as cur:
            existing_basic: Set[str] = set()
            existing_roster: Set[str] = set()
            for batch in chunked(ordered_ids, 500):
                placeholders = ", ".join(["%s"] * len(batch))
                if table_exists(cur, "player_basic"):
                    cur.execute(
                        f"""
                        SELECT DISTINCT player_id
                        FROM player_basic
                        WHERE player_id IN ({placeholders})
                        """,
                        tuple(batch),
                    )
                    existing_basic.update(
                        str(row.get("player_id") or "").strip()
                        for row in cur.fetchall()
                        if str(row.get("player_id") or "").strip()
                    )
                if table_exists(cur, "team_player_relation"):
                    cur.execute(
                        f"""
                        SELECT DISTINCT player_id
                        FROM team_player_relation
                        WHERE player_id IN ({placeholders})
                        """,
                        tuple(batch),
                    )
                    existing_roster.update(
                        str(row.get("player_id") or "").strip()
                        for row in cur.fetchall()
                        if str(row.get("player_id") or "").strip()
                    )
            missing_basic = set(ordered_ids) - existing_basic
            missing_roster = set(ordered_ids) - existing_roster

    order_map = {player_id: idx for idx, player_id in enumerate(ordered_ids)}
    return sorted(
        ordered_ids,
        key=lambda pid: (
            0 if pid in missing_basic else 1,
            0 if pid in missing_roster else 1,
            order_map.get(pid, 0),
        ),
    )


def merge_player_id_sources(*sources: Sequence[str]) -> List[str]:
    merged: List[str] = []
    seen: Set[str] = set()
    for source in sources:
        for player_id in source:
            clean_id = str(player_id or "").strip()
            if not clean_id or clean_id in seen:
                continue
            seen.add(clean_id)
            merged.append(clean_id)
    return merged


def filter_player_refresh_ids(
    player_ids: Sequence[str],
    *,
    ttl_hours: int,
    max_players: int,
) -> List[str]:
    ttl_seconds = max(0, int(ttl_hours)) * 3600
    safe_limit = max(0, int(max_players))
    now_ts = time.time()
    selected: List[str] = []
    seen: Set[str] = set()
    for player_id in player_ids:
        clean_id = str(player_id or "").strip()
        if not clean_id or clean_id in seen:
            continue
        seen.add(clean_id)
        last_ts = PLAYER_REFRESH_SEEN.get(clean_id)
        if ttl_seconds and last_ts and now_ts - last_ts < ttl_seconds:
            continue
        selected.append(clean_id)
        if safe_limit and len(selected) >= safe_limit:
            break
    return selected


def upsert_team_player_relation_csv(
    db_config: Dict[str, Any],
    *,
    csv_path: Path,
) -> Dict[str, int]:
    if not csv_path.exists():
        return {"rows": 0, "players": 0}

    with csv_path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = [str(col or "").strip() for col in (reader.fieldnames or [])]
        headers = [col for col in headers if col]
        rows = [{col: row.get(col) for col in headers} for row in reader]

    if not headers or not rows:
        return {"rows": 0, "players": 0}

    with pymysql.connect(**db_config) as conn:
        with conn.cursor() as cur:
            ensure_longtext_table_for_csv(cur, "team_player_relation", headers)
            cur.execute("SHOW COLUMNS FROM `team_player_relation`")
            schema_rows = cur.fetchall() or []
            table_types: Dict[str, str] = {}
            for row in schema_rows:
                if isinstance(row, dict):
                    col_name = str(row.get("Field") or "").strip()
                    col_type = str(row.get("Type") or "")
                else:
                    col_name = str(row[0] or "").strip()
                    col_type = str(row[1] or "")
                if col_name:
                    table_types[col_name] = col_type
            insert_headers = [col for col in headers if col in table_types]
            if not insert_headers:
                return {"rows": 0, "players": 0}
            cur.execute("TRUNCATE TABLE `team_player_relation`")
            col_sql = ", ".join(quote_ident(col) for col in insert_headers)
            placeholders = ", ".join(["%s"] * len(insert_headers))
            cur.executemany(
                f"INSERT INTO `team_player_relation` ({col_sql}) VALUES ({placeholders})",
                [
                    tuple(
                        normalize_csv_value_for_column(row.get(col), table_types.get(col, ""))
                        for col in insert_headers
                    )
                    for row in rows
                ],
            )

    player_ids = {
        str(row.get("player_id") or "").strip()
        for row in rows
        if str(row.get("player_id") or "").strip()
    }
    return {"rows": len(rows), "players": len(player_ids)}


def maybe_refresh_team_rosters(
    db_config: Dict[str, Any],
    *,
    interval_hours: int,
    max_pages: int,
    workers: int,
) -> Dict[str, int]:
    global TEAM_ROSTER_LAST_REFRESH_TS
    now_ts = time.time()
    interval_seconds = max(0, int(interval_hours)) * 3600
    if (
        TEAM_ROSTER_LAST_REFRESH_TS
        and interval_seconds
        and now_ts - TEAM_ROSTER_LAST_REFRESH_TS < interval_seconds
    ):
        return {"skipped": 1, "rows": 0, "players": 0}

    script_dir = Path(__file__).resolve().parent
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))

    import team_player_relation as roster_scraper  # pylint: disable=import-outside-toplevel

    output_path = script_dir / "cs_data" / "team_player_relation.csv"
    roster_scraper.crawl_team_player_relation(
        max_pages=max(1, int(max_pages)),
        output_file=output_path,
        max_workers=max(1, int(workers)),
    )
    stats = upsert_team_player_relation_csv(db_config, csv_path=output_path)
    TEAM_ROSTER_LAST_REFRESH_TS = time.time()
    return {"skipped": 0, **stats}


def normalize_csv_value_for_column(value: Any, column_type: str) -> Any:
    value = to_nullable(value)
    if value is None:
        return None
    lower_type = str(column_type or "").lower()
    if lower_type.startswith("datetime") or lower_type.startswith("timestamp"):
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M:%S")
        text = str(value).strip()
        if text.isdigit():
            return datetime.fromtimestamp(int(text)).strftime("%Y-%m-%d %H:%M:%S")
    return value


def upsert_full_csv_table(
    db_config: Dict[str, Any],
    *,
    table_name: str,
    csv_path: Path,
) -> int:
    if not csv_path.exists():
        return 0

    with csv_path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = [str(col or "").strip() for col in (reader.fieldnames or [])]
        headers = [col for col in headers if col]
        rows = [{col: row.get(col) for col in headers} for row in reader]

    if not headers:
        return 0

    with pymysql.connect(**db_config) as conn:
        with conn.cursor() as cur:
            ensure_longtext_table_for_csv(cur, table_name, headers)
            cur.execute(f"SHOW COLUMNS FROM {quote_ident(table_name)}")
            schema_rows = cur.fetchall() or []
            table_types: Dict[str, str] = {}
            for row in schema_rows:
                if isinstance(row, dict):
                    col_name = str(row.get("Field") or "").strip()
                    col_type = str(row.get("Type") or "")
                else:
                    col_name = str(row[0] or "").strip()
                    col_type = str(row[1] or "")
                if col_name:
                    table_types[col_name] = col_type

            insert_cols = [col for col in headers if col in table_types]
            if not insert_cols:
                return 0

            cur.execute(f"TRUNCATE TABLE {quote_ident(table_name)}")
            if rows:
                col_sql = ", ".join(quote_ident(col) for col in insert_cols)
                placeholders = ", ".join(["%s"] * len(insert_cols))
                cur.executemany(
                    f"INSERT INTO {quote_ident(table_name)} ({col_sql}) VALUES ({placeholders})",
                    [
                        tuple(
                            normalize_csv_value_for_column(row.get(col), table_types.get(col, ""))
                            for col in insert_cols
                        )
                        for row in rows
                    ],
                )
    return len(rows)


def maybe_refresh_team_snapshots(
    db_config: Dict[str, Any],
    *,
    interval_hours: int,
    max_pages: int,
    workers: int,
) -> Dict[str, int]:
    global TEAM_SNAPSHOT_LAST_REFRESH_TS
    now_ts = time.time()
    interval_seconds = max(0, int(interval_hours)) * 3600
    if (
        TEAM_SNAPSHOT_LAST_REFRESH_TS
        and interval_seconds
        and now_ts - TEAM_SNAPSHOT_LAST_REFRESH_TS < interval_seconds
    ):
        return {"skipped": 1, "rank_rows": 0, "stat_rows": 0}

    script_dir = Path(__file__).resolve().parent
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))

    import team_rank_snapshot as rank_scraper  # pylint: disable=import-outside-toplevel
    import team_stat_snapshot as stat_scraper  # pylint: disable=import-outside-toplevel

    data_dir = script_dir / "cs_data"
    rank_path = data_dir / "team_rank_snapshot.csv"
    stat_path = data_dir / "team_stat_snapshot.csv"
    rank_rows = 0
    stat_rows = 0
    try:
        rank_scraper.crawl_team_rank_snapshot(
            max_pages=max(1, int(max_pages)),
            output_file=str(rank_path),
            max_workers=max(1, int(workers)),
        )
        rank_rows = upsert_full_csv_table(db_config, table_name="team_rank_snapshot", csv_path=rank_path)
    except Exception as exc:
        print(f"[realtime-sync] team_rank_snapshot refresh failed: {exc}")

    try:
        stat_scraper.crawl_team_stat_snapshot(
            max_pages=max(1, int(max_pages)),
            output_file=str(stat_path),
            max_workers=max(1, int(workers)),
        )
        stat_rows = upsert_full_csv_table(db_config, table_name="team_stat_snapshot", csv_path=stat_path)
    except Exception as exc:
        print(f"[realtime-sync] team_stat_snapshot refresh failed: {exc}")

    TEAM_SNAPSHOT_LAST_REFRESH_TS = time.time()
    return {"skipped": 0, "rank_rows": rank_rows, "stat_rows": stat_rows}


def collect_roster_player_ids(
    db_config: Dict[str, Any],
    *,
    limit: int,
) -> List[str]:
    safe_limit = max(0, int(limit))
    if safe_limit <= 0:
        return []
    with pymysql.connect(**db_config) as conn:
        with conn.cursor() as cur:
            if not table_exists(cur, "team_player_relation"):
                return []
            cur.execute(
                """
                SELECT player_id, MAX(crawl_time) AS latest_at
                FROM team_player_relation
                WHERE player_id IS NOT NULL
                  AND player_id <> ''
                GROUP BY player_id
                ORDER BY latest_at DESC
                LIMIT %s
                """,
                (safe_limit,),
            )
            rows = list(cur.fetchall() or [])
    return [str(row.get("player_id") or "").strip() for row in rows if str(row.get("player_id") or "").strip()]


def ensure_longtext_table_for_csv(
    cur: pymysql.cursors.DictCursor,
    table_name: str,
    headers: Sequence[str],
) -> None:
    safe_table = quote_ident(table_name)
    cur.execute("SHOW TABLES LIKE %s", (table_name,))
    exists = cur.fetchone() is not None
    if not exists:
        col_defs = ",\n".join(f"  {quote_ident(col)} LONGTEXT NULL" for col in headers)
        index_defs: List[str] = []
        if "player_id" in headers:
            index_defs.append("  KEY `idx_player_id` (`player_id`(100))")
        if "team_id" in headers:
            index_defs.append("  KEY `idx_team_id` (`team_id`(100))")
        if "match_id" in headers:
            index_defs.append("  KEY `idx_match_id` (`match_id`(100))")
        idx_sql = ",\n" + ",\n".join(index_defs) if index_defs else ""
        cur.execute(
            f"""
            CREATE TABLE {safe_table} (
{col_defs}{idx_sql}
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
        return

    cur.execute(f"SHOW COLUMNS FROM {safe_table}")
    existing_columns: Set[str] = set()
    for row in cur.fetchall():
        if isinstance(row, dict):
            col_name = str(row.get("Field") or "").strip()
        elif isinstance(row, (list, tuple)) and row:
            col_name = str(row[0] or "").strip()
        else:
            col_name = ""
        if col_name:
            existing_columns.add(col_name)
    for col in headers:
        if col not in existing_columns:
            cur.execute(f"ALTER TABLE {safe_table} ADD COLUMN {quote_ident(col)} LONGTEXT NULL")


def upsert_player_csv_tables(
    db_config: Dict[str, Any],
    *,
    csv_dir: Path,
    player_ids: Sequence[str],
) -> Dict[str, int]:
    player_id_set = {str(pid or "").strip() for pid in player_ids if str(pid or "").strip()}
    if not player_id_set:
        return {}

    results: Dict[str, int] = {}
    with pymysql.connect(**db_config) as conn:
        with conn.cursor() as cur:
            for filename, table_name in PLAYER_REFRESH_CSV_TABLES:
                csv_path = csv_dir / filename
                if not csv_path.exists():
                    continue
                with csv_path.open("r", newline="", encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)
                    headers = [str(col or "").strip() for col in (reader.fieldnames or [])]
                    headers = [col for col in headers if col]
                    if not headers or "player_id" not in headers:
                        continue
                    rows = [
                        {col: row.get(col) for col in headers}
                        for row in reader
                        if str(row.get("player_id") or "").strip() in player_id_set
                    ]
                if not rows:
                    continue

                ensure_longtext_table_for_csv(cur, table_name, headers)
                placeholders = ", ".join(["%s"] * len(player_id_set))
                cur.execute(
                    f"DELETE FROM {quote_ident(table_name)} WHERE `player_id` IN ({placeholders})",
                    tuple(sorted(player_id_set)),
                )
                col_sql = ", ".join(quote_ident(col) for col in headers)
                row_placeholders = ", ".join(["%s"] * len(headers))
                cur.executemany(
                    f"INSERT INTO {quote_ident(table_name)} ({col_sql}) VALUES ({row_placeholders})",
                    [
                        tuple(to_nullable(row.get(col)) for col in headers)
                        for row in rows
                    ],
                )
                results[table_name] = len(rows)
    return results


def refresh_player_profiles_for_ids(
    db_config: Dict[str, Any],
    *,
    player_ids: Sequence[str],
    workers: int,
) -> Dict[str, Any]:
    clean_ids = [str(pid or "").strip() for pid in player_ids if str(pid or "").strip()]
    if not clean_ids:
        return {"requested": 0, "written": 0, "tables": {}}

    script_dir = Path(__file__).resolve().parent
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))

    import scrape_player as player_scraper  # pylint: disable=import-outside-toplevel

    player_scraper.MAX_WORKERS = max(1, int(workers))
    player_scraper.CSV_MERGE_CACHE.clear()
    player_scraper.CSV_FIXED_CACHE.clear()
    player_scraper.EQUIPMENT_ROWS_CACHE = None

    payloads_by_player = player_scraper.fetch_players_concurrently(
        clean_ids,
        None,
        None,
        None,
    )

    written_ids: List[str] = []
    for player_id in clean_ids:
        payload = payloads_by_player.get(player_id) or {}
        if not any(
            [
                payload.get("basic_data"),
                payload.get("stats_data"),
                payload.get("match_items"),
            ]
        ):
            continue
        player_scraper.build_basic_csv(player_id, payload.get("basic_data", {}))
        player_scraper.build_stats_csv(player_id, payload.get("stats_data", {}))
        player_scraper.build_matches_csv(player_id, payload.get("match_items", {}))
        written_ids.append(player_id)

    player_scraper.flush_csv_buffers()
    table_counts = upsert_player_csv_tables(
        db_config,
        csv_dir=player_scraper.BASE_DIR,
        player_ids=written_ids,
    )

    now_ts = time.time()
    for player_id in written_ids:
        PLAYER_REFRESH_SEEN[player_id] = now_ts

    return {
        "requested": len(clean_ids),
        "written": len(written_ids),
        "tables": table_counts,
    }


def backfill_missing_player_basic_team_from_recent_matches(
    db_config: Dict[str, Any],
    *,
    player_ids: Sequence[str],
    start_dt: datetime,
) -> int:
    clean_ids = [str(pid or "").strip() for pid in player_ids if str(pid or "").strip()]
    if not clean_ids:
        return 0

    updated = 0
    with pymysql.connect(**db_config) as conn:
        with conn.cursor() as cur:
            if not table_exists(cur, "player_basic") or not table_exists(cur, "match_result_player_stats"):
                return 0

            cur.execute("SHOW COLUMNS FROM `player_basic`")
            basic_cols = {
                str(row.get("Field") or "").strip()
                for row in cur.fetchall()
                if str(row.get("Field") or "").strip()
            }
            if "team_id" not in basic_cols or "team_name" not in basic_cols:
                return 0

            for batch in chunked(clean_ids, 500):
                placeholders = ", ".join(["%s"] * len(batch))
                cur.execute(
                    f"""
                    SELECT player_id, team_id, team_name
                    FROM player_basic
                    WHERE player_id IN ({placeholders})
                      AND (
                        team_id IS NULL OR team_id = ''
                        OR team_name IS NULL OR team_name = ''
                      )
                    """,
                    tuple(batch),
                )
                needs_team = {
                    str(row.get("player_id") or "").strip()
                    for row in cur.fetchall()
                    if str(row.get("player_id") or "").strip()
                }
                if not needs_team:
                    continue

                placeholders = ", ".join(["%s"] * len(needs_team))
                cur.execute(
                    f"""
                    SELECT
                      ps.player_id,
                      ps.team_id,
                      ps.team_name,
                      MAX(COALESCE(mr.match_time, ps.fetched_at)) AS latest_at
                    FROM match_result_player_stats ps
                    LEFT JOIN match_result mr ON mr.match_id = ps.match_id
                    WHERE ps.player_id IN ({placeholders})
                      AND COALESCE(mr.match_time, ps.fetched_at) >= %s
                      AND ps.team_id IS NOT NULL
                      AND ps.team_id <> ''
                      AND ps.team_name IS NOT NULL
                      AND ps.team_name <> ''
                    GROUP BY ps.player_id, ps.team_id, ps.team_name
                    ORDER BY latest_at DESC
                    """,
                    (*sorted(needs_team), start_dt),
                )
                latest_by_player: Dict[str, Dict[str, Any]] = {}
                for row in cur.fetchall():
                    player_id = str(row.get("player_id") or "").strip()
                    if player_id and player_id not in latest_by_player:
                        latest_by_player[player_id] = row

                for player_id, row in latest_by_player.items():
                    team_id = str(row.get("team_id") or "").strip()
                    team_name = str(row.get("team_name") or "").strip()
                    if not team_id or not team_name:
                        continue

                    set_parts = ["team_id = %s", "team_name = %s"]
                    params: List[Any] = [team_id, team_name]
                    if "team_logo" in basic_cols and table_exists(cur, "team_rank_snapshot"):
                        cur.execute(
                            """
                            SELECT team_logo
                            FROM team_rank_snapshot
                            WHERE team_id = %s AND team_logo IS NOT NULL AND team_logo <> ''
                            ORDER BY crawl_time DESC
                            LIMIT 1
                            """,
                            (team_id,),
                        )
                        logo_row = cur.fetchone()
                        team_logo = str((logo_row or {}).get("team_logo") or "").strip()
                        if team_logo:
                            set_parts.append("team_logo = %s")
                            params.append(team_logo)

                    params.append(player_id)
                    cur.execute(
                        f"""
                        UPDATE player_basic
                        SET {", ".join(set_parts)}
                        WHERE player_id = %s
                          AND (
                            team_id IS NULL OR team_id = ''
                            OR team_name IS NULL OR team_name = ''
                          )
                        """,
                        tuple(params),
                    )
                    updated += int(cur.rowcount or 0)

    return updated


def reconcile_stale_live_status(
    db_config: Dict[str, Any],
    *,
    stale_hours: int,
) -> Dict[str, int]:
    threshold_hours = max(1, stale_hours)
    out = {
        "schedule_fixed": 0,
        "result_fixed": 0,
    }
    with pymysql.connect(**db_config) as conn:
        with conn.cursor() as cur:
            if table_exists(cur, "match_result"):
                cur.execute(
                    """
                    UPDATE match_result mr
                    SET mr.status = 2
                    WHERE mr.status = 1
                      AND mr.match_time IS NOT NULL
                      AND mr.match_time <= DATE_SUB(NOW(), INTERVAL %s HOUR)
                      AND (
                        (mr.score1 IS NOT NULL AND mr.score2 IS NOT NULL AND (mr.score1 <> 0 OR mr.score2 <> 0))
                        OR NULLIF(TRIM(COALESCE(mr.bout_details, '')), '') IS NOT NULL
                      )
                    """,
                    (threshold_hours,),
                )
                out["result_fixed"] = int(cur.rowcount or 0)

            if table_exists(cur, "match_schedule"):
                cur.execute(
                    """
                    UPDATE match_schedule ms
                    LEFT JOIN match_result mr
                      ON mr.match_id = ms.match_id
                    SET
                      ms.status = CASE
                        WHEN mr.status = 2 THEN 2
                        ELSE ms.status
                      END,
                      ms.score1 = CASE
                        WHEN mr.status = 2 THEN COALESCE(mr.score1, ms.score1)
                        ELSE ms.score1
                      END,
                      ms.score2 = CASE
                        WHEN mr.status = 2 THEN COALESCE(mr.score2, ms.score2)
                        ELSE ms.score2
                      END
                    WHERE ms.status = 1
                      AND ms.match_time IS NOT NULL
                      AND ms.match_time <= DATE_SUB(NOW(), INTERVAL %s HOUR)
                      AND mr.match_id IS NOT NULL
                    """,
                    (threshold_hours,),
                )
                out["schedule_fixed"] = int(cur.rowcount or 0)
    return out


def main() -> int:
    args = build_args()
    configure_env(args)

    script_dir = Path(__file__).resolve().parent
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))

    from cs_api_server import DB_CONFIG, LIVE_SYNC_STATE, run_live_sync_once  # pylint: disable=import-outside-toplevel
    from match_result_detail import (  # pylint: disable=import-outside-toplevel
        DETAIL_COLUMNS,
        MAP_COLUMNS,
        MAP_PLAYER_COLUMNS,
        PLAYER_COLUMNS,
        build_rows_for_match,
    )

    interval = max(5, args.interval_seconds)
    cycle = 0
    start_desc = args.start_date if args.start_date else f"now-{max(1, args.lookback_days)}d"
    print(
        "[realtime-sync] start "
        f"version={SCRIPT_VERSION} "
        f"start={start_desc} future_days={args.future_days} "
        f"schedule_pages={args.schedule_pages} result_pages={args.result_pages} "
        f"detail_sync={not args.disable_detail_sync} "
        f"detail_max_matches={max(1, args.detail_max_matches)} "
        f"detail_workers={max(1, args.detail_workers)} "
        f"detail_stale_hours={max(1, args.detail_stale_hours)} "
        f"detail_force_refresh_incomplete={bool(args.detail_force_refresh_incomplete)} "
        f"player_refresh={not bool(args.disable_player_refresh)} "
        f"player_refresh_max_per_cycle={max(0, args.player_refresh_max_per_cycle)} "
        f"player_refresh_workers={max(1, args.player_refresh_workers)} "
        f"player_refresh_ttl_hours={max(0, args.player_refresh_ttl_hours)} "
        f"player_refresh_window_scan={not bool(args.disable_player_refresh_window_scan)} "
        f"team_roster_refresh={not bool(args.disable_team_roster_refresh)} "
        f"team_roster_refresh_interval_hours={max(0, args.team_roster_refresh_interval_hours)} "
        f"team_snapshot_refresh={not bool(args.disable_team_snapshot_refresh)} "
        f"team_snapshot_refresh_interval_hours={max(0, args.team_snapshot_refresh_interval_hours)} "
        f"stale_live_hours={max(1, args.stale_live_hours)} "
        f"interval={interval}s once={args.once}"
    )

    try:
        while True:
            cycle += 1
            cycle_start = datetime.now()
            ts = cycle_start.strftime("%Y-%m-%d %H:%M:%S")
            try:
                counts = run_live_sync_once()
                stale_fix_counts = reconcile_stale_live_status(
                    DB_CONFIG,
                    stale_hours=max(1, args.stale_live_hours),
                )
                log_line = (
                    f"[realtime-sync] cycle={cycle} at={ts} "
                    f"schedule_upserted={counts.get('schedule', 0)} "
                    f"result_upserted={counts.get('result', 0)} "
                    f"schedule_fixed={stale_fix_counts.get('schedule_fixed', 0)} "
                    f"result_fixed={stale_fix_counts.get('result_fixed', 0)} "
                    f"last_error={LIVE_SYNC_STATE.get('lastError', '')}"
                )

                if not args.disable_detail_sync:
                    detail_since = cycle_start
                    snapshot_counts: Dict[str, int] = {"skipped": 1, "rank_rows": 0, "stat_rows": 0}
                    if not args.disable_team_snapshot_refresh:
                        snapshot_counts = maybe_refresh_team_snapshots(
                            DB_CONFIG,
                            interval_hours=max(0, args.team_snapshot_refresh_interval_hours),
                            max_pages=max(1, args.team_snapshot_refresh_pages),
                            workers=max(1, args.team_snapshot_refresh_workers),
                        )
                        log_line += (
                            f" snapshot_refresh_skipped={snapshot_counts.get('skipped', 0)}"
                            f" rank_rows={snapshot_counts.get('rank_rows', 0)}"
                            f" stat_rows={snapshot_counts.get('stat_rows', 0)}"
                        )
                    roster_counts: Dict[str, int] = {"skipped": 1, "rows": 0, "players": 0}
                    if not args.disable_team_roster_refresh:
                        roster_counts = maybe_refresh_team_rosters(
                            DB_CONFIG,
                            interval_hours=max(0, args.team_roster_refresh_interval_hours),
                            max_pages=max(1, args.team_roster_refresh_pages),
                            workers=max(1, args.team_roster_refresh_workers),
                        )
                        log_line += (
                            f" roster_refresh_skipped={roster_counts.get('skipped', 0)}"
                            f" roster_rows={roster_counts.get('rows', 0)}"
                            f" roster_players={roster_counts.get('players', 0)}"
                        )
                    detail_counts = sync_detail_once(
                        db_config=DB_CONFIG,
                        start_dt=parse_start_dt(args),
                        max_matches=max(1, args.detail_max_matches),
                        stale_hours=max(1, args.detail_stale_hours),
                        workers=max(1, args.detail_workers),
                        force_refresh_incomplete=bool(args.detail_force_refresh_incomplete),
                        build_rows_for_match=build_rows_for_match,
                        detail_columns=DETAIL_COLUMNS,
                        player_columns=PLAYER_COLUMNS,
                        map_columns=MAP_COLUMNS,
                        map_player_columns=MAP_PLAYER_COLUMNS,
                    )
                    log_line += (
                        f" detail_candidates={detail_counts.get('candidates', 0)}"
                        f" detail_processed={detail_counts.get('processed', 0)}"
                        f" detail_upserted={detail_counts.get('detail_upserted', 0)}"
                        f" player_rows={detail_counts.get('player_rows', 0)}"
                        f" map_rows={detail_counts.get('map_rows', 0)}"
                        f" map_player_rows={detail_counts.get('map_player_rows', 0)}"
                    )
                    if not args.disable_player_refresh:
                        recent_player_ids = collect_recent_synced_player_ids(
                            DB_CONFIG,
                            since_dt=detail_since,
                            limit=max(0, args.player_refresh_max_per_cycle * 3),
                        )
                        window_player_ids: List[str] = []
                        if not args.disable_player_refresh_window_scan:
                            window_player_ids = collect_window_player_ids(
                                DB_CONFIG,
                                start_dt=parse_start_dt(args),
                                limit=max(0, args.player_refresh_max_per_cycle * 6),
                            )
                        roster_player_ids: List[str] = []
                        if roster_counts.get("skipped", 1) == 0:
                            roster_player_ids = collect_roster_player_ids(
                                DB_CONFIG,
                                limit=max(0, args.player_refresh_max_per_cycle * 3),
                            )
                        player_id_candidates = merge_player_id_sources(
                            recent_player_ids,
                            window_player_ids,
                            roster_player_ids,
                        )
                        player_id_candidates = prioritize_missing_player_profiles(
                            DB_CONFIG,
                            player_id_candidates,
                        )
                        refresh_ids = filter_player_refresh_ids(
                            player_id_candidates,
                            ttl_hours=max(0, args.player_refresh_ttl_hours),
                            max_players=max(0, args.player_refresh_max_per_cycle),
                        )
                        player_refresh_counts = refresh_player_profiles_for_ids(
                            DB_CONFIG,
                            player_ids=refresh_ids,
                            workers=max(1, args.player_refresh_workers),
                        )
                        player_team_backfilled = backfill_missing_player_basic_team_from_recent_matches(
                            DB_CONFIG,
                            player_ids=refresh_ids,
                            start_dt=parse_start_dt(args),
                        )
                        table_rows = sum(
                            int(v or 0)
                            for v in (player_refresh_counts.get("tables") or {}).values()
                        )
                        log_line += (
                            f" player_refresh_seen={len(recent_player_ids)}"
                            f" player_refresh_window_seen={len(window_player_ids)}"
                            f" player_refresh_roster_seen={len(roster_player_ids)}"
                            f" player_refresh_requested={player_refresh_counts.get('requested', 0)}"
                            f" player_refresh_written={player_refresh_counts.get('written', 0)}"
                            f" player_team_backfilled={player_team_backfilled}"
                            f" player_refresh_table_rows={table_rows}"
                        )
                print(log_line)
            except Exception as exc:  # pragma: no cover
                print(f"[realtime-sync] cycle={cycle} at={ts} failed: {exc}")

            if args.once:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        print("[realtime-sync] interrupted by user, exiting")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
