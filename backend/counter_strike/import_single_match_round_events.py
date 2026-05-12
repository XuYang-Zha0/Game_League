from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = BASE_DIR.parent
ENV_FILE = BACKEND_DIR / ".env"
TARGET_MATCH_ID = "csgo_mc_2393350"
TARGET_EVENT_LIMIT = 1000


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file(ENV_FILE)
sys.path.insert(0, str(BASE_DIR))

import pymysql

from match_result_detail import DB_CONFIG, EVENT_LOG_URL, fetch_json, normalize_text, to_int

EVENT_TYPE_NAMES = {
    "1": "round_start",
    "2": "round_end",
    "3": "player_join",
    "4": "player_quit",
    "6": "bomb_planted",
    "8": "kill",
    "10": "match_started",
}

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS match_result_round_events (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    match_id VARCHAR(50) NOT NULL,
    event_id VARCHAR(50),
    event_name VARCHAR(255),
    bout_id VARCHAR(80),
    map_index INT,
    map_name VARCHAR(100),
    source_map_name VARCHAR(100),
    round_number INT,
    round_global_index INT,
    event_type VARCHAR(32),
    event_type_code VARCHAR(16),
    update_version VARCHAR(64),
    source_order INT,
    team_side VARCHAR(32),
    team_name VARCHAR(100),
    player_id VARCHAR(50),
    player_name VARCHAR(100),
    related_player_id VARCHAR(50),
    related_player_name VARCHAR(100),
    weapon VARCHAR(100),
    weapon_logo VARCHAR(255),
    bomb_site VARCHAR(16),
    winner_side VARCHAR(32),
    win_type VARCHAR(64),
    score_ct INT,
    score_t INT,
    event_text VARCHAR(500),
    raw_event JSON,
    fetched_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_match_update_source (match_id, update_version, source_order),
    KEY idx_match_map_round (match_id, map_index, round_number),
    KEY idx_match_event_type (match_id, event_type),
    KEY idx_player_id (player_id),
    KEY idx_related_player_id (related_player_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


def get_conn():
    return pymysql.connect(**DB_CONFIG)


def load_target_match(cur) -> Dict[str, Any]:
    cur.execute(
        """
        SELECT match_id, match_time, bo, team1_id, team1, team2_id, team2,
               event_id, event_name, score1, score2, status, bout_count, bout_details
        FROM match_result
        WHERE match_id = %s
        """,
        (TARGET_MATCH_ID,),
    )
    row = cur.fetchone()
    if not row:
        raise RuntimeError(f"未找到目标比赛 {TARGET_MATCH_ID}")
    if normalize_text(row.get("team1")) != "NAVI" or normalize_text(row.get("team2")) != "Vitality":
        raise RuntimeError(f"目标比赛队伍不符合预期：{row.get('team1')} vs {row.get('team2')}")
    if to_int(row.get("score1")) != 0 or to_int(row.get("score2")) != 3:
        raise RuntimeError(f"目标比赛比分不符合预期：{row.get('score1')}:{row.get('score2')}")
    return row


def parse_log_info(item: Dict[str, Any]) -> Dict[str, Any]:
    raw = item.get("log_info")
    if isinstance(raw, dict):
        return raw
    try:
        payload = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def safe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def normalize_map_name(value: Any) -> str:
    text = normalize_text(value)
    if text.lower().startswith("de_"):
        text = text[3:]
    return text[:1].upper() + text[1:] if text else ""


def int_update_version(item: Dict[str, Any]) -> int:
    value = normalize_text(item.get("update_version"))
    try:
        return int(value)
    except ValueError:
        return 0


def collect_event_rows(match: Dict[str, Any], event_items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sorted_items = sorted(
        enumerate(event_items),
        key=lambda pair: (to_int(pair[1].get("bout_num")) or 0, int_update_version(pair[1]), pair[0]),
    )
    current_round_by_map: Dict[int, Optional[int]] = {}
    max_round_by_map: Dict[int, int] = {}
    round_global_lookup: Dict[Tuple[int, int], int] = {}
    rows: List[Dict[str, Any]] = []

    for source_order, item in sorted_items:
        if not isinstance(item, dict):
            continue
        info = parse_log_info(item)
        event_type_code = normalize_text(info.get("type"))
        event_type = EVENT_TYPE_NAMES.get(event_type_code, f"type_{event_type_code}" if event_type_code else "unknown")
        map_index = to_int(item.get("bout_num")) or to_int(info.get("round_start", {}).get("bout_num"))
        source_map_name = normalize_text(item.get("map_name")) or normalize_text(info.get("round_start", {}).get("map"))
        map_name = normalize_map_name(source_map_name)

        round_start = info.get("round_start") if isinstance(info.get("round_start"), dict) else {}
        round_end = info.get("round_end") if isinstance(info.get("round_end"), dict) else {}
        kill = info.get("kill") if isinstance(info.get("kill"), dict) else {}
        bomb_planted = info.get("bomb_planted") if isinstance(info.get("bomb_planted"), dict) else {}
        bomb_defused = info.get("bomb_defused") if isinstance(info.get("bomb_defused"), dict) else {}
        player_join = info.get("player_join") if isinstance(info.get("player_join"), dict) else {}
        player_quit = info.get("player_quit") if isinstance(info.get("player_quit"), dict) else {}
        assist = info.get("assist") if isinstance(info.get("assist"), dict) else {}
        match_started = info.get("match_started") if isinstance(info.get("match_started"), dict) else {}
        suicide = info.get("suicide") if isinstance(info.get("suicide"), dict) else {}

        if not map_name:
            map_name = normalize_map_name(match_started.get("map_name"))

        round_number = to_int(round_start.get("round_num"))
        if round_number is not None and map_index is not None:
            known_max = max_round_by_map.get(map_index, 0)
            if event_type == "round_start" and round_number <= known_max:
                round_number = known_max + 1
            current_round_by_map[map_index] = round_number
            max_round_by_map[map_index] = max(known_max, round_number)
        elif map_index is not None:
            round_number = current_round_by_map.get(map_index)

        score_ct = to_int(round_end.get("ct_score"))
        score_t = to_int(round_end.get("t_score"))
        if event_type == "round_end" and map_index is not None:
            score_round = (score_ct or 0) + (score_t or 0) if score_ct is not None or score_t is not None else None
            if score_round:
                round_number = score_round
            elif round_number is None:
                inferred = max_round_by_map.get(map_index)
                round_number = inferred or None
            if round_number is not None:
                current_round_by_map[map_index] = round_number
                max_round_by_map[map_index] = max(max_round_by_map.get(map_index, 0), round_number)

        if map_index is not None and round_number is not None:
            round_global_lookup.setdefault((map_index, round_number), len(round_global_lookup) + 1)
        round_global_index = round_global_lookup.get((map_index, round_number)) if map_index is not None and round_number is not None else None

        player_id = ""
        player_name = ""
        related_player_id = ""
        related_player_name = ""
        team_side = ""
        weapon = ""
        weapon_logo = ""
        bomb_site = ""
        team_name = ""
        event_text = event_type

        if event_type == "kill":
            player_id = normalize_text(kill.get("killer_id"))
            player_name = normalize_text(kill.get("killer_nick")) or normalize_text(kill.get("killer_name"))
            related_player_id = normalize_text(kill.get("victim_id"))
            related_player_name = normalize_text(kill.get("victim_nick")) or normalize_text(kill.get("victim_name"))
            team_side = normalize_text(kill.get("killer_side"))
            weapon = normalize_text(kill.get("weapon"))
            weapon_logo = normalize_text(kill.get("weapon_logo"))
            event_text = f"{player_name or '-'} 击杀 {related_player_name or '-'}"
        elif event_type == "bomb_planted":
            player_name = normalize_text(bomb_planted.get("player_nick")) or normalize_text(bomb_planted.get("player_name"))
            bomb_site = normalize_text(bomb_planted.get("bomb_site"))
            event_text = f"{player_name or '-'} 安放炸弹 {bomb_site or ''}".strip()
        elif event_type == "round_end":
            team_side = normalize_text(round_end.get("winner"))
            event_text = f"回合结束 {score_ct if score_ct is not None else '-'}:{score_t if score_t is not None else '-'} {normalize_text(round_end.get('win_type'))}"
        elif event_type == "round_start":
            event_text = f"第 {round_number or '-'} 回合开始"
        elif event_type == "player_join":
            player_name = normalize_text(player_join.get("player_nick")) or normalize_text(player_join.get("player_name"))
            event_text = f"{player_name or '-'} 加入比赛"
        elif event_type == "player_quit":
            player_name = normalize_text(player_quit.get("player_nick")) or normalize_text(player_quit.get("player_name"))
            team_side = normalize_text(player_quit.get("player_side"))
            event_text = f"{player_name or '-'} 离开比赛"
        elif event_type == "match_started":
            event_text = "地图开始"
        elif normalize_text(bomb_defused.get("player_name")):
            player_name = normalize_text(bomb_defused.get("player_nick")) or normalize_text(bomb_defused.get("player_name"))
            event_text = f"{player_name} 拆除炸弹"
        elif normalize_text(suicide.get("player_name")):
            player_name = normalize_text(suicide.get("player_nick")) or normalize_text(suicide.get("player_name"))
            team_side = normalize_text(suicide.get("side"))
            weapon = normalize_text(suicide.get("weapon"))
            weapon_logo = normalize_text(suicide.get("weapon_logo"))
            event_text = f"{player_name} 自杀"

        if not related_player_name and normalize_text(assist.get("assister_name")):
            related_player_name = normalize_text(assist.get("assister_nick")) or normalize_text(assist.get("assister_name"))

        rows.append(
            {
                "match_id": TARGET_MATCH_ID,
                "event_id": normalize_text(match.get("event_id")),
                "event_name": normalize_text(match.get("event_name")),
                "bout_id": normalize_text(item.get("bout_id")),
                "map_index": map_index,
                "map_name": map_name,
                "source_map_name": source_map_name,
                "round_number": round_number,
                "round_global_index": round_global_index,
                "event_type": event_type,
                "event_type_code": event_type_code,
                "update_version": normalize_text(item.get("update_version")),
                "source_order": source_order,
                "team_side": team_side,
                "team_name": team_name,
                "player_id": player_id,
                "player_name": player_name,
                "related_player_id": related_player_id,
                "related_player_name": related_player_name,
                "weapon": weapon,
                "weapon_logo": weapon_logo,
                "bomb_site": bomb_site,
                "winner_side": normalize_text(round_end.get("winner")),
                "win_type": normalize_text(round_end.get("win_type")),
                "score_ct": score_ct,
                "score_t": score_t,
                "event_text": event_text,
                "raw_event": safe_json({"source": item, "log_info": info}),
                "fetched_at": fetched_at,
            }
        )

    return rows


def fetch_event_items() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    url = EVENT_LOG_URL.format(match_id=TARGET_MATCH_ID).replace("limit=500", f"limit={TARGET_EVENT_LIMIT}")
    resp = fetch_json(url)
    if not resp.get("success"):
        raise RuntimeError(f"event log 请求失败：status={resp.get('status_code')}")
    data = resp.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("event log 返回 data 不是对象")
    items = data.get("list")
    if not isinstance(items, list):
        raise RuntimeError("event log 返回 list 不是数组")
    return [item for item in items if isinstance(item, dict)], data


def insert_rows(cur, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        return
    columns = [
        "match_id",
        "event_id",
        "event_name",
        "bout_id",
        "map_index",
        "map_name",
        "source_map_name",
        "round_number",
        "round_global_index",
        "event_type",
        "event_type_code",
        "update_version",
        "source_order",
        "team_side",
        "team_name",
        "player_id",
        "player_name",
        "related_player_id",
        "related_player_name",
        "weapon",
        "weapon_logo",
        "bomb_site",
        "winner_side",
        "win_type",
        "score_ct",
        "score_t",
        "event_text",
        "raw_event",
        "fetched_at",
    ]
    placeholders = ", ".join(["%s"] * len(columns))
    sql = f"""
        INSERT INTO match_result_round_events ({", ".join(columns)})
        VALUES ({placeholders})
    """
    cur.executemany(sql, [[row.get(column) for column in columns] for row in rows])


def summarize(cur) -> None:
    cur.execute("SELECT COUNT(*) AS count FROM match_result_round_events WHERE match_id = %s", (TARGET_MATCH_ID,))
    print("inserted_events=", cur.fetchone()["count"])
    cur.execute(
        """
        SELECT map_index, map_name, COUNT(*) AS event_count,
               COUNT(DISTINCT round_number) AS round_count,
               SUM(event_type = 'round_end') AS round_end_count
        FROM match_result_round_events
        WHERE match_id = %s
        GROUP BY map_index, map_name
        ORDER BY map_index, map_name
        """,
        (TARGET_MATCH_ID,),
    )
    for row in cur.fetchall():
        print("map_summary=", row)
    cur.execute(
        """
        SELECT event_type, COUNT(*) AS event_count
        FROM match_result_round_events
        WHERE match_id = %s
        GROUP BY event_type
        ORDER BY event_count DESC
        """,
        (TARGET_MATCH_ID,),
    )
    for row in cur.fetchall():
        print("type_summary=", row)


def main() -> None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            match = load_target_match(cur)
            print(
                "target_match=",
                match["match_id"],
                match["match_time"],
                f"{match['team1']} {match['score1']}:{match['score2']} {match['team2']}",
                match["event_name"],
            )
            event_items, payload = fetch_event_items()
            print(
                "event_log=",
                f"items={len(event_items)}",
                f"from_ver={payload.get('from_ver')}",
                f"to_ver={payload.get('to_ver')}",
                f"not_more={payload.get('not_more')}",
            )
            rows = collect_event_rows(match, event_items)
            cur.execute(TABLE_SQL)
            cur.execute("DELETE FROM match_result_round_events WHERE match_id = %s", (TARGET_MATCH_ID,))
            insert_rows(cur, rows)
            summarize(cur)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
