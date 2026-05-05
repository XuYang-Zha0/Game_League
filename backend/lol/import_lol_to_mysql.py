from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List

import pandas as pd
import pymysql


def load_local_env() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
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
}

BASE_DIR = Path(__file__).resolve().parent
LOL_DATA_DIR = BASE_DIR / "lol_data"

CSV_FILES = {
    "lol_event_basic": LOL_DATA_DIR / "lol_event_basic.csv",
    "lol_team_basic": LOL_DATA_DIR / "lol_team_basic.csv",
    "lol_player_basic": LOL_DATA_DIR / "lol_player_basic.csv",
    "lol_match_result": LOL_DATA_DIR / "lol_match_result.csv",
    "lol_game_basic": LOL_DATA_DIR / "lol_game_basic.csv",
    "lol_game_player_stats": LOL_DATA_DIR / "lol_game_player_stats.csv",
}

TABLE_SQL: Dict[str, str] = {
    "lol_event_basic": """
        CREATE TABLE IF NOT EXISTS lol_event_basic (
            event_id VARCHAR(100) PRIMARY KEY,
            event_name VARCHAR(255),
            source VARCHAR(50),
            source_event_url VARCHAR(500),
            fetched_at DATETIME,
            KEY idx_event_name (event_name)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    "lol_team_basic": """
        CREATE TABLE IF NOT EXISTS lol_team_basic (
            team_id VARCHAR(120) PRIMARY KEY,
            team_name VARCHAR(255),
            region VARCHAR(100),
            team_logo VARCHAR(500),
            source VARCHAR(50),
            fetched_at DATETIME,
            KEY idx_team_name (team_name)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    "lol_player_basic": """
        CREATE TABLE IF NOT EXISTS lol_player_basic (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            player_id VARCHAR(120),
            player_name VARCHAR(255),
            team_id VARCHAR(120),
            team_name VARCHAR(255),
            role VARCHAR(50),
            avatar VARCHAR(500),
            is_active TINYINT DEFAULT 1,
            source VARCHAR(50),
            fetched_at DATETIME,
            UNIQUE KEY uk_player_team (player_id, team_id),
            KEY idx_player_id (player_id),
            KEY idx_team_id (team_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    "lol_match_result": """
        CREATE TABLE IF NOT EXISTS lol_match_result (
            match_id VARCHAR(100) PRIMARY KEY,
            source VARCHAR(50),
            source_match_url VARCHAR(500),
            first_game_id VARCHAR(100),
            event_id VARCHAR(100),
            event_name VARCHAR(255),
            match_date DATE,
            match_time DATETIME,
            league_slug VARCHAR(100),
            stage VARCHAR(100),
            patch VARCHAR(50),
            team1_id VARCHAR(120),
            team1 VARCHAR(255),
            team1_logo VARCHAR(500),
            team2_id VARCHAR(120),
            team2 VARCHAR(255),
            team2_logo VARCHAR(500),
            score1 INT,
            score2 INT,
            winner VARCHAR(255),
            bo INT,
            status VARCHAR(30),
            fetched_at DATETIME,
            KEY idx_event_id (event_id),
            KEY idx_match_date (match_date),
            KEY idx_match_time (match_time),
            KEY idx_league_slug (league_slug),
            KEY idx_team1_id (team1_id),
            KEY idx_team2_id (team2_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    "lol_game_basic": """
        CREATE TABLE IF NOT EXISTS lol_game_basic (
            game_id VARCHAR(100) PRIMARY KEY,
            match_id VARCHAR(100),
            event_id VARCHAR(100),
            event_name VARCHAR(255),
            game_number INT,
            match_date DATE,
            stage VARCHAR(100),
            patch VARCHAR(50),
            team1_id VARCHAR(120),
            team1 VARCHAR(255),
            team2_id VARCHAR(120),
            team2 VARCHAR(255),
            source_game_url VARCHAR(500),
            fetched_at DATETIME,
            KEY idx_match_id (match_id),
            KEY idx_event_id (event_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    "lol_game_player_stats": """
        CREATE TABLE IF NOT EXISTS lol_game_player_stats (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            game_id VARCHAR(100),
            match_id VARCHAR(100),
            event_id VARCHAR(100),
            event_name VARCHAR(255),
            game_number INT,
            team_side VARCHAR(20),
            team_id VARCHAR(120),
            team_name VARCHAR(255),
            player_id VARCHAR(120),
            player_name VARCHAR(255),
            champion VARCHAR(100),
            kills INT,
            deaths INT,
            assists INT,
            kda_text VARCHAR(50),
            cs INT,
            stat_index INT,
            source_player_url VARCHAR(500),
            fetched_at DATETIME,
            UNIQUE KEY uk_game_stat (game_id, stat_index),
            KEY idx_game_id (game_id),
            KEY idx_match_id (match_id),
            KEY idx_event_id (event_id),
            KEY idx_player_id (player_id),
            KEY idx_team_id (team_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
}

TABLE_COLUMN_SQL: Dict[str, Dict[str, str]] = {
    "lol_team_basic": {
        "team_logo": "ALTER TABLE lol_team_basic ADD COLUMN team_logo VARCHAR(500) AFTER region",
    },
    "lol_player_basic": {
        "avatar": "ALTER TABLE lol_player_basic ADD COLUMN avatar VARCHAR(500) AFTER role",
        "is_active": "ALTER TABLE lol_player_basic ADD COLUMN is_active TINYINT DEFAULT 1 AFTER avatar",
    },
    "lol_match_result": {
        "match_time": "ALTER TABLE lol_match_result ADD COLUMN match_time DATETIME AFTER match_date",
        "league_slug": "ALTER TABLE lol_match_result ADD COLUMN league_slug VARCHAR(100) AFTER match_time",
        "team1_logo": "ALTER TABLE lol_match_result ADD COLUMN team1_logo VARCHAR(500) AFTER team1",
        "team2_logo": "ALTER TABLE lol_match_result ADD COLUMN team2_logo VARCHAR(500) AFTER team2",
        "status": "ALTER TABLE lol_match_result ADD COLUMN status VARCHAR(30) AFTER bo",
    },
}

TABLE_INDEX_SQL: Dict[str, Dict[str, str]] = {
    "lol_match_result": {
        "idx_match_time": "ALTER TABLE lol_match_result ADD KEY idx_match_time (match_time)",
        "idx_league_slug": "ALTER TABLE lol_match_result ADD KEY idx_league_slug (league_slug)",
    },
}


def get_connection(database: str | None = None) -> pymysql.Connection:
    config = DB_CONFIG.copy()
    if database is None:
        config.pop("database", None)
    else:
        config["database"] = database
    return pymysql.connect(**config)


def create_database_and_tables() -> None:
    with get_connection(None) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{DATABASE_NAME}` DEFAULT CHARSET utf8mb4;"
            )

    with get_connection(DATABASE_NAME) as conn:
        with conn.cursor() as cursor:
            for sql in TABLE_SQL.values():
                cursor.execute(sql)
            ensure_table_migrations(cursor)
    print(f"[DONE] Database and LoL tables ready: {DATABASE_NAME}")


def ensure_table_migrations(cursor: pymysql.cursors.Cursor) -> None:
    for table_name, column_sql in TABLE_COLUMN_SQL.items():
        cursor.execute(f"SHOW COLUMNS FROM {quote_ident(table_name)}")
        existing_columns = {row[0] for row in cursor.fetchall()}
        for column_name, sql in column_sql.items():
            if column_name not in existing_columns:
                cursor.execute(sql)
                print(f"[DONE] {table_name}.{column_name} column added")

    for table_name, index_sql in TABLE_INDEX_SQL.items():
        cursor.execute(f"SHOW INDEX FROM {quote_ident(table_name)}")
        existing_indexes = {row[2] for row in cursor.fetchall()}
        for index_name, sql in index_sql.items():
            if index_name not in existing_indexes:
                cursor.execute(sql)
                print(f"[DONE] {table_name}.{index_name} index added")


def prepare_dataframe(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df.columns = [str(col).strip() for col in df.columns]
    df = df.astype(object).where(pd.notna(df), None)
    df = df.replace("", None)
    return df


def normalize_date_column(series: pd.Series) -> pd.Series:
    dt = pd.to_datetime(series, errors="coerce")
    out = dt.dt.strftime("%Y-%m-%d")
    return out.where(dt.notna(), None)


def normalize_datetime_column(series: pd.Series) -> pd.Series:
    dt = pd.to_datetime(series, errors="coerce")
    out = dt.dt.strftime("%Y-%m-%d %H:%M:%S")
    return out.where(dt.notna(), None)


def quote_ident(name: str) -> str:
    return f"`{name.replace('`', '``')}`"


def insert_dataframe(table_name: str, df: pd.DataFrame) -> None:
    with get_connection(DATABASE_NAME) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
            try:
                cursor.execute(f"TRUNCATE TABLE {quote_ident(table_name)}")
            finally:
                cursor.execute("SET FOREIGN_KEY_CHECKS = 1")

            cursor.execute(f"SHOW COLUMNS FROM {quote_ident(table_name)}")
            schema = cursor.fetchall()
            table_columns = {row[0] for row in schema}
            date_columns = {row[0] for row in schema if str(row[1]).lower().startswith("date")}
            datetime_columns = {
                row[0]
                for row in schema
                if str(row[1]).lower().startswith("datetime")
                or str(row[1]).lower().startswith("timestamp")
            }

            columns = [col for col in df.columns if col in table_columns]
            ignored = [col for col in df.columns if col not in table_columns]
            if ignored:
                print(f"[WARN] {table_name} ignored columns: {ignored}")
            if not columns:
                raise ValueError(f"No matching columns for table {table_name}")

            df = df[columns].copy()
            for col in columns:
                if col in date_columns:
                    df[col] = normalize_date_column(df[col])
                elif col in datetime_columns:
                    df[col] = normalize_datetime_column(df[col])

            placeholders = ", ".join(["%s"] * len(columns))
            column_sql = ", ".join(quote_ident(col) for col in columns)
            insert_sql = (
                f"INSERT INTO {quote_ident(table_name)} ({column_sql}) VALUES ({placeholders})"
            )
            rows = [
                tuple(None if pd.isna(value) else value for value in row)
                for row in df.itertuples(index=False, name=None)
            ]
            if rows:
                cursor.executemany(insert_sql, rows)
    print(f"[DONE] {table_name}: {len(df)} rows imported")


def verify_csv_files() -> None:
    missing = [str(path) for path in CSV_FILES.values() if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing LoL CSV files:\n" + "\n".join(missing))


def main() -> int:
    verify_csv_files()
    create_database_and_tables()
    for table_name, csv_path in CSV_FILES.items():
        insert_dataframe(table_name, prepare_dataframe(csv_path))
    print("[DONE] LoL CSV import finished.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
