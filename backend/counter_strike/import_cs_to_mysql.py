import os
from pathlib import Path
from typing import Dict, List

import pandas as pd
import pymysql


DB_CONFIG = {
    "host": os.getenv("CS_DB_HOST", "localhost"),
    "port": int(os.getenv("CS_DB_PORT", "3306")),
    "user": os.getenv("CS_DB_USER", "root"),
    "password": os.getenv("CS_DB_PASSWORD", ""),
    "database": os.getenv("CS_DB_NAME", "cs_esports"),
    "charset": "utf8mb4",
    "autocommit": True,
}

DATABASE_NAME = os.getenv("CS_DB_NAME", "cs_esports")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CS_DATA_DIR = Path(BASE_DIR) / "cs_data"
INVALID_TEAM_ID_TOKENS = {"", "none", "null", "nan", "na", "n/a", "-"}

CSV_FILES = {
    "team_basic": str(CS_DATA_DIR / "team_basic.csv"),
    "team_player_relation": str(CS_DATA_DIR / "team_player_relation.csv"),
    "team_rank_snapshot": str(CS_DATA_DIR / "team_rank_snapshot.csv"),
    "team_stat_snapshot": str(CS_DATA_DIR / "team_stat_snapshot.csv"),
    "event_basic": str(CS_DATA_DIR / "event_basic_5eplay.csv"),
    "cs2_matches": str(CS_DATA_DIR / "cs2_matches_5eplay.csv"),
    "cs2_results": str(CS_DATA_DIR / "cs2_results_5eplay.csv"),
    "match_result_detail": str(CS_DATA_DIR / "cs2_result_details_5eplay.csv"),
    "match_result_player_stats": str(CS_DATA_DIR / "cs2_result_player_stats_5eplay.csv"),
    "match_result_map_stats": str(CS_DATA_DIR / "cs2_result_map_stats_5eplay.csv"),
    "match_result_map_player_stats": str(CS_DATA_DIR / "cs2_result_map_player_stats_5eplay.csv"),
}


