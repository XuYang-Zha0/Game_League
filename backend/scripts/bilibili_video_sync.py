#!/usr/bin/env python3
"""
Bilibili CSGO 官方赛事视频同步脚本

从 CSGO官方赛事 (UID: 474595627) 拉取最新视频列表，
获取每个视频的分 P 信息，匹配数据库比赛，输出完整官方回放索引。

用法：
  # 输出到 stdout
  python bilibili_video_sync.py

  # 写入 .env（更新 CS_BILIBILI_OFFICIAL_VIDEO_INDEX）
  python bilibili_video_sync.py --write-env

  # 仅检查有更新的视频
  python bilibili_video_sync.py --check

工作流程：
  1. 建立 requests 会话并预热（获取 nav / cookie）
  2. 按赛事关键词搜索 B 站视频
  3. 过滤 CSGO官方赛事 的视频
  4. 对每个视频调用 /x/web-interface/view 获取分 P 信息
  5. 按日期 + 赛事名 + 战队匹配数据库比赛
  6. 输出完整 JSON 索引
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import quote

import requests
import urllib3

urllib3.disable_warnings()


def load_local_env() -> None:
    """加载 backend/.env 文件中的环境变量"""
    env_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", ".env")
    )
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as env_file:
        for line in env_file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


load_local_env()

# ── 配置 ──────────────────────────────────────────────────

UPLOADER_UID = "474595627"
UPLOADER_NAME = "CSGO官方赛事"
UPLOADER_URL = f"https://space.bilibili.com/{UPLOADER_UID}/video"

# 本地 BV 缓存文件（搜索 API 被 412 时回退到此）
BV_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bilibili_bv_cache.json")

# 赛事搜索关键词 → 对应的数据库赛事名
TOURNAMENT_SEARCH_KEYWORDS: Dict[str, List[str]] = {
    "2026PGL阿斯塔纳": ["PGL 阿斯塔纳 2026", "PGL Astana 2026"],
    "2026IEM亚特兰大": ["IEM 亚特兰大 2026", "IEM Atlanta 2026"],
    "IEM亚特兰大": ["IEM 亚特兰大 2026", "IEM Atlanta 2026"],
    "IEM Atlanta 2026": ["IEM 亚特兰大 2026", "IEM Atlanta 2026"],
    "2026BLAST": [],
    "2026IEM": [],
}

# 队名别名（视频标题缩写 → 数据库全名）
TEAM_NAME_ALIASES: Dict[str, List[str]] = {
    "pv": ["parivision"],
    "parivision": ["pv"],
    "magic": ["magic"],
    "gentle mates": ["gentle mates"],
    "fisher college": ["fisher college"],
    "the huns": ["the huns"],
    "mongolz": ["mongolz"],
    "k27": ["k27"],
}

# 数据库配置（从环境变量读取，与主服务一致）
DB_CONFIG = {
    "host": os.getenv("CS_DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("CS_DB_PORT", "3306")),
    "user": os.getenv("CS_DB_USER", "root"),
    "password": os.getenv("CS_DB_PASSWORD", ""),
    "database": os.getenv("CS_DB_NAME", "esports"),
    "charset": "utf8mb4",
}

SEARCH_PAGE_SIZE = 50
SEARCH_DELAY = 0.4   # 搜索分页间隔（秒）
VIDEO_DETAIL_DELAY = 0.25  # 视频详情请求间隔（秒）
REQUEST_TIMEOUT = 20

# ── 工具函数 ──────────────────────────────────────────────

def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def build_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://www.bilibili.com",
        "Origin": "https://www.bilibili.com",
    })
    s.verify = False
    return s


def warmup_session(session: requests.Session) -> bool:
    """预热会话：访问主页和 nav 获取 cookie"""
    try:
        session.get("https://www.bilibili.com", timeout=REQUEST_TIMEOUT)
        time.sleep(0.3)
        session.get("https://api.bilibili.com/x/web-interface/nav", timeout=REQUEST_TIMEOUT)
        return True
    except Exception as exc:
        print(f"[warmup] 预热失败: {exc}", file=sys.stderr)
        return False


def strip_html_tags(text: str) -> str:
    """移除 B 站搜索结果中的 <em> 高亮标签"""
    return re.sub(r"<[^>]+>", "", text)


# ── B 站 API ──────────────────────────────────────────────

def search_videos(
    session: requests.Session,
    keyword: str,
    page: int = 1,
    page_size: int = SEARCH_PAGE_SIZE,
) -> Optional[Dict[str, Any]]:
    """搜索 B 站视频，返回原始 API 响应 data 字段"""
    params = {
        "search_type": "video",
        "keyword": keyword,
        "order": "pubdate",
        "page": page,
        "page_size": page_size,
    }
    url = "https://api.bilibili.com/x/web-interface/search/type"
    try:
        resp = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 412:
            print(f"[search] 412 banned (keyword={keyword}, page={page})", file=sys.stderr)
            return None
        data = resp.json()
        if data.get("code") != 0:
            print(f"[search] API error: {data.get('message')} (keyword={keyword}, page={page})", file=sys.stderr)
            return None
        return data.get("data") or {}
    except Exception as exc:
        print(f"[search] 请求异常: {exc}", file=sys.stderr)
        return None


def search_all_official_videos(
    session: requests.Session,
    keywords: List[str],
    max_pages: int = 10,
) -> List[Dict[str, Any]]:
    """搜索所有指定关键词的 CSGO官方赛事 视频（去重）"""
    seen_bvids: Set[str] = set()
    videos: List[Dict[str, Any]] = []

    for keyword in keywords:
        print(f"[search] 搜索关键词: {keyword}", file=sys.stderr)
        for page in range(1, max_pages + 1):
            data = search_videos(session, keyword, page=page)
            if data is None:
                break

            results = data.get("result") or []
            num_pages = safe_int(data.get("numPages"), 1)
            total = safe_int(data.get("numResults"), 0)
            print(f"[search]   page {page}/{num_pages} total={total} results={len(results)}", file=sys.stderr)

            official_count = 0
            for item in results:
                author = str(item.get("author") or "").strip()
                if author != UPLOADER_NAME:
                    continue
                bvid = str(item.get("bvid") or "").strip()
                if not bvid or bvid in seen_bvids:
                    continue
                seen_bvids.add(bvid)
                title = strip_html_tags(str(item.get("title") or "").strip())
                pubdate = safe_int(item.get("pubdate"))
                official_count += 1
                videos.append({
                    "bvid": bvid,
                    "title": title,
                    "author": author,
                    "pubdate": pubdate,
                    "pubdateStr": datetime.fromtimestamp(pubdate).strftime("%Y-%m-%d") if pubdate > 0 else "",
                })

            if official_count > 0:
                print(f"[search]   -> CSGO官方赛事 {official_count} 个视频", file=sys.stderr)

            if page >= num_pages:
                break
            time.sleep(SEARCH_DELAY)

    print(f"[search] 总计 {len(videos)} 个 CSGO官方赛事 视频（去重后）", file=sys.stderr)
    return videos


def get_video_detail(session: requests.Session, bvid: str) -> Optional[Dict[str, Any]]:
    """获取单个视频详情（含分 P 列表）"""
    url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    try:
        resp = session.get(url, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 412:
            print(f"[detail] 412 banned: {bvid}", file=sys.stderr)
            return None
        data = resp.json()
        if data.get("code") != 0:
            print(f"[detail] API error: {data.get('message')} for {bvid}", file=sys.stderr)
            return None
        return data.get("data") or {}
    except Exception as exc:
        print(f"[detail] 请求异常: {exc} for {bvid}", file=sys.stderr)
        return None


def build_episodes_from_pages(pages: List[Dict[str, Any]], bvid: str) -> List[Dict[str, Any]]:
    """从 B 站 pages 数组构建 episodes 列表"""
    episodes: List[Dict[str, Any]] = []
    for p in pages:
        page_num = safe_int(p.get("page"), 1)
        part = str(p.get("part") or "").strip()
        cid = str(p.get("cid") or "").strip()
        episodes.append({
            "page": page_num,
            "part": part,
            "cid": cid,
            "bvid": bvid,
            "videoUrl": f"https://www.bilibili.com/video/{bvid}/?p={page_num}",
        })
    episodes.sort(key=lambda e: e["page"])
    return episodes


# ── 标题解析 ──────────────────────────────────────────────

def parse_video_title(title: str) -> Dict[str, Any]:
    """
    从视频标题中解析赛事名、对阵双方、日期

    格式: 【2026PGL阿斯塔纳】TeamA vs TeamB  月日 阶段
    """
    result: Dict[str, Any] = {
        "tournament": "",
        "teamA": "",
        "teamB": "",
        "dateStr": "",
        "stage": "",
    }
    text = strip_html_tags(title).strip()

    # 提取【...】中的赛事名
    bracket_match = re.match(r"【(.+?)】", text)
    if bracket_match:
        result["tournament"] = bracket_match.group(1).strip()
        text = text[bracket_match.end():].strip()

    # 提取 vs 对阵
    vs_match = re.search(r"(.+?)\s+vs\s+(.+?)(?:\s+\d|$)", text, re.IGNORECASE)
    if vs_match:
        result["teamA"] = vs_match.group(1).strip()
        after_vs = text[vs_match.start(2):]
        # teamB 取到下一个空格或数字日期前
        team_b_end = re.search(r"\s+(?=\d)", after_vs)
        if team_b_end:
            result["teamB"] = after_vs[:team_b_end.start()].strip()
        else:
            result["teamB"] = vs_match.group(2).strip()

    # 提取日期（月日）
    date_match = re.search(r"(\d+)月(\d+)日", text)
    if date_match:
        result["dateStr"] = f"{date_match.group(1)}月{date_match.group(2)}日"

    # 提取阶段
    stage_keywords = ["瑞士轮", "小组赛", "淘汰赛", "半决赛", "总决赛", "决赛", "封闭预选", "公开预选"]
    for kw in stage_keywords:
        if kw in text:
            result["stage"] = kw
            break

    return result


# ── 数据库查询 ────────────────────────────────────────────

def get_db_connection():
    """获取数据库连接（使用与主服务相同的配置）"""
    try:
        import pymysql
        return pymysql.connect(**DB_CONFIG)
    except ImportError:
        print("[db] pymysql 未安装，跳过数据库匹配", file=sys.stderr)
        return None
    except Exception as exc:
        print(f"[db] 连接失败: {exc}", file=sys.stderr)
        return None


def get_tournament_event_ids(cur, tournament_names: List[str]) -> Dict[str, str]:
    """根据赛事名获取 event_id 映射"""
    if not tournament_names or cur is None:
        return {}
    placeholders = ", ".join(["%s"] * len(tournament_names))
    cur.execute(
        f"SELECT event_id, event_name FROM event_basic WHERE event_name IN ({placeholders})",
        tournament_names,
    )
    mapping: Dict[str, str] = {}
    for row in cur.fetchall():
        mapping[str(row[0] or "").strip()] = str(row[1] or "").strip()
    return mapping


def get_matches_for_event(cur, event_id: str) -> List[Dict[str, Any]]:
    """获取指定 event 下所有已结束比赛"""
    cur.execute(
        """
        SELECT match_id, team1, team2, match_time, score1, score2
        FROM match_result
        WHERE event_id = %s
        ORDER BY match_time DESC
        """,
        (event_id,),
    )
    matches: List[Dict[str, Any]] = []
    for row in cur.fetchall():
        mt = row[3]
        date_str = ""
        if isinstance(mt, datetime):
            date_str = mt.strftime("%Y-%m-%d")
        matches.append({
            "match_id": str(row[0] or "").strip(),
            "team1": str(row[1] or "").strip(),
            "team2": str(row[2] or "").strip(),
            "match_time": mt,
            "dateStr": date_str,
            "score1": safe_int(row[4]),
            "score2": safe_int(row[5]),
        })
    return matches


# ── 匹配引擎 ──────────────────────────────────────────────

def match_video_to_db(
    video: Dict[str, Any],
    matches: List[Dict[str, Any]],
) -> Optional[str]:
    """
    将视频匹配到数据库比赛，返回 match_id 或 None

    匹配策略（与主服务 match_bilibili_official_replay 一致）：
    1. 赛事名匹配
    2. 双方战队匹配
    3. 日期匹配
    """
    parsed = parse_video_title(video.get("title", ""))
    v_team_a = normalize_key(parsed["teamA"])
    v_team_b = normalize_key(parsed["teamB"])
    v_date = parsed["dateStr"]
    v_pubdate = video.get("pubdateStr", "")

    best_match: Optional[str] = None
    best_score = 0

    for m in matches:
        score = 0

        # 赛事名已由 event_id 层面保证一致

        # 战队匹配
        m_team_a = normalize_key(m["team1"])
        m_team_b = normalize_key(m["team2"])

        # 考虑别名
        a_aliases = TEAM_NAME_ALIASES.get(v_team_a, [v_team_a])
        b_aliases = TEAM_NAME_ALIASES.get(v_team_b, [v_team_b])
        a_set = set(a_aliases) | {v_team_a}
        b_set = set(b_aliases) | {v_team_b}

        a_hit = m_team_a in a_set or v_team_a == m_team_a or any(
            alias == m_team_a for alias in a_aliases
        )
        b_hit = m_team_b in b_set or v_team_b == m_team_b or any(
            alias == m_team_b for alias in b_aliases
        )

        # 也检查交叉匹配（teamA→teamB, teamB→teamA）
        a_cross = m_team_a in b_set or any(alias == m_team_a for alias in b_aliases)
        b_cross = m_team_b in a_set or any(alias == m_team_b for alias in a_aliases)

        if a_hit and b_hit:
            score += 9
        elif a_hit or b_hit:
            score += 4
        elif a_cross and b_cross:
            score += 9
        elif a_cross or b_cross:
            score += 4

        # 日期匹配
        m_date_alt = _date_variants(m["dateStr"])
        if v_date and any(v_date in alt for alt in m_date_alt):
            score += 3
        elif v_pubdate == m["dateStr"]:
            score += 2

        # 同日期发布加分
        if v_pubdate and v_pubdate == m["dateStr"]:
            score += 2

        if score > best_score:
            best_score = score
            best_match = m["match_id"]

    # 阈值：至少需要 9 分（双方战队匹配 或 单方+日期+发布日）
    if best_score >= 9:
        return best_match
    return None


def _date_variants(date_str: str) -> List[str]:
    """生成日期变体"""
    if not date_str:
        return []
    variants = [date_str]
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        variants.extend([
            f"{dt.month}月{dt.day}日",
            f"{dt.year}年{dt.month}月{dt.day}日",
        ])
    except ValueError:
        pass
    return variants


# ── 主同步流程 ────────────────────────────────────────────

def sync_videos(
    keywords: Optional[List[str]] = None,
    max_search_pages: int = 10,
) -> List[Dict[str, Any]]:
    """
    主同步函数：拉取视频 → 获取详情 → 返回索引

    返回: 完整官方视频索引列表
    """
    if keywords is None:
        keywords = list(TOURNAMENT_SEARCH_KEYWORDS.keys())

    session = build_session()
    if not warmup_session(session):
        print("[sync] 会话预热失败，无法继续", file=sys.stderr)
        return []

    # 1. 搜索所有视频
    print("[sync] 开始搜索视频...", file=sys.stderr)
    videos = search_all_official_videos(session, keywords, max_pages=max_search_pages)

    # 2. 获取每个视频的详情（分P）
    print(f"[sync] 获取 {len(videos)} 个视频详情...", file=sys.stderr)
    index_entries: List[Dict[str, Any]] = []
    for i, video in enumerate(videos):
        bvid = video["bvid"]
        detail = get_video_detail(session, bvid)
        if detail is None:
            # 无详情也加入基础条目
            index_entries.append({
                "title": video["title"],
                "bvid": bvid,
                "publishedAt": video.get("pubdateStr", ""),
                "uploader": UPLOADER_NAME,
            })
            continue

        pages = detail.get("pages") or []
        episodes = build_episodes_from_pages(pages, bvid) if len(pages) > 1 else []
        pubdate_ts = safe_int(detail.get("pubdate") or video.get("pubdate"), 0)
        pubdate_str = datetime.fromtimestamp(pubdate_ts).strftime("%Y-%m-%d") if pubdate_ts > 0 else video.get("pubdateStr", "")

        entry: Dict[str, Any] = {
            "title": strip_html_tags(str(detail.get("title") or video["title"]).strip()),
            "bvid": bvid,
            "publishedAt": pubdate_str,
            "uploader": UPLOADER_NAME,
        }

        if episodes:
            entry["episodes"] = episodes
            entry["episodeCount"] = len(episodes)

        index_entries.append(entry)

        if (i + 1) % 10 == 0:
            print(f"[sync]   {i + 1}/{len(videos)} ...", file=sys.stderr)
        time.sleep(VIDEO_DETAIL_DELAY)

    print(f"[sync] 完成，共 {len(index_entries)} 条索引", file=sys.stderr)
    return index_entries


def match_and_report(
    index_entries: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """将索引条目匹配到数据库比赛，输出匹配报告"""
    conn = get_db_connection()
    if conn is None:
        return {"matched": 0, "unmatched": len(index_entries), "details": []}

    report: List[Dict[str, Any]] = []
    matched = 0
    unmatched = 0

    try:
        cur = conn.cursor()

        def _extract_tokens(text: str) -> List[str]:
            parts = re.findall(r"\d+|[a-zA-Z]+|[一-鿿]+", text.lower())
            return [p.strip() for p in parts if p.strip()]

        # 收集所有 DB 中可能的赛事名
        search_keywords = set()
        for entry in index_entries:
            parsed = parse_video_title(entry.get("title", ""))
            t_name = parsed["tournament"]
            if t_name:
                search_keywords.add(normalize_key(t_name))

        # 查所有可能的 event_basic 记录
        cur.execute("SELECT event_id, event_name FROM event_basic")
        all_events: Dict[str, str] = {}
        for row in cur.fetchall():
            all_events[str(row[0] or "").strip()] = str(row[1] or "").strip()

        # 令牌匹配赛事名
        event_mapping: Dict[str, str] = {}
        for event_id, event_name in all_events.items():
            nk_db = normalize_key(event_name)
            db_tokens = set(_extract_tokens(nk_db))
            for kw in search_keywords:
                v_tokens = set(_extract_tokens(kw))
                common = v_tokens & db_tokens
                if common and len(common) >= 2 and len(common) >= len(v_tokens) * 0.5:
                    # 排除封闭预选/公开预选等次级赛事
                    if any(t in nk_db for t in ["封闭预选", "公开预选", "cq", "oq"]):
                        continue
                    event_mapping[event_id] = event_name
                    break

        # 按 event_id 分组获取所有比赛
        all_matches: Dict[str, List[Dict[str, Any]]] = {}
        for event_id in event_mapping:
            all_matches[event_id] = get_matches_for_event(cur, event_id)

        # 扁平化
        flat_matches: List[Dict[str, Any]] = []
        for event_id, matches in all_matches.items():
            event_name = event_mapping.get(event_id, event_id)
            for m in matches:
                m["event_name"] = event_name
                flat_matches.append(m)

        # 匹配
        for entry in index_entries:
            parsed = parse_video_title(entry.get("title", ""))
            t_name = parsed["tournament"]
            nk_t = normalize_key(t_name)
            v_tokens = set(_extract_tokens(nk_t))

            relevant_matches: List[Dict[str, Any]] = []
            for event_id, event_name in event_mapping.items():
                nk_db = normalize_key(event_name)
                db_tokens = set(_extract_tokens(nk_db))
                common = v_tokens & db_tokens
                if len(common) >= 2 and len(common) >= len(v_tokens) * 0.5:
                    relevant_matches.extend(all_matches.get(event_id, []))

            if not relevant_matches:
                relevant_matches = flat_matches

            match_id = match_video_to_db(entry, relevant_matches)
            entry_copy = {
                "bvid": entry["bvid"],
                "title": entry.get("title", ""),
                "publishedAt": entry.get("publishedAt", ""),
                "episodeCount": len(entry.get("episodes") or []),
            }
            if match_id:
                matched += 1
                report.append({**entry_copy, "matched": True, "match_id": match_id})
            else:
                unmatched += 1
                report.append({**entry_copy, "matched": False, "match_id": None})

    finally:
        conn.close()

    return {"matched": matched, "unmatched": unmatched, "details": report}


# ── CLI ────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Bilibili CSGO官方赛事 视频同步")
    parser.add_argument(
        "--keywords", nargs="*",
        help="自定义搜索关键词（默认使用配置中的 TOURNAMENT_SEARCH_KEYWORDS）",
    )
    parser.add_argument(
        "--max-pages", type=int, default=10,
        help="每个关键词最大搜索页数",
    )
    parser.add_argument(
        "--output", type=str, default="",
        help="输出 JSON 文件路径（默认输出到 stdout）",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="仅检查匹配情况，不输出完整 JSON",
    )
    parser.add_argument(
        "--pretty", action="store_true",
        help="美化 JSON 输出",
    )
    parser.add_argument(
        "--report", action="store_true",
        help="输出匹配报告",
    )
    args = parser.parse_args()

    keywords = args.keywords if args.keywords else None

    # 同步
    index_entries = sync_videos(keywords=keywords, max_search_pages=args.max_pages)

    if not index_entries:
        print("[]")
        return

    # 输出
    indent = 2 if args.pretty else None
    json_text = json.dumps(index_entries, indent=indent, ensure_ascii=False)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(json_text)
            f.write("\n")
        print(f"已写入 {args.output} ({len(index_entries)} 条)", file=sys.stderr)
    elif args.check:
        report = match_and_report(index_entries)
        print(json.dumps(report, indent=2, ensure_ascii=False))
    elif args.report:
        report = match_and_report(index_entries)
        print(f"匹配: {report['matched']}/{len(index_entries)}")
        print(f"未匹配: {report['unmatched']}")
        for item in report["details"]:
            status = "OK" if item["matched"] else "??"
            print(f"  [{status}] {item['bvid']} | {item['title'][:60]}")
    else:
        print(json_text)


if __name__ == "__main__":
    main()
