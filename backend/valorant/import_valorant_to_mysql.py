from __future__ import annotations

import csv
import os
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

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
    "cursorclass": pymysql.cursors.DictCursor,
}

BASE_DIR = Path(__file__).resolve().parent
VALORANT_DATA_DIR = BASE_DIR / "vlr_data"

CSV_FILES = {
    "schedule": VALORANT_DATA_DIR / "valorant_match_schedule_vlr_experiment.csv",
    "result": VALORANT_DATA_DIR / "valorant_match_result_vlr_experiment.csv",
    "detail": VALORANT_DATA_DIR / "valorant_match_detail_vlr_experiment.csv",
    "map_stats": VALORANT_DATA_DIR / "valorant_match_map_stats_vlr_experiment.csv",
    "player_stats": VALORANT_DATA_DIR / "valorant_match_player_stats_vlr_experiment.csv",
    "player_summary": VALORANT_DATA_DIR / "valorant_player_stats_vlr_experiment.csv",
    "player_profile": VALORANT_DATA_DIR / "valorant_player_profile_vlr_experiment.csv",
}

TABLE_SQL: Dict[str, str] = {
    "valorant_event_basic": """
        CREATE TABLE IF NOT EXISTS valorant_event_basic (
            event_id VARCHAR(120) PRIMARY KEY,
            event_slug VARCHAR(180),
            event_name VARCHAR(255),
            region VARCHAR(100),
            tier VARCHAR(50),
            event_logo VARCHAR(500),
            event_start_time DATETIME,
            event_end_time DATETIME,
            source VARCHAR(50),
            source_event_url VARCHAR(500),
            fetched_at DATETIME,
            KEY idx_event_name (event_name),
            KEY idx_region (region),
            KEY idx_event_time (event_start_time, event_end_time)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    "valorant_team_basic": """
        CREATE TABLE IF NOT EXISTS valorant_team_basic (
            team_id VARCHAR(120) PRIMARY KEY,
            team_slug VARCHAR(180),
            team_name VARCHAR(255),
            country VARCHAR(50),
            region VARCHAR(100),
            team_logo VARCHAR(500),
            source VARCHAR(50),
            source_team_url VARCHAR(500),
            fetched_at DATETIME,
            KEY idx_team_name (team_name),
            KEY idx_region (region)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    "valorant_player_basic": """
        CREATE TABLE IF NOT EXISTS valorant_player_basic (
            player_id VARCHAR(120) PRIMARY KEY,
            player_slug VARCHAR(180),
            player_name VARCHAR(255),
            country VARCHAR(50),
            current_team_abbrev VARCHAR(80),
            current_team_name VARCHAR(255),
            agents VARCHAR(500),
            avatar VARCHAR(500),
            avatar_source VARCHAR(50),
            avatar_source_url VARCHAR(500),
            avatar_checked_at DATETIME,
            source VARCHAR(50),
            source_player_url VARCHAR(500),
            fetched_at DATETIME,
            KEY idx_player_name (player_name),
            KEY idx_team_abbrev (current_team_abbrev)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    "valorant_team_player_relation": """
        CREATE TABLE IF NOT EXISTS valorant_team_player_relation (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            team_id VARCHAR(120),
            team_name VARCHAR(255),
            team_abbrev VARCHAR(80),
            player_id VARCHAR(120),
            player_name VARCHAR(255),
            is_active TINYINT DEFAULT 1,
            source VARCHAR(50),
            fetched_at DATETIME,
            UNIQUE KEY uk_team_player (team_abbrev, player_id),
            KEY idx_team_id (team_id),
            KEY idx_player_id (player_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    "valorant_match_schedule": """
        CREATE TABLE IF NOT EXISTS valorant_match_schedule (
            match_id VARCHAR(120) PRIMARY KEY,
            slug VARCHAR(255),
            match_url VARCHAR(500),
            match_date DATE,
            match_time DATETIME,
            time_text VARCHAR(50),
            event_id VARCHAR(120),
            event_name VARCHAR(255),
            stage VARCHAR(255),
            bo INT,
            team1_id VARCHAR(120),
            team1 VARCHAR(255),
            team1_country VARCHAR(50),
            team1_logo VARCHAR(500),
            team2_id VARCHAR(120),
            team2 VARCHAR(255),
            team2_country VARCHAR(50),
            team2_logo VARCHAR(500),
            score1 INT,
            score2 INT,
            winner VARCHAR(255),
            status VARCHAR(40),
            note VARCHAR(255),
            source VARCHAR(50),
            source_list_url VARCHAR(500),
            fetched_at DATETIME,
            KEY idx_match_time (match_time),
            KEY idx_event_id (event_id),
            KEY idx_team1_id (team1_id),
            KEY idx_team2_id (team2_id),
            KEY idx_status (status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    "valorant_match_result": """
        CREATE TABLE IF NOT EXISTS valorant_match_result (
            match_id VARCHAR(120) PRIMARY KEY,
            slug VARCHAR(255),
            match_url VARCHAR(500),
            match_date DATE,
            match_time DATETIME,
            time_text VARCHAR(50),
            event_id VARCHAR(120),
            event_name VARCHAR(255),
            stage VARCHAR(255),
            bo INT,
            team1_id VARCHAR(120),
            team1 VARCHAR(255),
            team1_country VARCHAR(50),
            team1_logo VARCHAR(500),
            team2_id VARCHAR(120),
            team2 VARCHAR(255),
            team2_country VARCHAR(50),
            team2_logo VARCHAR(500),
            score1 INT,
            score2 INT,
            winner VARCHAR(255),
            status VARCHAR(40),
            note VARCHAR(255),
            source VARCHAR(50),
            source_list_url VARCHAR(500),
            fetched_at DATETIME,
            KEY idx_match_time (match_time),
            KEY idx_event_id (event_id),
            KEY idx_team1_id (team1_id),
            KEY idx_team2_id (team2_id),
            KEY idx_status (status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    "valorant_match_detail": """
        CREATE TABLE IF NOT EXISTS valorant_match_detail (
            match_id VARCHAR(120) PRIMARY KEY,
            slug VARCHAR(255),
            match_url VARCHAR(500),
            event_id VARCHAR(120),
            event_slug VARCHAR(180),
            event_name VARCHAR(255),
            stage VARCHAR(255),
            match_time_utc DATETIME,
            bo INT,
            team1_id VARCHAR(120),
            team1 VARCHAR(255),
            team1_logo VARCHAR(500),
            team2_id VARCHAR(120),
            team2 VARCHAR(255),
            team2_logo VARCHAR(500),
            score VARCHAR(50),
            score1 INT,
            score2 INT,
            status VARCHAR(40),
            source VARCHAR(50),
            source_url VARCHAR(500),
            fetched_at DATETIME,
            KEY idx_event_id (event_id),
            KEY idx_match_time_utc (match_time_utc),
            KEY idx_team1_id (team1_id),
            KEY idx_team2_id (team2_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    "valorant_match_map_stats": """
        CREATE TABLE IF NOT EXISTS valorant_match_map_stats (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            match_id VARCHAR(120),
            game_id VARCHAR(120),
            map_index INT,
            map_name VARCHAR(100),
            duration VARCHAR(50),
            team1 VARCHAR(255),
            team1_score INT,
            team1_ct_score INT,
            team1_t_score INT,
            team2 VARCHAR(255),
            team2_score INT,
            team2_ct_score INT,
            team2_t_score INT,
            winner VARCHAR(255),
            source VARCHAR(50),
            source_url VARCHAR(500),
            fetched_at DATETIME,
            UNIQUE KEY uk_match_game (match_id, game_id),
            KEY idx_match_id (match_id),
            KEY idx_map_name (map_name)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    "valorant_match_player_stats": """
        CREATE TABLE IF NOT EXISTS valorant_match_player_stats (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            match_id VARCHAR(120),
            game_id VARCHAR(120),
            map_index INT,
            map_name VARCHAR(100),
            player_id VARCHAR(120),
            player_slug VARCHAR(180),
            player_name VARCHAR(255),
            team_abbrev VARCHAR(80),
            country VARCHAR(50),
            agents VARCHAR(500),
            rating VARCHAR(50),
            acs VARCHAR(50),
            kills INT,
            deaths INT,
            assists INT,
            kd_diff VARCHAR(50),
            kast VARCHAR(50),
            adr VARCHAR(50),
            hs_pct VARCHAR(50),
            first_kills INT,
            first_deaths INT,
            source VARCHAR(50),
            source_url VARCHAR(500),
            fetched_at DATETIME,
            UNIQUE KEY uk_match_game_player (match_id, game_id, player_id),
            KEY idx_match_id (match_id),
            KEY idx_game_id (game_id),
            KEY idx_player_id (player_id),
            KEY idx_team_abbrev (team_abbrev)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    "valorant_player_stats_summary": """
        CREATE TABLE IF NOT EXISTS valorant_player_stats_summary (
            player_id VARCHAR(120) PRIMARY KEY,
            player_slug VARCHAR(180),
            player_name VARCHAR(255),
            team_abbrev VARCHAR(80),
            country VARCHAR(50),
            agents VARCHAR(500),
            rounds INT,
            rating VARCHAR(50),
            acs VARCHAR(50),
            kd VARCHAR(50),
            kast VARCHAR(50),
            adr VARCHAR(50),
            kpr VARCHAR(50),
            apr VARCHAR(50),
            fkpr VARCHAR(50),
            fdpr VARCHAR(50),
            hs_pct VARCHAR(50),
            cl_pct VARCHAR(50),
            clutches VARCHAR(50),
            kmax INT,
            kills INT,
            deaths INT,
            assists INT,
            first_kills INT,
            first_deaths INT,
            source VARCHAR(50),
            source_url VARCHAR(500),
            fetched_at DATETIME,
            KEY idx_team_abbrev (team_abbrev),
            KEY idx_rating (rating)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    "valorant_player_agent_stats": """
        CREATE TABLE IF NOT EXISTS valorant_player_agent_stats (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            player_id VARCHAR(120),
            player_name VARCHAR(255),
            team_abbrev VARCHAR(80),
            agent VARCHAR(100),
            source VARCHAR(50),
            fetched_at DATETIME,
            UNIQUE KEY uk_player_agent (player_id, agent),
            KEY idx_player_id (player_id),
            KEY idx_agent (agent)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    "valorant_team_rank_snapshot": """
        CREATE TABLE IF NOT EXISTS valorant_team_rank_snapshot (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            team_id VARCHAR(120),
            team_name VARCHAR(255),
            region VARCHAR(100),
            global_rank INT,
            rating VARCHAR(50),
            wins INT,
            losses INT,
            crawl_time DATETIME,
            KEY idx_team_id (team_id),
            KEY idx_crawl_time (crawl_time)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
}

TABLE_COLUMNS: Dict[str, Sequence[str]] = {
    "valorant_match_schedule": [
        "match_id", "slug", "match_url", "match_date", "match_time", "time_text",
        "event_id", "event_name", "stage", "bo", "team1_id", "team1",
        "team1_country", "team1_logo", "team2_id", "team2", "team2_country",
        "team2_logo", "score1", "score2", "winner", "status", "note", "source",
        "source_list_url", "fetched_at",
    ],
    "valorant_match_result": [
        "match_id", "slug", "match_url", "match_date", "match_time", "time_text",
        "event_id", "event_name", "stage", "bo", "team1_id", "team1",
        "team1_country", "team1_logo", "team2_id", "team2", "team2_country",
        "team2_logo", "score1", "score2", "winner", "status", "note", "source",
        "source_list_url", "fetched_at",
    ],
    "valorant_match_detail": [
        "match_id", "slug", "match_url", "event_id", "event_slug", "event_name",
        "stage", "match_time_utc", "bo", "team1_id", "team1", "team1_logo",
        "team2_id", "team2", "team2_logo", "score", "score1", "score2",
        "status", "source", "source_url", "fetched_at",
    ],
    "valorant_match_map_stats": [
        "match_id", "game_id", "map_index", "map_name", "duration", "team1",
        "team1_score", "team1_ct_score", "team1_t_score", "team2",
        "team2_score", "team2_ct_score", "team2_t_score", "winner", "source",
        "source_url", "fetched_at",
    ],
    "valorant_match_player_stats": [
        "match_id", "game_id", "map_index", "map_name", "player_id",
        "player_slug", "player_name", "team_abbrev", "country", "agents",
        "rating", "acs", "kills", "deaths", "assists", "kd_diff", "kast",
        "adr", "hs_pct", "first_kills", "first_deaths", "source", "source_url",
        "fetched_at",
    ],
    "valorant_player_stats_summary": [
        "player_id", "player_slug", "player_name", "team_abbrev", "country",
        "agents", "rounds", "rating", "acs", "kd", "kast", "adr", "kpr",
        "apr", "fkpr", "fdpr", "hs_pct", "cl_pct", "clutches", "kmax",
        "kills", "deaths", "assists", "first_kills", "first_deaths", "source",
        "source_url", "fetched_at",
    ],
}

INT_COLUMNS = {
    "bo",
    "score1",
    "score2",
    "map_index",
    "team1_score",
    "team1_ct_score",
    "team1_t_score",
    "team2_score",
    "team2_ct_score",
    "team2_t_score",
    "kills",
    "deaths",
    "assists",
    "first_kills",
    "first_deaths",
    "rounds",
    "kmax",
}

DATETIME_COLUMNS = {
    "match_time",
    "match_time_utc",
    "fetched_at",
    "event_start_time",
    "event_end_time",
    "crawl_time",
    "avatar_checked_at",
}
DATE_COLUMNS = {"match_date"}
PRESERVE_IF_INCOMING_NULL_COLUMNS = {
    "avatar",
    "event_logo",
    "team_logo",
    "team1_logo",
    "team2_logo",
}


def quote_ident(name: str) -> str:
    return f"`{str(name).replace('`', '``')}`"


def table_exists(cur: pymysql.cursors.DictCursor, table_name: str) -> bool:
    cur.execute("SHOW TABLES LIKE %s", (table_name,))
    return cur.fetchone() is not None


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()


PLACEHOLDER_IMAGE_MARKERS = (
    "/img/vlr/tmp/vlr.png",
    "/img/base/ph/sil.png",
    "/null.png",
)


def is_placeholder_image(value: Any) -> bool:
    text = clean(value).lower()
    if not text:
        return False
    return any(marker in text for marker in PLACEHOLDER_IMAGE_MARKERS)


def clean_image_url(value: Any) -> str:
    text = clean(value)
    if not text or is_placeholder_image(text):
        return ""
    return text


def is_placeholder_map_name(value: Any) -> bool:
    return clean(value).lower() in {"", "-", "tbd"}


def is_placeholder_map_stat(row: Dict[str, Any]) -> bool:
    return (
        is_placeholder_map_name(row.get("map_name"))
        and row.get("team1_score") in {None, 0}
        and row.get("team2_score") in {None, 0}
        and not clean(row.get("winner"))
        and clean(row.get("duration")) in {"", "-"}
    )


def is_empty_placeholder_player_stat(row: Dict[str, Any]) -> bool:
    stat_columns = (
        "agents", "rating", "acs", "kills", "deaths", "assists", "kd_diff",
        "kast", "adr", "hs_pct", "first_kills", "first_deaths",
    )
    return is_placeholder_map_name(row.get("map_name")) and all(
        row.get(col) is None or clean(row.get(col)) == "" for col in stat_columns
    )


def is_real_team_name(value: Any) -> bool:
    text = clean(value).lower()
    return bool(text) and text not in {"-", "tbd", "tba", "tbc", "bye"}


def is_better_display_team_name(current: Any, candidate: Any) -> bool:
    old = clean(current)
    new = clean(candidate)
    if not is_real_team_name(new):
        return False
    if not old:
        return True
    if old.lower() == new.lower():
        return False
    old_lower = old.lower()
    new_lower = new.lower()
    if f"({new_lower})" in old_lower:
        return True
    if new_lower in old_lower and len(new) < len(old):
        return True
    return False


def team_names_match(left: Any, right: Any) -> bool:
    a = clean(left).lower()
    b = clean(right).lower()
    if not a or not b:
        return False
    return a == b or f"({a})" in b or f"({b})" in a


def slugify(value: Any) -> str:
    text = clean(value).lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text[:100] or "unknown"


def to_nullable(value: Any) -> Any:
    text = clean(value)
    if not text:
        return None
    return text


def to_int_or_none(value: Any) -> Optional[int]:
    text = clean(value)
    if not text:
        return None
    match = re.search(r"-?\d+", text)
    return int(match.group(0)) if match else None


def normalize_row(row: Dict[str, Any], columns: Sequence[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for col in columns:
        value = row.get(col)
        if col in INT_COLUMNS:
            out[col] = to_int_or_none(value)
        elif col in DATETIME_COLUMNS:
            out[col] = normalize_datetime(value)
        elif col in DATE_COLUMNS:
            out[col] = normalize_date(value)
        elif col.endswith("_logo") or col in {"avatar", "event_logo"}:
            out[col] = to_nullable(clean_image_url(value))
        else:
            out[col] = to_nullable(value)
    return out


def normalize_datetime(value: Any) -> Optional[str]:
    text = clean(value)
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    return None


def normalize_date(value: Any) -> Optional[str]:
    text = clean(value)
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


def region_from_text(value: Any) -> str:
    text = clean(value).upper()
    pacific_codes = {
        "AP", "APAC", "JP", "KR", "TH", "SG", "ID", "PH", "MY", "VN", "IN",
        "HK", "TW", "AU", "NZ", "BD", "PK", "LK",
    }
    americas_codes = {"US", "CA", "BR", "MX", "CL", "AR", "CO", "PE", "UY", "EC", "BO"}
    emea_codes = {
        "EU", "GB", "UK", "FR", "DE", "ES", "IT", "PL", "TR", "RU", "UA", "NL",
        "SE", "NO", "DK", "FI", "PT", "CZ", "SK", "RS", "RO", "GR", "HU", "AT",
        "CH", "BE", "IE", "IL", "SA", "AE", "EG", "MA", "DZ", "ZA",
    }
    if text in pacific_codes:
        return "Pacific"
    if text in americas_codes:
        return "Americas"
    if text in emea_codes:
        return "EMEA"
    if "CHINA" in text or "CN" in text:
        return "CN"
    if "PACIFIC" in text or "JAPAN" in text or "KOREA" in text or "SEA" in text:
        return "Pacific"
    if "EMEA" in text or "EUROPE" in text or "DACH" in text or "FRANCE" in text:
        return "EMEA"
    if "AMERICAS" in text or "NORTH AMERICA" in text or "BRAZIL" in text or "LATAM" in text:
        return "Americas"
    if "GAME CHANGERS" in text:
        return "Game Changers"
    return "International"


def tier_from_event(value: Any) -> str:
    text = clean(value).upper()
    if "CHAMPIONS" in text or "MASTERS" in text:
        return "S"
    if text.startswith("VCT ") or " VCT " in text:
        return "S"
    if "CHALLENGERS" in text:
        return "A"
    if "GAME CHANGERS" in text:
        return "A"
    return "B"


def get_connection(database: Optional[str] = None) -> pymysql.Connection:
    config = DB_CONFIG.copy()
    if database is None:
        config.pop("database", None)
    else:
        config["database"] = database
    return pymysql.connect(**config)


def create_database_and_tables() -> None:
    with get_connection(None) as conn:
        with conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS {quote_ident(DATABASE_NAME)} DEFAULT CHARSET utf8mb4;")
    with get_connection(DATABASE_NAME) as conn:
        with conn.cursor() as cur:
            for sql in TABLE_SQL.values():
                cur.execute(sql)
    print(f"[DONE] Database and Valorant tables ready: {DATABASE_NAME}")


def read_csv_rows(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        print(f"[WARN] missing CSV: {path}")
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return [dict(row) for row in csv.DictReader(f)]


def table_columns(table_name: str) -> List[str]:
    with get_connection(DATABASE_NAME) as conn:
        with conn.cursor() as cur:
            cur.execute(f"SHOW COLUMNS FROM {quote_ident(table_name)}")
            return [str(row["Field"]) for row in cur.fetchall()]


def truncate_tables(table_names: Iterable[str]) -> None:
    with get_connection(DATABASE_NAME) as conn:
        with conn.cursor() as cur:
            cur.execute("SET FOREIGN_KEY_CHECKS = 0")
            for table_name in table_names:
                cur.execute(f"TRUNCATE TABLE {quote_ident(table_name)}")
            cur.execute("SET FOREIGN_KEY_CHECKS = 1")


def insert_rows(table_name: str, rows: List[Dict[str, Any]], columns: Optional[Sequence[str]] = None) -> None:
    if not rows:
        print(f"[DONE] {table_name}: 0 rows imported")
        return
    available = set(table_columns(table_name))
    selected = list(columns or rows[0].keys())
    selected = [col for col in selected if col in available]
    if not selected:
        raise ValueError(f"No insertable columns for {table_name}")
    normalized = [normalize_row(row, selected) for row in rows]
    if table_name == "valorant_match_schedule":
        for row in normalized:
            if (
                clean(row.get("status")).lower() == "in_progress"
                and row.get("score1") is None
                and row.get("score2") is None
            ):
                row["status"] = "scheduled"
    elif table_name == "valorant_match_map_stats":
        normalized = [row for row in normalized if not is_placeholder_map_stat(row)]
    elif table_name == "valorant_match_player_stats":
        normalized = [row for row in normalized if not is_empty_placeholder_player_stat(row)]
    if not normalized:
        print(f"[DONE] {table_name}: 0 rows imported")
        return
    placeholders = ", ".join(["%s"] * len(selected))
    column_sql = ", ".join(quote_ident(col) for col in selected)
    update_parts = []
    for col in selected:
        if col == "id":
            continue
        if col in PRESERVE_IF_INCOMING_NULL_COLUMNS or col.endswith("_logo"):
            update_parts.append(f"{quote_ident(col)}=COALESCE(VALUES({quote_ident(col)}), {quote_ident(col)})")
        else:
            update_parts.append(f"{quote_ident(col)}=VALUES({quote_ident(col)})")
    update_sql = ", ".join(update_parts)
    sql = (
        f"INSERT INTO {quote_ident(table_name)} ({column_sql}) VALUES ({placeholders}) "
        f"ON DUPLICATE KEY UPDATE {update_sql}"
    )
    values = [tuple(row.get(col) for col in selected) for row in normalized]
    with get_connection(DATABASE_NAME) as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, values)
    print(f"[DONE] {table_name}: {len(rows)} rows imported")


def purge_completed_schedule_rows() -> None:
    with get_connection(DATABASE_NAME) as conn:
        with conn.cursor() as cur:
            if not table_exists(cur, "valorant_match_schedule"):
                return
            migrated_from_detail = 0
            deleted_by_result = 0
            deleted_by_detail = 0
            has_result_table = table_exists(cur, "valorant_match_result")
            has_detail_table = table_exists(cur, "valorant_match_detail")
            if has_result_table and has_detail_table:
                cur.execute(
                    """
                    INSERT INTO valorant_match_result (
                        match_id, slug, match_url, match_date, match_time, time_text,
                        event_id, event_name, stage, bo,
                        team1_id, team1, team1_country, team1_logo,
                        team2_id, team2, team2_country, team2_logo,
                        score1, score2, winner, status, note, source,
                        source_list_url, fetched_at
                    )
                    SELECT
                        s.match_id, s.slug, s.match_url, s.match_date, s.match_time, s.time_text,
                        COALESCE(s.event_id, d.event_id), COALESCE(s.event_name, d.event_name),
                        COALESCE(s.stage, d.stage), COALESCE(s.bo, d.bo),
                        COALESCE(s.team1_id, d.team1_id), COALESCE(s.team1, d.team1),
                        s.team1_country, COALESCE(s.team1_logo, d.team1_logo),
                        COALESCE(s.team2_id, d.team2_id), COALESCE(s.team2, d.team2),
                        s.team2_country, COALESCE(s.team2_logo, d.team2_logo),
                        COALESCE(d.score1, s.score1), COALESCE(d.score2, s.score2),
                        CASE
                            WHEN d.score1 IS NULL OR d.score2 IS NULL THEN s.winner
                            WHEN d.score1 > d.score2 THEN COALESCE(NULLIF(d.team1, ''), s.team1)
                            WHEN d.score2 > d.score1 THEN COALESCE(NULLIF(d.team2, ''), s.team2)
                            ELSE s.winner
                        END,
                        'completed',
                        s.note,
                        COALESCE(s.source, d.source),
                        COALESCE(s.source_list_url, d.source_url, s.match_url),
                        COALESCE(d.fetched_at, s.fetched_at)
                    FROM valorant_match_schedule s
                    JOIN valorant_match_detail d ON d.match_id = s.match_id
                    LEFT JOIN valorant_match_result r ON r.match_id = s.match_id
                    WHERE r.match_id IS NULL
                      AND LOWER(COALESCE(d.status, '')) IN ('completed', 'finished')
                    ON DUPLICATE KEY UPDATE
                        score1 = COALESCE(VALUES(score1), valorant_match_result.score1),
                        score2 = COALESCE(VALUES(score2), valorant_match_result.score2),
                        winner = COALESCE(VALUES(winner), valorant_match_result.winner),
                        status = VALUES(status),
                        fetched_at = COALESCE(VALUES(fetched_at), valorant_match_result.fetched_at)
                    """
                )
                migrated_from_detail = cur.rowcount
            if has_result_table:
                cur.execute(
                    """
                    DELETE s
                    FROM valorant_match_schedule s
                    JOIN valorant_match_result r ON r.match_id = s.match_id
                    WHERE LOWER(COALESCE(r.status, 'completed')) IN ('completed', 'finished', '')
                    """
                )
                deleted_by_result = cur.rowcount
            if has_detail_table:
                cur.execute(
                    """
                    DELETE s
                    FROM valorant_match_schedule s
                    JOIN valorant_match_detail d ON d.match_id = s.match_id
                    WHERE LOWER(COALESCE(d.status, '')) IN ('completed', 'finished')
                    """
                )
                deleted_by_detail = cur.rowcount
    print(
        "[DONE] valorant_match_schedule purge: "
        f"migrated_from_detail={migrated_from_detail}, "
        f"deleted_by_result={deleted_by_result}, deleted_by_detail={deleted_by_detail}"
    )


def sync_schedule_completion_from_results() -> None:
    purge_completed_schedule_rows()


def purge_placeholder_map_rows() -> None:
    with get_connection(DATABASE_NAME) as conn:
        with conn.cursor() as cur:
            if not table_exists(cur, "valorant_match_map_stats"):
                return
            deleted_player_rows = 0
            deleted_orphan_player_rows = 0
            deleted_map_rows = 0
            placeholder_map_where = """
                LOWER(COALESCE(ms.map_name, '')) IN ('', '-', 'tbd')
                AND COALESCE(ms.team1_score, 0) = 0
                AND COALESCE(ms.team2_score, 0) = 0
                AND COALESCE(ms.winner, '') = ''
                AND COALESCE(ms.duration, '') IN ('', '-')
            """
            if table_exists(cur, "valorant_match_player_stats"):
                cur.execute(
                    f"""
                    DELETE ps
                    FROM valorant_match_player_stats ps
                    JOIN valorant_match_map_stats ms
                      ON ms.match_id = ps.match_id
                     AND ms.game_id = ps.game_id
                    WHERE {placeholder_map_where}
                    """
                )
                deleted_player_rows = cur.rowcount
                cur.execute(
                    """
                    DELETE FROM valorant_match_player_stats
                    WHERE LOWER(COALESCE(map_name, '')) IN ('', '-', 'tbd')
                      AND COALESCE(agents, '') = ''
                      AND COALESCE(rating, '') = ''
                      AND COALESCE(acs, '') = ''
                      AND kills IS NULL
                      AND deaths IS NULL
                      AND assists IS NULL
                      AND COALESCE(kd_diff, '') = ''
                      AND COALESCE(kast, '') = ''
                      AND COALESCE(adr, '') = ''
                      AND COALESCE(hs_pct, '') = ''
                      AND first_kills IS NULL
                      AND first_deaths IS NULL
                    """
                )
                deleted_orphan_player_rows = cur.rowcount
            cur.execute(
                f"""
                DELETE ms
                FROM valorant_match_map_stats ms
                WHERE {placeholder_map_where}
                """
            )
            deleted_map_rows = cur.rowcount
    print(
        "[DONE] valorant placeholder maps purge: "
        f"map_rows={deleted_map_rows}, player_rows={deleted_player_rows}, "
        f"orphan_player_rows={deleted_orphan_player_rows}"
    )


def load_source_rows() -> Dict[str, List[Dict[str, Any]]]:
    return {key: read_csv_rows(path) for key, path in CSV_FILES.items()}


def derive_events(rows_by_name: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    events: Dict[str, Dict[str, Any]] = {}
    for source_name in ("schedule", "result", "detail"):
        for row in rows_by_name.get(source_name, []):
            event_name = clean(row.get("event_name"))
            if not event_name:
                continue
            event_id = clean(row.get("event_id")) or f"vlr_event_{slugify(event_name)}"
            current = events.setdefault(
                event_id,
                {
                    "event_id": event_id,
                    "event_slug": clean(row.get("event_slug")) or slugify(event_name),
                    "event_name": event_name,
                    "region": region_from_text(event_name),
                    "tier": tier_from_event(event_name),
                    "event_logo": "",
                    "event_start_time": None,
                    "event_end_time": None,
                    "source": "vlr_experiment",
                    "source_event_url": f"https://www.vlr.gg/event/{event_id}/{clean(row.get('event_slug'))}" if clean(row.get("event_slug")) and clean(row.get("event_id")) else "",
                    "fetched_at": clean(row.get("fetched_at")),
                },
            )
            ts = normalize_datetime(row.get("match_time_utc") or row.get("match_time"))
            if ts and (not current.get("event_start_time") or ts < str(current["event_start_time"])):
                current["event_start_time"] = ts
            if ts and (not current.get("event_end_time") or ts > str(current["event_end_time"])):
                current["event_end_time"] = ts
    return list(events.values())


def upsert_team(team_map: Dict[str, Dict[str, Any]], row: Dict[str, Any]) -> None:
    team_name = clean(row.get("team_name"))
    if not is_real_team_name(team_name):
        return
    requested_team_id = clean(row.get("team_id"))
    team_id = requested_team_id or f"vlr_team_{slugify(team_name)}"
    existing_key = requested_team_id if requested_team_id in team_map else ""
    if not existing_key:
        existing_key = next(
            (
                key
                for key, item in team_map.items()
                if team_names_match(item.get("team_name"), team_name)
            ),
            "",
        )
    if existing_key:
        if requested_team_id and existing_key != requested_team_id and existing_key.startswith("vlr_team_"):
            current = team_map.pop(existing_key)
            current["team_id"] = requested_team_id
            team_map[requested_team_id] = current
            team_id = requested_team_id
        else:
            team_id = existing_key
    current = team_map.setdefault(
        team_id,
        {
            "team_id": team_id,
            "team_slug": clean(row.get("team_slug")) or slugify(team_name),
            "team_name": team_name,
            "country": clean(row.get("country")),
            "region": clean(row.get("region")) or region_from_text(row.get("country") or team_name),
            "team_logo": "",
            "source": "vlr_experiment",
            "source_team_url": "",
            "fetched_at": clean(row.get("fetched_at")),
        },
    )
    if is_better_display_team_name(current.get("team_name"), team_name):
        current["team_name"] = team_name
        current["team_slug"] = clean(row.get("team_slug")) or slugify(team_name)
    for key in ("team_slug", "country", "region", "team_logo", "source_team_url", "fetched_at"):
        value = clean_image_url(row.get(key)) if key == "team_logo" else clean(row.get(key))
        if value and not clean(current.get(key)):
            current[key] = value
    country_region = region_from_text(current.get("country"))
    if country_region != "International" and clean(current.get("region")) in {"", "International"}:
        current["region"] = country_region


def derive_teams(rows_by_name: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    teams: Dict[str, Dict[str, Any]] = {}
    for row in rows_by_name.get("detail", []):
        for side in ("team1", "team2"):
            team_id = clean(row.get(f"{side}_id"))
            team_name = clean(row.get(side))
            upsert_team(
                teams,
                {
                    "team_id": team_id,
                    "team_slug": slugify(team_name),
                    "team_name": team_name,
                    "team_logo": clean_image_url(row.get(f"{side}_logo")),
                    "source_team_url": f"https://www.vlr.gg/team/{team_id}/{slugify(team_name)}" if team_id else "",
                    "fetched_at": row.get("fetched_at"),
                },
            )
    for source_name in ("schedule", "result"):
        for row in rows_by_name.get(source_name, []):
            for side in ("team1", "team2"):
                team_name = clean(row.get(side))
                team_id = clean(row.get(f"{side}_id"))
                upsert_team(
                    teams,
                    {
                        "team_id": team_id,
                        "team_slug": slugify(team_name),
                        "team_name": team_name,
                        "country": clean(row.get(f"{side}_country")),
                        "team_logo": clean_image_url(row.get(f"{side}_logo")),
                        "source_team_url": f"https://www.vlr.gg/team/{team_id}/{slugify(team_name)}" if team_id else "",
                        "fetched_at": row.get("fetched_at"),
                    },
                )
    return list(teams.values())


def match_team_rows(rows_by_name: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    rows: Dict[str, Dict[str, Any]] = {}
    for source_name in ("detail", "result", "schedule"):
        for row in rows_by_name.get(source_name, []):
            match_id = clean(row.get("match_id"))
            if not match_id:
                continue
            current = rows.setdefault(match_id, {})
            for key in ("team1_id", "team1", "team1_logo", "team2_id", "team2", "team2_logo"):
                value = clean(row.get(key))
                if key.endswith("_logo"):
                    value = clean_image_url(value)
                if key in {"team1", "team2"} and is_better_display_team_name(current.get(key), value):
                    current[key] = value
                elif value and not clean(current.get(key)):
                    current[key] = value
    return rows


def normalize_team_display_names(rows_by_name: Dict[str, List[Dict[str, Any]]]) -> None:
    list_names: Dict[Tuple[str, str], str] = {}
    for source_name in ("result", "schedule"):
        for row in rows_by_name.get(source_name, []):
            match_id = clean(row.get("match_id"))
            if not match_id:
                continue
            for side in ("team1", "team2"):
                value = clean(row.get(side))
                if is_real_team_name(value):
                    list_names[(match_id, side)] = value

    for source_name in ("detail", "result", "schedule"):
        for row in rows_by_name.get(source_name, []):
            for side in ("team1", "team2"):
                logo_key = f"{side}_logo"
                if logo_key in row:
                    row[logo_key] = clean_image_url(row.get(logo_key))

    for row in rows_by_name.get("detail", []):
        match_id = clean(row.get("match_id"))
        if not match_id:
            continue
        for side in ("team1", "team2"):
            display = list_names.get((match_id, side))
            if is_better_display_team_name(row.get(side), display):
                row[side] = display


def derive_team_abbrev_lookup(rows_by_name: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    match_rows = match_team_rows(rows_by_name)
    grouped: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for row in rows_by_name.get("player_stats", []):
        match_id = clean(row.get("match_id"))
        if not match_id:
            continue
        map_key = clean(row.get("map_index")) or clean(row.get("game_id")) or "match"
        grouped.setdefault((match_id, map_key), []).append(row)

    lookup: Dict[str, Dict[str, Any]] = {}
    for (match_id, _map_key), rows in grouped.items():
        match = match_rows.get(match_id) or {}
        if not match:
            continue
        abbrevs: List[str] = []
        for row in rows:
            abbrev = clean(row.get("team_abbrev"))
            if abbrev and abbrev not in abbrevs:
                abbrevs.append(abbrev)
            if len(abbrevs) >= 2:
                break
        for idx, abbrev in enumerate(abbrevs[:2], start=1):
            team_name = clean(match.get(f"team{idx}"))
            if not team_name:
                continue
            team_id = clean(match.get(f"team{idx}_id")) or f"vlr_team_{slugify(team_name)}"
            current = lookup.setdefault(
                abbrev,
                {
                    "team_id": team_id,
                    "team_name": team_name,
                    "team_logo": clean_image_url(match.get(f"team{idx}_logo")),
                },
            )
            for key, value in (
                ("team_id", team_id),
                ("team_name", team_name),
                ("team_logo", clean_image_url(match.get(f"team{idx}_logo"))),
            ):
                if value and not clean(current.get(key)):
                    current[key] = value
    return lookup


def derive_players(
    rows_by_name: Dict[str, List[Dict[str, Any]]],
    team_abbrev_lookup: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    players: Dict[str, Dict[str, Any]] = {}
    for source_name in ("player_stats", "player_summary"):
        for row in rows_by_name.get(source_name, []):
            player_name = clean(row.get("player_name"))
            if not player_name:
                continue
            player_id = clean(row.get("player_id")) or f"vlr_player_{slugify(player_name)}"
            team_abbrev = clean(row.get("team_abbrev"))
            team_info = team_abbrev_lookup.get(team_abbrev, {})
            current = players.setdefault(
                player_id,
                {
                    "player_id": player_id,
                    "player_slug": clean(row.get("player_slug")) or slugify(player_name),
                    "player_name": player_name,
                    "country": clean(row.get("country")),
                    "current_team_abbrev": team_abbrev,
                    "current_team_name": clean(team_info.get("team_name")),
                    "agents": clean(row.get("agents")),
                    "avatar": "",
                    "source": "vlr_experiment",
                    "source_player_url": f"https://www.vlr.gg/player/{player_id}/{clean(row.get('player_slug')) or slugify(player_name)}",
                    "fetched_at": clean(row.get("fetched_at")),
                },
            )
            for key in ("country", "current_team_abbrev", "agents", "fetched_at"):
                if clean(row.get(key)) and not clean(current.get(key)):
                    current[key] = clean(row.get(key))
            if clean(team_info.get("team_name")) and not clean(current.get("current_team_name")):
                current["current_team_name"] = clean(team_info.get("team_name"))
    for row in rows_by_name.get("player_profile", []):
        player_name = clean(row.get("player_name"))
        player_id = clean(row.get("player_id")) or f"vlr_player_{slugify(player_name)}"
        if not player_id:
            continue
        current = players.setdefault(
            player_id,
            {
                "player_id": player_id,
                "player_slug": clean(row.get("player_slug")) or slugify(player_name),
                "player_name": player_name,
                "country": clean(row.get("country")),
                "current_team_abbrev": "",
                "current_team_name": "",
                "agents": "",
                "avatar": clean(row.get("avatar")),
                "source": "vlr_experiment",
                "source_player_url": clean(row.get("source_player_url")),
                "fetched_at": clean(row.get("fetched_at")),
            },
        )
        for key in ("player_slug", "player_name", "country", "avatar", "source_player_url", "fetched_at"):
            if clean(row.get(key)) and not clean(current.get(key)):
                current[key] = clean(row.get(key))
    return list(players.values())


def derive_relations(
    rows_by_name: Dict[str, List[Dict[str, Any]]],
    team_abbrev_lookup: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    relations: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in [*rows_by_name.get("player_summary", []), *rows_by_name.get("player_stats", [])]:
        player_id = clean(row.get("player_id"))
        player_name = clean(row.get("player_name"))
        team_abbrev = clean(row.get("team_abbrev"))
        if not player_id or not team_abbrev:
            continue
        team_info = team_abbrev_lookup.get(team_abbrev, {})
        team_id = clean(team_info.get("team_id")) or f"vlr_team_abbrev_{slugify(team_abbrev)}"
        team_name = clean(team_info.get("team_name"))
        key = (team_abbrev, player_id)
        relations[key] = {
            "team_id": team_id,
            "team_name": team_name,
            "team_abbrev": team_abbrev,
            "player_id": player_id,
            "player_name": player_name,
            "is_active": 1,
            "source": "vlr_experiment",
            "fetched_at": clean(row.get("fetched_at")),
        }
    return list(relations.values())


def derive_agent_rows(rows_by_name: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in rows_by_name.get("player_summary", []):
        player_id = clean(row.get("player_id"))
        if not player_id:
            continue
        for agent in clean(row.get("agents")).split("|"):
            agent = clean(agent)
            if not agent:
                continue
            out[(player_id, agent)] = {
                "player_id": player_id,
                "player_name": clean(row.get("player_name")),
                "team_abbrev": clean(row.get("team_abbrev")),
                "agent": agent,
                "source": "vlr_experiment",
                "fetched_at": clean(row.get("fetched_at")),
            }
    return list(out.values())


def enrich_match_tables(rows_by_name: Dict[str, List[Dict[str, Any]]]) -> None:
    detail_by_match = {clean(row.get("match_id")): row for row in rows_by_name.get("detail", [])}
    for source_name in ("schedule", "result"):
        for row in rows_by_name.get(source_name, []):
            detail = detail_by_match.get(clean(row.get("match_id"))) or {}
            if detail:
                row["event_id"] = clean(row.get("event_id")) or clean(detail.get("event_id"))
                row["bo"] = clean(row.get("bo")) or clean(detail.get("bo"))
                row["team1_id"] = clean(row.get("team1_id")) or clean(detail.get("team1_id"))
                row["team1_logo"] = clean_image_url(row.get("team1_logo")) or clean_image_url(detail.get("team1_logo"))
                row["team2_id"] = clean(row.get("team2_id")) or clean(detail.get("team2_id"))
                row["team2_logo"] = clean_image_url(row.get("team2_logo")) or clean_image_url(detail.get("team2_logo"))
            if not clean(row.get("event_id")) and clean(row.get("event_name")):
                row["event_id"] = f"vlr_event_{slugify(row.get('event_name'))}"


def verify_csv_files() -> None:
    required = {key: path for key, path in CSV_FILES.items() if key != "player_profile"}
    missing = [str(path) for path in required.values() if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "Missing Valorant CSV files. Run backend/valorant/vlr_experiment.py first:\n"
            + "\n".join(missing)
        )


def main() -> int:
    verify_csv_files()
    create_database_and_tables()
    rows_by_name = load_source_rows()
    enrich_match_tables(rows_by_name)
    normalize_team_display_names(rows_by_name)
    team_abbrev_lookup = derive_team_abbrev_lookup(rows_by_name)

    truncate_tables(TABLE_SQL.keys())

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
    insert_rows(
        "valorant_team_player_relation",
        derive_relations(rows_by_name, team_abbrev_lookup),
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
    print("[DONE] Valorant CSV import finished.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