TABLE_SQL: Dict[str, str] = {
    "team_basic": """
        CREATE TABLE IF NOT EXISTS team_basic (
            team_id VARCHAR(50) PRIMARY KEY,
            team_name VARCHAR(100),
            team_logo VARCHAR(255),
            country_logo VARCHAR(255),
            region_name VARCHAR(100),
            crawl_time DATETIME
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    "team_player_relation": """
        CREATE TABLE IF NOT EXISTS team_player_relation (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            team_id VARCHAR(50) NOT NULL,
            team_name VARCHAR(100),
            player_id VARCHAR(50),
            player_name VARCHAR(100),
            player_portrait VARCHAR(255),
            player_country_logo VARCHAR(255),
            crawl_time DATETIME,
            KEY idx_team_id (team_id),
            KEY idx_player_id (player_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    "team_rank_snapshot": """
        CREATE TABLE IF NOT EXISTS team_rank_snapshot (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            team_id VARCHAR(50) NOT NULL,
            team_name VARCHAR(100),
            team_logo VARCHAR(255),
            country_logo VARCHAR(255),
            global_rank INT,
            valve_rank INT,
            valve_point VARCHAR(50),
            score DECIMAL(12,2),
            point DECIMAL(12,2),
            rank_change VARCHAR(50),
            rank_diff VARCHAR(50),
            crawl_time DATETIME,
            KEY idx_team_id (team_id),
            KEY idx_crawl_time (crawl_time)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    "team_stat_snapshot": """
        CREATE TABLE IF NOT EXISTS team_stat_snapshot (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            team_id VARCHAR(50) NOT NULL,
            team_name VARCHAR(100),
            team_logo VARCHAR(255),
            region_name VARCHAR(100),
            rating DECIMAL(10,2),
            map_num INT,
            map_win_rate VARCHAR(20),
            map_win_loss VARCHAR(20),
            game_played DECIMAL(10,2),
            win_rate VARCHAR(20),
            avg_round DECIMAL(10,2),
            kd_rate DECIMAL(10,2),
            kd INT,
            kd_diff VARCHAR(50),
            first_five_win_num INT,
            first_five_win_rate VARCHAR(20),
            first_ten_win_num INT,
            first_ten_win_rate VARCHAR(20),
            ct_win_rate VARCHAR(20),
            ct_win_round INT,
            t_win_rate VARCHAR(20),
            t_win_round INT,
            ct_first_win_rate VARCHAR(20),
            ct_first_win_round INT,
            t_first_win_rate VARCHAR(20),
            t_first_win_round INT,
            first_kill INT,
            first_kill_rate VARCHAR(20),
            first_death_num INT,
            first_death_rate VARCHAR(20),
            avg_kill DECIMAL(10,2),
            avg_death DECIMAL(10,2),
            avg_assist DECIMAL(10,2),
            total_kill INT,
            total_death INT,
            total_assist INT,
            total_round INT,
            `index` VARCHAR(50),
            `rank` VARCHAR(50),
            global_rank VARCHAR(50),
            valve_rank VARCHAR(50),
            valve_point VARCHAR(50),
            point VARCHAR(50),
            score VARCHAR(50),
            global_bonus VARCHAR(50),
            rank_change VARCHAR(50),
            rank_diff VARCHAR(50),
            crawl_time DATETIME,
            KEY idx_team_id (team_id),
            KEY idx_crawl_time (crawl_time)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    "event_basic": """
        CREATE TABLE IF NOT EXISTS event_basic (
            event_id VARCHAR(50) PRIMARY KEY,
            event_name VARCHAR(255),
            event_logo VARCHAR(255),
            start_time DATETIME,
            end_time DATETIME
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    "match_schedule": """
        CREATE TABLE IF NOT EXISTS match_schedule (
            match_id VARCHAR(50) PRIMARY KEY,
            match_time DATETIME,
            bo INT,
            team1_id VARCHAR(50),
            team1 VARCHAR(100),
            team2_id VARCHAR(50),
            team2 VARCHAR(100),
            event_id VARCHAR(50),
            event_name VARCHAR(255),
            event_logo VARCHAR(255),
            event_start_time DATETIME,
            event_end_time DATETIME,
            score1 INT,
            score2 INT,
            status INT,
            KEY idx_event_id (event_id),
            KEY idx_match_time (match_time),
            KEY idx_team1_id (team1_id),
            KEY idx_team2_id (team2_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    "match_result": """
        CREATE TABLE IF NOT EXISTS match_result (
            match_id VARCHAR(50) PRIMARY KEY,
            match_time DATETIME,
            bo INT,
            team1_id VARCHAR(50),
            team1 VARCHAR(100),
            team2_id VARCHAR(50),
            team2 VARCHAR(100),
            event_id VARCHAR(50),
            event_name VARCHAR(255),
            event_logo VARCHAR(255),
            event_start_time DATETIME,
            event_end_time DATETIME,
            score1 INT,
            score2 INT,
            status INT,
            bout_count INT,
            bout_details TEXT,
            KEY idx_event_id (event_id),
            KEY idx_match_time (match_time),
            KEY idx_team1_id (team1_id),
            KEY idx_team2_id (team2_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    "match_result_detail": """
        CREATE TABLE IF NOT EXISTS match_result_detail (
            match_id VARCHAR(50) PRIMARY KEY,
            match_time DATETIME,
            bo INT,
            team1_id VARCHAR(50),
            team1 VARCHAR(100),
            team2_id VARCHAR(50),
            team2 VARCHAR(100),
            event_id VARCHAR(50),
            event_name VARCHAR(255),
            event_logo VARCHAR(255),
            event_start_time DATETIME,
            event_end_time DATETIME,
            score1 INT,
            score2 INT,
            status INT,
            bout_count INT,
            bout_details LONGTEXT,
            analysis_success TINYINT,
            analysis_state_ver VARCHAR(100),
            data_success TINYINT,
            data_state_ver VARCHAR(100),
            event_log_success TINYINT,
            event_log_to_ver VARCHAR(100),
            event_log_count INT,
            event_log_map_count INT,
            team1_form_rating VARCHAR(50),
            team2_form_rating VARCHAR(50),
            team1_form_win_rate VARCHAR(50),
            team2_form_win_rate VARCHAR(50),
            fetch_error VARCHAR(255),
            fetched_at DATETIME,
            schema_version VARCHAR(50),
            KEY idx_event_id (event_id),
            KEY idx_match_time (match_time),
            KEY idx_team1_id (team1_id),
            KEY idx_team2_id (team2_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    "match_result_player_stats": """
        CREATE TABLE IF NOT EXISTS match_result_player_stats (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            match_id VARCHAR(50) NOT NULL,
            team_side VARCHAR(8),
            team_id VARCHAR(50),
            team_name VARCHAR(100),
            player_id VARCHAR(50),
            player_name VARCHAR(100),
            country_name VARCHAR(100),
            country_logo VARCHAR(255),
            rating VARCHAR(50),
            adr VARCHAR(50),
            kast VARCHAR(50),
            kd VARCHAR(50),
            kpr VARCHAR(50),
            mk_rating VARCHAR(50),
            impact VARCHAR(50),
            swing VARCHAR(50),
            stat_index INT,
            fetched_at DATETIME,
            KEY idx_match_id (match_id),
            KEY idx_team_id (team_id),
            KEY idx_player_id (player_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    "match_result_map_stats": """
        CREATE TABLE IF NOT EXISTS match_result_map_stats (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            match_id VARCHAR(50) NOT NULL,
            map_index INT,
            map_name VARCHAR(100),
            team1_score INT,
            team2_score INT,
            winner_side VARCHAR(8),
            winner_team_id VARCHAR(50),
            winner_team_name VARCHAR(100),
            fetched_at DATETIME,
            KEY idx_match_id (match_id),
            KEY idx_winner_team_id (winner_team_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    "match_result_map_player_stats": """
        CREATE TABLE IF NOT EXISTS match_result_map_player_stats (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            match_id VARCHAR(50) NOT NULL,
            map_index INT,
            map_name VARCHAR(100),
            team_side VARCHAR(8),
            team_id VARCHAR(50),
            team_name VARCHAR(100),
            player_id VARCHAR(50),
            player_name VARCHAR(100),
            country_name VARCHAR(100),
            country_logo VARCHAR(255),
            rating VARCHAR(50),
            mk_rating VARCHAR(50),
            adr VARCHAR(50),
            kast VARCHAR(50),
            kpr VARCHAR(50),
            `kill` INT,
            `death` INT,
            `assist` INT,
            kd_rate VARCHAR(50),
            kd_diff VARCHAR(50),
            stat_index INT,
            bout_status INT,
            fetched_at DATETIME,
            KEY idx_match_id (match_id),
            KEY idx_map_index (map_index),
            KEY idx_team_id (team_id),
            KEY idx_player_id (player_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
}


TABLE_COLUMNS: Dict[str, List[str]] = {
    "team_basic": ["team_id", "team_name", "team_logo", "country_logo", "region_name", "crawl_time"],
    "team_player_relation": ["team_id", "team_name", "player_id", "player_name", "player_portrait", "player_country_logo", "crawl_time"],
    "team_rank_snapshot": ["team_id", "team_name", "team_logo", "country_logo", "global_rank", "valve_rank", "valve_point", "score", "point", "rank_change", "rank_diff", "crawl_time"],
    "team_stat_snapshot": [
        "team_id", "team_name", "team_logo", "region_name", "rating", "map_num", "map_win_rate", "map_win_loss",
        "game_played", "win_rate", "avg_round", "kd_rate", "kd", "kd_diff", "first_five_win_num", "first_five_win_rate",
        "first_ten_win_num", "first_ten_win_rate", "ct_win_rate", "ct_win_round", "t_win_rate", "t_win_round",
        "ct_first_win_rate", "ct_first_win_round", "t_first_win_rate", "t_first_win_round", "first_kill", "first_kill_rate",
        "first_death_num", "first_death_rate", "avg_kill", "avg_death", "avg_assist", "total_kill", "total_death",
        "total_assist", "total_round", "index", "rank", "global_rank", "valve_rank", "valve_point", "point", "score",
        "global_bonus", "rank_change", "rank_diff", "crawl_time"
    ],
    "event_basic": ["event_id", "event_name", "event_logo", "start_time", "end_time"],
    "match_schedule": ["match_id", "match_time", "bo", "team1_id", "team1", "team2_id", "team2", "event_id", "event_name", "event_logo", "event_start_time", "event_end_time", "score1", "score2", "status"],
    "match_result": ["match_id", "match_time", "bo", "team1_id", "team1", "team2_id", "team2", "event_id", "event_name", "event_logo", "event_start_time", "event_end_time", "score1", "score2", "status", "bout_count", "bout_details"],
    "match_result_detail": [
        "match_id", "match_time", "bo", "team1_id", "team1", "team2_id", "team2",
        "event_id", "event_name", "event_logo", "event_start_time", "event_end_time",
        "score1", "score2", "status", "bout_count", "bout_details",
        "analysis_success", "analysis_state_ver",
        "data_success", "data_state_ver",
        "event_log_success", "event_log_to_ver", "event_log_count", "event_log_map_count",
        "team1_form_rating", "team2_form_rating", "team1_form_win_rate", "team2_form_win_rate",
        "fetch_error", "fetched_at", "schema_version"
    ],
    "match_result_player_stats": [
        "match_id", "team_side", "team_id", "team_name", "player_id", "player_name",
        "country_name", "country_logo", "rating", "adr", "kast", "kd", "kpr",
        "mk_rating", "impact", "swing", "stat_index", "fetched_at"
    ],
    "match_result_map_stats": [
        "match_id", "map_index", "map_name", "team1_score", "team2_score", "winner_side",
        "winner_team_id", "winner_team_name", "fetched_at"
    ],
    "match_result_map_player_stats": [
        "match_id", "map_index", "map_name", "team_side", "team_id", "team_name",
        "player_id", "player_name", "country_name", "country_logo", "rating",
        "mk_rating", "adr", "kast", "kpr", "kill", "death", "assist",
        "kd_rate", "kd_diff", "stat_index", "bout_status", "fetched_at"
    ],
}

REQUIRED_COLUMN_DDL: Dict[str, Dict[str, str]] = {
    "event_basic": {
        "event_name": "VARCHAR(255) NULL",
        "event_logo": "VARCHAR(255) NULL",
        "start_time": "DATETIME NULL",
        "end_time": "DATETIME NULL",
    },
    "match_schedule": {
        "event_name": "VARCHAR(255) NULL",
        "event_logo": "VARCHAR(255) NULL",
        "event_start_time": "DATETIME NULL",
        "event_end_time": "DATETIME NULL",
    },
    "match_result": {
        "event_name": "VARCHAR(255) NULL",
        "event_logo": "VARCHAR(255) NULL",
        "event_start_time": "DATETIME NULL",
        "event_end_time": "DATETIME NULL",
    },
    "match_result_detail": {
        "event_name": "VARCHAR(255) NULL",
        "event_logo": "VARCHAR(255) NULL",
        "event_start_time": "DATETIME NULL",
        "event_end_time": "DATETIME NULL",
        "bout_count": "INT NULL",
        "bout_details": "LONGTEXT NULL",
        "analysis_success": "TINYINT NULL",
        "analysis_state_ver": "VARCHAR(100) NULL",
        "data_success": "TINYINT NULL",
        "data_state_ver": "VARCHAR(100) NULL",
        "event_log_success": "TINYINT NULL",
        "event_log_to_ver": "VARCHAR(100) NULL",
        "event_log_count": "INT NULL",
        "event_log_map_count": "INT NULL",
        "team1_form_rating": "VARCHAR(50) NULL",
        "team2_form_rating": "VARCHAR(50) NULL",
        "team1_form_win_rate": "VARCHAR(50) NULL",
        "team2_form_win_rate": "VARCHAR(50) NULL",
        "fetch_error": "VARCHAR(255) NULL",
        "fetched_at": "DATETIME NULL",
        "schema_version": "VARCHAR(50) NULL",
    },
    "match_result_player_stats": {
        "team_side": "VARCHAR(8) NULL",
        "team_id": "VARCHAR(50) NULL",
        "team_name": "VARCHAR(100) NULL",
        "player_id": "VARCHAR(50) NULL",
        "player_name": "VARCHAR(100) NULL",
        "country_name": "VARCHAR(100) NULL",
        "country_logo": "VARCHAR(255) NULL",
        "rating": "VARCHAR(50) NULL",
        "adr": "VARCHAR(50) NULL",
        "kast": "VARCHAR(50) NULL",
        "kd": "VARCHAR(50) NULL",
        "kpr": "VARCHAR(50) NULL",
        "mk_rating": "VARCHAR(50) NULL",
        "impact": "VARCHAR(50) NULL",
        "swing": "VARCHAR(50) NULL",
        "stat_index": "INT NULL",
        "fetched_at": "DATETIME NULL",
    },
    "match_result_map_stats": {
        "map_index": "INT NULL",
        "map_name": "VARCHAR(100) NULL",
        "team1_score": "INT NULL",
        "team2_score": "INT NULL",
        "winner_side": "VARCHAR(8) NULL",
        "winner_team_id": "VARCHAR(50) NULL",
        "winner_team_name": "VARCHAR(100) NULL",
        "fetched_at": "DATETIME NULL",
    },
    "match_result_map_player_stats": {
        "map_index": "INT NULL",
        "map_name": "VARCHAR(100) NULL",
        "team_side": "VARCHAR(8) NULL",
        "team_id": "VARCHAR(50) NULL",
        "team_name": "VARCHAR(100) NULL",
        "player_id": "VARCHAR(50) NULL",
        "player_name": "VARCHAR(100) NULL",
        "country_name": "VARCHAR(100) NULL",
        "country_logo": "VARCHAR(255) NULL",
        "rating": "VARCHAR(50) NULL",
        "mk_rating": "VARCHAR(50) NULL",
        "adr": "VARCHAR(50) NULL",
        "kast": "VARCHAR(50) NULL",
        "kpr": "VARCHAR(50) NULL",
        "kill": "INT NULL",
        "death": "INT NULL",
        "assist": "INT NULL",
        "kd_rate": "VARCHAR(50) NULL",
        "kd_diff": "VARCHAR(50) NULL",
        "stat_index": "INT NULL",
        "bout_status": "INT NULL",
        "fetched_at": "DATETIME NULL",
    },
}


def normalize_datetime_column(series: pd.Series) -> pd.Series:
    """Convert mixed datetime inputs to MySQL-friendly datetime strings.

    Handles:
    - normal datetime strings: ``YYYY-mm-dd HH:MM:SS``
    - unix timestamps in seconds (int/float/numeric strings)
    """
    # First try normal datetime parsing.
    dt = pd.to_datetime(series, errors="coerce")

    # For values not parsed above, attempt unix-seconds parsing.
    numeric = pd.to_numeric(series, errors="coerce")
    needs_unix = dt.isna() & numeric.notna()
    if needs_unix.any():
        dt.loc[needs_unix] = pd.to_datetime(
            numeric[needs_unix], unit="s", errors="coerce"
        )

    # Convert to MySQL DATETIME text; invalid values become None.
    out = dt.dt.strftime("%Y-%m-%d %H:%M:%S")
    return out.where(dt.notna(), None)


def quote_ident(name: str) -> str:
    return f"`{name.replace('`', '``')}`"


def is_valid_team_id(value) -> bool:
    text = str(value).strip()
    if not text:
        return False
    return text.lower() not in INVALID_TEAM_ID_TOKENS


def get_connection(database=None):
    config = DB_CONFIG.copy()
    if database is None:
        config.pop("database", None)
    else:
        config["database"] = database
    return pymysql.connect(**config)


def create_database_and_tables():
    conn = get_connection(None)
    try:
        with conn.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS {DATABASE_NAME} DEFAULT CHARSET utf8mb4;")
    finally:
        conn.close()

    conn = get_connection(DATABASE_NAME)
    try:
        with conn.cursor() as cursor:
            for sql in TABLE_SQL.values():
                cursor.execute(sql)
            ensure_required_columns(cursor)
        print("Database and tables created.")
    finally:
        conn.close()


def ensure_required_columns(cursor):
    for table_name, column_map in REQUIRED_COLUMN_DDL.items():
        cursor.execute(f"SHOW COLUMNS FROM `{table_name}`")
        rows = cursor.fetchall()
        existing_columns = set()
        for row in rows:
            if isinstance(row, dict):
                existing_columns.add(str(row.get("Field") or ""))
            elif isinstance(row, (list, tuple)) and row:
                existing_columns.add(str(row[0]))
        existing_columns = {c for c in existing_columns if c}
        for col, ddl in column_map.items():
            if col in existing_columns:
                continue
            cursor.execute(f"ALTER TABLE `{table_name}` ADD COLUMN `{col}` {ddl}")
            print(f"[MIGRATE] Added missing column `{table_name}`.`{col}`")


def backfill_event_time_range(conn):
    cursor = conn.cursor()
    try:
        cursor.execute("SHOW COLUMNS FROM `event_basic`")
        event_cols = {str(row[0]) for row in cursor.fetchall()}
        if "start_time" not in event_cols or "end_time" not in event_cols:
            print("[WARN] skip event time backfill: event_basic missing start_time/end_time")
            return

        table_exprs: List[str] = []
        for table_name in ("match_result_detail", "match_result", "match_schedule"):
            cursor.execute(f"SHOW COLUMNS FROM `{table_name}`")
            cols = {str(row[0]) for row in cursor.fetchall()}
            if "event_id" not in cols or "match_time" not in cols:
                continue

            start_expr = "match_time"
            end_expr = "match_time"
            if "event_start_time" in cols:
                start_expr = "COALESCE(event_start_time, match_time)"
            if "event_end_time" in cols:
                end_expr = "COALESCE(event_end_time, match_time)"

            table_exprs.append(
                f"""
                SELECT
                    event_id,
                    {start_expr} AS start_candidate,
                    {end_expr} AS end_candidate
                FROM `{table_name}`
                WHERE event_id IS NOT NULL AND event_id <> ''
                """
            )

        if not table_exprs:
            print("[WARN] skip event time backfill: no usable match tables")
            return

        merged_sql = "\nUNION ALL\n".join(table_exprs)
        cursor.execute(
            f"""
            UPDATE event_basic eb
            JOIN (
                SELECT
                    event_id,
                    MIN(start_candidate) AS min_start_time,
                    MAX(end_candidate) AS max_end_time
                FROM (
                    {merged_sql}
                ) merged
                GROUP BY event_id
            ) ms ON ms.event_id = eb.event_id
            SET
                eb.start_time = CASE
                    WHEN eb.start_time IS NULL THEN ms.min_start_time
                    WHEN ms.min_start_time IS NULL THEN eb.start_time
                    WHEN ms.min_start_time < eb.start_time THEN ms.min_start_time
                    ELSE eb.start_time
                END,
                eb.end_time = CASE
                    WHEN eb.end_time IS NULL THEN ms.max_end_time
                    WHEN ms.max_end_time IS NULL THEN eb.end_time
                    WHEN ms.max_end_time > eb.end_time THEN ms.max_end_time
                    ELSE eb.end_time
                END
            """
        )
        print(f"[INFO] event_basic time range backfilled/merged: {cursor.rowcount}")
    finally:
        cursor.close()


def prepare_dataframe(file_path: str) -> pd.DataFrame:
    df = pd.read_csv(file_path)
    df.columns = [col.strip() for col in df.columns]
    df = df.astype(object).where(pd.notna(df), None)
    return df


def ensure_team_basic_rows(conn, df: pd.DataFrame):
    if "team_id" not in df.columns:
        return

    cols = ["team_id"]
    if "team_name" in df.columns:
        cols.append("team_name")

    refs = df[cols].copy()
    refs["team_id"] = refs["team_id"].astype(str).str.strip()
    refs = refs[refs["team_id"].map(is_valid_team_id)]
    if "team_name" not in refs.columns:
        refs["team_name"] = None
    refs = refs.drop_duplicates(subset=["team_id"])

    if refs.empty:
        return

    cur = conn.cursor()
    cur.execute("SELECT team_id FROM team_basic")
    existing = {row[0] for row in cur.fetchall()}

    missing_rows = []
    for row in refs.itertuples(index=False):
        team_id = row[0]
        team_name = row[1] if len(row) > 1 else None
        if team_id not in existing:
            missing_rows.append((team_id, team_name))

    if missing_rows:
        cur.executemany(
            "INSERT IGNORE INTO team_basic (team_id, team_name) VALUES (%s, %s)",
            missing_rows,
        )
        print(f"[INFO] Added missing teams into team_basic: {len(missing_rows)}")
    cur.close()


def ensure_match_team_basic_rows(conn, df: pd.DataFrame):
    """Add missing teams from match team1/team2 ids into team_basic."""
    refs = []

    if "team1_id" in df.columns:
        refs.append(
            pd.DataFrame(
                {
                    "team_id": df["team1_id"],
                    "team_name": df["team1"] if "team1" in df.columns else None,
                }
            )
        )
    if "team2_id" in df.columns:
        refs.append(
            pd.DataFrame(
                {
                    "team_id": df["team2_id"],
                    "team_name": df["team2"] if "team2" in df.columns else None,
                }
            )
        )

    if not refs:
        return

    merged = pd.concat(refs, ignore_index=True)
    ensure_team_basic_rows(conn, merged)


def enrich_snapshot_team_meta(conn, table_name: str, df: pd.DataFrame) -> pd.DataFrame:
    """Fill missing team metadata in snapshot tables from team_basic."""
    if "team_id" not in df.columns:
        return df

    table_meta_columns = {
        "team_rank_snapshot": ["team_name", "team_logo", "country_logo"],
        "team_stat_snapshot": ["team_name", "team_logo", "region_name"],
    }
    target_cols = table_meta_columns.get(table_name, [])
    if not target_cols:
        return df

    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT team_id, team_name, team_logo, country_logo, region_name
        FROM team_basic
        """
    )
    rows = cursor.fetchall()
    cursor.close()
    if not rows:
        return df

    meta_df = pd.DataFrame(
        rows,
        columns=["team_id", "team_name", "team_logo", "country_logo", "region_name"],
    )
    meta_df["team_id"] = meta_df["team_id"].astype(str).str.strip()

    result = df.copy()
    result["team_id"] = result["team_id"].astype(str).str.strip()
    for col in target_cols:
        if col not in result.columns:
            result[col] = None

    result = result.merge(
        meta_df[["team_id", *target_cols]],
        on="team_id",
        how="left",
        suffixes=("", "__tb"),
    )

    for col in target_cols:
        tb_col = f"{col}__tb"
        current = result[col]
        is_blank = current.isna() | current.astype(str).str.strip().isin(["", "None", "nan"])
        result[col] = current.where(~is_blank, result[tb_col])
        result = result.drop(columns=[tb_col])

    return result


def insert_dataframe(table_name: str, df: pd.DataFrame):
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor()

    # Clear table first to support full refresh imports.
    # TRUNCATE may fail when table is referenced by FK, so disable FK checks
    # for the current session during truncate.
    cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
    try:
        cursor.execute(f"TRUNCATE TABLE `{table_name}`")
    finally:
        cursor.execute("SET FOREIGN_KEY_CHECKS = 1")

    # Align dataframe columns to current table schema for compatibility with
    # existing databases that may use old column names.
    cursor.execute(f"SHOW COLUMNS FROM `{table_name}`")
    table_schema = cursor.fetchall()
    table_columns = {row[0] for row in table_schema}
    datetime_columns = {
        row[0]
        for row in table_schema
        if str(row[1]).lower().startswith("datetime")
        or str(row[1]).lower().startswith("timestamp")
    }

    # Keep FK integrity for child tables that reference team_basic.team_id
    if table_name in {
        "team_rank_snapshot",
        "team_stat_snapshot",
        "match_result_player_stats",
        "match_result_map_player_stats",
    }:
        ensure_team_basic_rows(conn, df)
    if table_name in {"match_schedule", "match_result", "match_result_detail"}:
        ensure_match_team_basic_rows(conn, df)

    # Backfill snapshot meta fields (team_name/logo/region/country) from
    # team_basic to avoid NULL-heavy snapshot tables when CSV omits these
    # columns.
    df = enrich_snapshot_team_meta(conn, table_name, df)

    if table_name == "team_basic" and "team_id" in df.columns:
        before = len(df)
        cleaned_ids = df["team_id"].astype(str).str.strip()
        keep_mask = cleaned_ids.map(is_valid_team_id)
        df = df[keep_mask].copy()
        dropped = before - len(df)
        if dropped > 0:
            print(f"[WARN] team_basic dropped invalid team_id rows: {dropped}")

    # Backward-compatible aliases: CSV column -> legacy DB column
    alias_map = {"index": "csv_index"}
    rename_map = {}
    for src_col, target_col in alias_map.items():
        if src_col in df.columns and src_col not in table_columns and target_col in table_columns:
            rename_map[src_col] = target_col
    if rename_map:
        df = df.rename(columns=rename_map)

    columns = [c for c in df.columns if c in table_columns]
    ignored_columns = [c for c in df.columns if c not in table_columns]
    if ignored_columns:
        print(f"[WARN] {table_name} ignored columns not in DB table: {ignored_columns}")

    if not columns:
        raise ValueError(f"No matching columns found between dataframe and table `{table_name}`")

    df = df[columns]

    # Convert datetime/timestamp columns to MySQL-friendly strings.
    for col in columns:
        if col in datetime_columns:
            df[col] = normalize_datetime_column(df[col])

    col_sql = ", ".join([f"`{c}`" for c in columns])
    placeholders = ", ".join(["%s"] * len(columns))
    insert_sql = f"INSERT INTO `{table_name}` ({col_sql}) VALUES ({placeholders})"

    data = []
    for row in df.itertuples(index=False, name=None):
        clean_row = tuple(None if pd.isna(x) else x for x in row)
        data.append(clean_row)

    if data:
        cursor.executemany(insert_sql, data)

    conn.commit()
    cursor.close()
    conn.close()

    print(f"{table_name} imported: {len(data)} rows.")


def import_events():
    def _pick_event_columns(df: pd.DataFrame) -> pd.DataFrame:
        out = pd.DataFrame(index=df.index.copy())
        out["event_id"] = df["event_id"] if "event_id" in df.columns else None
        out["event_name"] = df["event_name"] if "event_name" in df.columns else None
        out["event_logo"] = df["event_logo"] if "event_logo" in df.columns else None
        out["start_time"] = (
            df["start_time"]
            if "start_time" in df.columns
            else (df["event_start_time"] if "event_start_time" in df.columns else None)
        )
        out["end_time"] = (
            df["end_time"]
            if "end_time" in df.columns
            else (df["event_end_time"] if "event_end_time" in df.columns else None)
        )
        return out

    frames: List[pd.DataFrame] = []

    event_file = Path(CSV_FILES["event_basic"])
    if event_file.exists():
        event_df_raw = prepare_dataframe(str(event_file))
        frames.append(_pick_event_columns(event_df_raw))
    else:
        print(f"[WARN] event csv not found, fallback to matches/results only: {event_file}")

    match_df = prepare_dataframe(CSV_FILES["cs2_matches"])
    result_df = prepare_dataframe(CSV_FILES["cs2_results"])
    frames.append(_pick_event_columns(match_df))
    frames.append(_pick_event_columns(result_df))

    event_df = pd.concat(frames, ignore_index=True)
    event_df["event_id"] = event_df["event_id"].astype(str).str.strip()
    event_df = event_df[event_df["event_id"].astype(str).str.strip() != ""].copy()

    # Prefer non-empty values when the same event_id appears multiple times.
    event_df = event_df.fillna("")
    event_df["event_name"] = event_df["event_name"].astype(str).str.strip()
    event_df["event_logo"] = event_df["event_logo"].astype(str).str.strip()
    event_df["start_time"] = event_df["start_time"].astype(str).str.strip()
    event_df["end_time"] = event_df["end_time"].astype(str).str.strip()
    event_df["_has_start"] = event_df["start_time"].astype(str).str.strip() != ""
    event_df["_has_end"] = event_df["end_time"].astype(str).str.strip() != ""
    event_df = event_df.sort_values(
        by=["_has_start", "_has_end", "event_name", "event_logo"],
        ascending=[False, False, False, False],
    )
    event_df = event_df.drop_duplicates(subset=["event_id"], keep="first")
    event_df = event_df.drop(columns=["_has_start", "_has_end"])
    event_df = event_df.replace("", None)

    insert_dataframe("event_basic", event_df)


def import_generic_csv_file(csv_path: Path):
    table_name = csv_path.stem
    try:
        df = prepare_dataframe(str(csv_path))
    except pd.errors.EmptyDataError:
        print(f"[SKIP] {csv_path.name}: empty file")
        return

    headers = [str(col).strip() for col in df.columns]
    if not headers:
        print(f"[SKIP] {csv_path.name}: empty header")
        return

    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor()
    try:
        col_defs = ",\n".join([f"  {quote_ident(col)} LONGTEXT NULL" for col in headers])
        index_defs: List[str] = []
        if "player_id" in headers:
            index_defs.append("  KEY `idx_player_id` (`player_id`(100))")
        if "team_id" in headers:
            index_defs.append("  KEY `idx_team_id` (`team_id`(100))")
        if "match_id" in headers:
            index_defs.append("  KEY `idx_match_id` (`match_id`(100))")
        if "event_id" in headers:
            index_defs.append("  KEY `idx_event_id` (`event_id`(100))")
        if "tournament_id" in headers:
            index_defs.append("  KEY `idx_tournament_id` (`tournament_id`(100))")
        idx_sql = ""
        if index_defs:
            idx_sql = ",\n" + ",\n".join(index_defs)

        cursor.execute(f"DROP TABLE IF EXISTS {quote_ident(table_name)}")
        cursor.execute(
            f"""
            CREATE TABLE {quote_ident(table_name)} (
{col_defs}{idx_sql}
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )

        if df.empty:
            conn.commit()
            print(f"[DONE] {csv_path.name}: 0 rows")
            return

        columns_sql = ", ".join(quote_ident(col) for col in headers)
        placeholders = ", ".join(["%s"] * len(headers))
        insert_sql = (
            f"INSERT INTO {quote_ident(table_name)} ({columns_sql}) "
            f"VALUES ({placeholders})"
        )

        rows = []
        for row in df.itertuples(index=False, name=None):
            rows.append(tuple(None if pd.isna(v) else v for v in row))
        cursor.executemany(insert_sql, rows)
        conn.commit()
        print(f"[DONE] {csv_path.name}: {len(rows)} rows")
    finally:
        cursor.close()
        conn.close()


def import_additional_csvs():
    if not CS_DATA_DIR.exists():
        print(f"[WARN] csv directory not found: {CS_DATA_DIR}")
        return

    core_csv_names = {Path(path).name for path in CSV_FILES.values()}
    reserved_table_names = set(TABLE_SQL.keys())
    extra_csv_files = sorted(
        p for p in CS_DATA_DIR.glob("*.csv")
        if (
            p.is_file()
            and not p.name.startswith("_")
            and "_raw_backup_" not in p.stem
            and p.name not in core_csv_names
            and p.stem not in reserved_table_names
        )
    )

    if not extra_csv_files:
        print("[INFO] No additional CSV files to import.")
        return

    print(f"[INFO] Importing additional CSV files from {CS_DATA_DIR} ...")
    for csv_path in extra_csv_files:
        import_generic_csv_file(csv_path)


def main():
    create_database_and_tables()

    insert_dataframe("team_basic", prepare_dataframe(CSV_FILES["team_basic"]))
    insert_dataframe("team_player_relation", prepare_dataframe(CSV_FILES["team_player_relation"]))
    insert_dataframe("team_rank_snapshot", prepare_dataframe(CSV_FILES["team_rank_snapshot"]))
    insert_dataframe("team_stat_snapshot", prepare_dataframe(CSV_FILES["team_stat_snapshot"]))
    import_events()
    insert_dataframe("match_schedule", prepare_dataframe(CSV_FILES["cs2_matches"]))
    insert_dataframe("match_result", prepare_dataframe(CSV_FILES["cs2_results"]))
    insert_dataframe("match_result_detail", prepare_dataframe(CSV_FILES["match_result_detail"]))
    insert_dataframe("match_result_player_stats", prepare_dataframe(CSV_FILES["match_result_player_stats"]))
    insert_dataframe("match_result_map_stats", prepare_dataframe(CSV_FILES["match_result_map_stats"]))
    insert_dataframe("match_result_map_player_stats", prepare_dataframe(CSV_FILES["match_result_map_player_stats"]))
    conn = pymysql.connect(**DB_CONFIG)
    try:
        backfill_event_time_range(conn)
    finally:
        conn.close()
    import_additional_csvs()

    print("\nAll imports completed.")
    print(f"Database: {DATABASE_NAME}")


if __name__ == "__main__":
    main()
