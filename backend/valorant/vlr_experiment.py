"""VLR.gg parsing pipeline for VALORANT data.

This script fetches VLR list/detail/profile pages and writes CSV files for the
Valorant MySQL import pipeline. HTML caching is disabled by default to avoid
large local cache directories; pass --cache-dir explicitly if you want one.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests


BASE_URL = "https://www.vlr.gg"
USER_AGENT = "GameLeagueVLRExperiment/0.1 local low-frequency research"
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "vlr_data"

PRESET_DEFAULTS = {
    "quick": {
        "fixture_pages": 1,
        "result_pages": 1,
        "result_start_date": "",
        "max_result_pages": 1,
        "detail_limit": 2,
        "stats_limit": 100,
        "player_profile_limit": 30,
        "skip_stats": False,
        "skip_player_profiles": False,
        "detail_workers": 2,
        "profile_workers": 2,
        "sleep_seconds": 0.5,
        "retry_attempts": 3,
        "timeout_seconds": 30,
    },
    "daily": {
        "fixture_pages": 3,
        "result_pages": 5,
        "result_start_date": "",
        "max_result_pages": 5,
        "detail_limit": 150,
        "stats_limit": 300,
        "player_profile_limit": 500,
        "skip_stats": False,
        "skip_player_profiles": False,
        "detail_workers": 4,
        "profile_workers": 2,
        "sleep_seconds": 0.35,
        "retry_attempts": 3,
        "timeout_seconds": 30,
    },
    "full": {
        "fixture_pages": 8,
        "result_pages": 0,
        "result_start_date": "2023-01-01",
        "max_result_pages": 1000,
        "detail_limit": 0,
        "stats_limit": 1000,
        "player_profile_limit": 0,
        "skip_stats": False,
        "skip_player_profiles": False,
        "detail_workers": 12,
        "profile_workers": 6,
        "sleep_seconds": 0.12,
        "retry_attempts": 4,
        "timeout_seconds": 35,
    },
    "turbo": {
        "fixture_pages": 8,
        "result_pages": 0,
        "result_start_date": "2023-01-01",
        "max_result_pages": 1000,
        "detail_limit": 0,
        "stats_limit": 1000,
        "player_profile_limit": 0,
        "skip_stats": False,
        "skip_player_profiles": True,
        "detail_workers": 16,
        "profile_workers": 1,
        "sleep_seconds": 0.08,
        "retry_attempts": 4,
        "timeout_seconds": 35,
    },
}

VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}

VALORANT_MAPS = {
    "abyss",
    "ascent",
    "bind",
    "breeze",
    "corrode",
    "fracture",
    "haven",
    "icebox",
    "lotus",
    "pearl",
    "split",
    "sunset",
}


def clean_text(value: Any) -> str:
    text = unescape(str(value or ""))
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def log(message: str) -> None:
    print(message, flush=True)


def normalize_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("//"):
        return f"https:{text}"
    return urljoin(BASE_URL, text)


def team_name_from_match_header(link: Node) -> str:
    img = link.first(tag="img")
    if img:
        alt = clean_text(img.attrs.get("alt", ""))
        alt = re.sub(r"\s+team\s+logo\s*$", "", alt, flags=re.I).strip()
        if alt:
            return alt

    title = link.first(class_name="wf-title-med")
    if not title:
        return link.text()
    for child in title.children:
        text = clean_text(child.text()).strip()
        if text.startswith("(") and text.endswith(")") and len(text) > 2:
            return clean_text(text[1:-1])
    return title.direct_text() or title.text()


def slug_from_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.strip("/").replace("/", "_") or "home"
    if parsed.query:
        path += "_" + re.sub(r"[^A-Za-z0-9]+", "_", parsed.query).strip("_")
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", path).strip("_")[:100]
    return f"{safe}_{digest}.html"


def parse_id_slug(href: Any, prefix: str) -> Tuple[str, str]:
    text = str(href or "").strip()
    match = re.search(rf"/{re.escape(prefix)}/(\d+)/([^/?#]+)", text)
    if not match:
        return "", ""
    return match.group(1), match.group(2)


def parse_match_href(href: Any) -> Tuple[str, str]:
    text = str(href or "").strip()
    match = re.search(r"^/(\d+)/([^/?#]+)", text)
    if not match:
        return "", ""
    return match.group(1), match.group(2)


def parse_vlr_date(label: str) -> str:
    text = clean_text(label)
    match = re.search(
        r"\b([A-Z][a-z]+, [A-Z][a-z]+ \d{1,2}(?:st|nd|rd|th)?, \d{4})\b",
        text,
    )
    if not match:
        return ""
    date_text = re.sub(r"(\d{1,2})(st|nd|rd|th)", r"\1", match.group(1))
    for fmt in ("%a, %b %d, %Y", "%a, %B %d, %Y", "%A, %b %d, %Y", "%A, %B %d, %Y"):
        try:
            return datetime.strptime(date_text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def combine_match_time(date_text: str, time_text: str) -> str:
    if not date_text:
        return ""
    text = clean_text(time_text)
    if not text or text.upper() in {"TBD", "LIVE"}:
        return f"{date_text} 00:00:00"
    try:
        parsed_time = datetime.strptime(text.upper(), "%I:%M %p").time()
    except ValueError:
        return f"{date_text} 00:00:00"
    return f"{date_text} {parsed_time.strftime('%H:%M:%S')}"


def first_int(value: Any) -> Optional[int]:
    match = re.search(r"-?\d+", str(value or ""))
    if not match:
        return None
    return int(match.group(0))


def map_name_from_text(value: Any) -> str:
    compact = clean_text(value)
    lower = compact.lower()
    for name in sorted(VALORANT_MAPS):
        if re.search(rf"\b{re.escape(name)}\b", lower):
            return name.title()
    return compact.split(" ")[0] if compact else ""


@dataclass
class Node:
    tag: str
    attrs: Dict[str, str] = field(default_factory=dict)
    parent: Optional["Node"] = None
    children: List["Node"] = field(default_factory=list)
    text_parts: List[str] = field(default_factory=list)

    def classes(self) -> set[str]:
        return set(str(self.attrs.get("class", "")).split())

    def has_class(self, class_name: str) -> bool:
        return class_name in self.classes()

    def text(self) -> str:
        parts = list(self.text_parts)
        for child in self.children:
            parts.append(child.text())
        return clean_text(" ".join(part for part in parts if part))

    def direct_text(self) -> str:
        return clean_text(" ".join(self.text_parts))

    def find_all(
        self,
        *,
        tag: str | None = None,
        class_name: str | None = None,
        pred: Any = None,
    ) -> List["Node"]:
        out: List[Node] = []
        if (
            (tag is None or self.tag == tag)
            and (class_name is None or self.has_class(class_name))
            and (pred is None or pred(self))
        ):
            out.append(self)
        for child in self.children:
            out.extend(child.find_all(tag=tag, class_name=class_name, pred=pred))
        return out

    def first(
        self,
        *,
        tag: str | None = None,
        class_name: str | None = None,
        pred: Any = None,
    ) -> Optional["Node"]:
        if (
            (tag is None or self.tag == tag)
            and (class_name is None or self.has_class(class_name))
            and (pred is None or pred(self))
        ):
            return self
        for child in self.children:
            found = child.first(tag=tag, class_name=class_name, pred=pred)
            if found:
                return found
        return None

    def direct_children(
        self, *, tag: str | None = None, class_name: str | None = None
    ) -> List["Node"]:
        return [
            child
            for child in self.children
            if (tag is None or child.tag == tag)
            and (class_name is None or child.has_class(class_name))
        ]


class TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("document")
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        node = Node(tag=tag.lower(), attrs={k: v or "" for k, v in attrs})
        node.parent = self.stack[-1]
        self.stack[-1].children.append(node)
        if tag.lower() not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(
        self, tag: str, attrs: List[Tuple[str, Optional[str]]]
    ) -> None:
        node = Node(tag=tag.lower(), attrs={k: v or "" for k, v in attrs})
        node.parent = self.stack[-1]
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag: str) -> None:
        target = tag.lower()
        for idx in range(len(self.stack) - 1, 0, -1):
            if self.stack[idx].tag == target:
                del self.stack[idx:]
                break

    def handle_data(self, data: str) -> None:
        if data and data.strip():
            self.stack[-1].text_parts.append(data)


def parse_html(html: str) -> Node:
    parser = TreeBuilder()
    parser.feed(html)
    return parser.root


def first_text(node: Node, class_name: str) -> str:
    found = node.first(class_name=class_name)
    return found.text() if found else ""


def normalize_score_text(value: Any) -> str:
    text = clean_text(value)
    return text if re.fullmatch(r"\d+", text) else ""


def country_from_flag(node: Node) -> str:
    flag = node.first(pred=lambda item: "flag" in item.classes())
    if not flag:
        return ""
    for cls in flag.classes():
        if cls.startswith("mod-") and cls != "mod":
            return cls[4:].upper()
    return ""


def extract_match_team(team_node: Node) -> Dict[str, Any]:
    name = first_text(team_node, "match-item-vs-team-name")
    score = normalize_score_text(first_text(team_node, "match-item-vs-team-score"))
    return {
        "name": name,
        "country": country_from_flag(team_node),
        "score": score,
        "isWinner": "mod-winner" in team_node.classes(),
    }


def parse_match_list(html: str, view: str, source_url: str) -> List[Dict[str, Any]]:
    root = parse_html(html)
    rows: List[Dict[str, Any]] = []
    current_date = ""

    def walk(node: Node) -> None:
        nonlocal current_date
        if node.tag == "div" and node.has_class("wf-label") and node.has_class("mod-large"):
            parsed_date = parse_vlr_date(node.text())
            if parsed_date:
                current_date = parsed_date
        if node.tag == "a" and node.has_class("match-item"):
            href = node.attrs.get("href", "")
            match_id, slug = parse_match_href(href)
            teams = [
                extract_match_team(item)
                for item in node.find_all(tag="div", class_name="match-item-vs-team")
            ][:2]
            while len(teams) < 2:
                teams.append({"name": "", "country": "", "score": "", "isWinner": False})

            event_node = node.first(class_name="match-item-event")
            series = first_text(node, "match-item-event-series")
            event_text = event_node.direct_text() if event_node else ""
            if not event_text and event_node:
                event_text = event_node.text().replace(series, "", 1)
            event_text = clean_text(event_text)
            time_text = first_text(node, "match-item-time")
            eta = first_text(node, "match-item-eta")
            status = "completed" if view == "result" else "scheduled"
            if "LIVE" in eta.upper():
                status = "live"
            elif view == "fixture" and (teams[0]["score"] or teams[1]["score"]):
                status = "in_progress"
            winner = ""
            if teams[0]["isWinner"]:
                winner = teams[0]["name"]
            elif teams[1]["isWinner"]:
                winner = teams[1]["name"]

            rows.append(
                {
                    "match_id": match_id,
                    "slug": slug,
                    "match_url": normalize_url(href),
                    "match_date": current_date,
                    "match_time": combine_match_time(current_date, time_text),
                    "time_text": clean_text(time_text),
                    "event_name": event_text,
                    "stage": series,
                    "team1": teams[0]["name"],
                    "team1_country": teams[0]["country"],
                    "team2": teams[1]["name"],
                    "team2_country": teams[1]["country"],
                    "score1": teams[0]["score"],
                    "score2": teams[1]["score"],
                    "winner": winner,
                    "status": status,
                    "note": first_text(node, "match-item-note"),
                    "source": "vlr_experiment",
                    "source_list_url": source_url,
                    "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
        for child in node.children:
            walk(child)

    walk(root)
    return rows


def parse_stats_page(html: str, source_url: str, limit: int = 0) -> List[Dict[str, Any]]:
    root = parse_html(html)
    rows: List[Dict[str, Any]] = []
    for tr in root.find_all(tag="tr"):
        player_link = tr.first(tag="a", pred=lambda n: "/player/" in n.attrs.get("href", ""))
        if not player_link:
            continue
        cells = tr.direct_children(tag="td")
        if len(cells) < 8:
            continue
        player_id, player_slug = parse_id_slug(player_link.attrs.get("href", ""), "player")
        player_cell = cells[0]
        player_name_node = player_cell.first(class_name="text-of")
        player_name = player_name_node.text() if player_name_node else player_link.text()
        team_node = player_cell.first(class_name="stats-player-country")
        agents = []
        for img in cells[1].find_all(tag="img"):
            title = clean_text(img.attrs.get("title", ""))
            src = img.attrs.get("src", "")
            agents.append(title or Path(src).stem)

        values = [cell.text() for cell in cells[2:]]
        values += [""] * 20
        rows.append(
            {
                "player_id": player_id,
                "player_slug": player_slug,
                "player_name": player_name,
                "team_abbrev": team_node.text() if team_node else "",
                "country": country_from_flag(player_cell),
                "agents": "|".join(item for item in agents if item),
                "rounds": values[0],
                "rating": values[1],
                "acs": values[2],
                "kd": values[3],
                "kast": values[4],
                "adr": values[5],
                "kpr": values[6],
                "apr": values[7],
                "fkpr": values[8],
                "fdpr": values[9],
                "hs_pct": values[10],
                "cl_pct": values[11],
                "clutches": values[12],
                "kmax": values[13],
                "kills": values[14],
                "deaths": values[15],
                "assists": values[16],
                "first_kills": values[17],
                "first_deaths": values[18],
                "source": "vlr_experiment",
                "source_url": source_url,
                "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
        if limit and len(rows) >= limit:
            break
    return rows


def stat_both_text(cell: Node) -> str:
    both = cell.first(
        pred=lambda n: "side" in n.classes()
        and ("mod-both" in n.classes() or "mod_both" in n.classes())
    )
    return both.text() if both else cell.text()


def player_from_cell(cell: Node) -> Dict[str, str]:
    link = cell.first(tag="a", pred=lambda n: "/player/" in n.attrs.get("href", ""))
    player_id, player_slug = parse_id_slug(link.attrs.get("href", "") if link else "", "player")
    name_node = cell.first(class_name="text-of")
    team_node = cell.first(class_name="ge-text-light")
    return {
        "player_id": player_id,
        "player_slug": player_slug,
        "player_name": name_node.text() if name_node else (link.text() if link else ""),
        "team_abbrev": team_node.text().upper() if team_node else "",
        "country": country_from_flag(cell),
    }


def agents_from_cell(cell: Node) -> str:
    agents = []
    for img in cell.find_all(tag="img"):
        agents.append(clean_text(img.attrs.get("title", "")) or Path(img.attrs.get("src", "")).stem)
    return "|".join(item for item in agents if item)


def parse_detail_header(root: Node, source_url: str) -> Dict[str, Any]:
    event_node = root.first(tag="a", class_name="match-header-event")
    event_id, event_slug = parse_id_slug(event_node.attrs.get("href", "") if event_node else "", "event")
    event_series = first_text(event_node, "match-header-event-series") if event_node else ""
    event_text = event_node.text() if event_node else ""
    event_name = clean_text(event_text.replace(event_series, "", 1))

    date_nodes = root.find_all(class_name="moment-tz-convert")
    utc_ts = ""
    for node in date_nodes:
        utc_ts = node.attrs.get("data-utc-ts", "")
        if utc_ts:
            break

    team_links = root.find_all(
        tag="a",
        pred=lambda n: n.has_class("match-header-link")
        and "/team/" in n.attrs.get("href", ""),
    )[:2]
    teams = []
    for link in team_links:
        team_id, team_slug = parse_id_slug(link.attrs.get("href", ""), "team")
        logo = ""
        img = link.first(tag="img")
        if img:
            logo = normalize_url(img.attrs.get("src", ""))
        teams.append(
            {
                "team_id": team_id,
                "team_slug": team_slug,
                "team_name": team_name_from_match_header(link),
                "team_logo": logo,
            }
        )
    while len(teams) < 2:
        teams.append({"team_id": "", "team_slug": "", "team_name": "", "team_logo": ""})

    header_score = ""
    score1 = ""
    score2 = ""
    for node in root.find_all(class_name="js-spoiler"):
        text = node.text()
        match = re.search(r"(\d+)\s*:\s*(\d+)", text)
        if match:
            score1, score2 = match.group(1), match.group(2)
            header_score = f"{score1}:{score2}"
            break

    bo = ""
    status = "scheduled"
    for note in root.find_all(class_name="match-header-vs-note"):
        text = note.text()
        if re.search(r"\bBo\d+\b", text, re.I):
            bo = re.search(r"\bBo(\d+)\b", text, re.I).group(1)
        if "live" in text.lower():
            status = "live"
    if status != "live" and header_score:
        status = "completed"

    match_id, slug = parse_match_href(urlparse(source_url).path)
    return {
        "match_id": match_id,
        "slug": slug,
        "match_url": source_url,
        "event_id": event_id,
        "event_slug": event_slug,
        "event_name": event_name,
        "stage": event_series,
        "match_time_utc": utc_ts,
        "bo": bo,
        "team1_id": teams[0]["team_id"],
        "team1": teams[0]["team_name"],
        "team1_logo": teams[0]["team_logo"],
        "team2_id": teams[1]["team_id"],
        "team2": teams[1]["team_name"],
        "team2_logo": teams[1]["team_logo"],
        "score": header_score,
        "score1": score1,
        "score2": score2,
        "status": status,
        "source": "vlr_experiment",
        "source_url": source_url,
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def parse_map_score_team(team_node: Node) -> Dict[str, str]:
    score_node = team_node.first(class_name="score")
    team_name = first_text(team_node, "team-name")
    ct_score = ""
    t_score = ""
    for span in team_node.find_all(tag="span"):
        if span.has_class("mod-ct"):
            ct_score = span.text()
        elif span.has_class("mod-t"):
            t_score = span.text()
    return {
        "team_name": team_name,
        "score": score_node.text() if score_node else "",
        "ct_score": ct_score,
        "t_score": t_score,
        "is_winner": bool(score_node and score_node.has_class("mod-win")),
    }


def is_placeholder_map_row(row: Dict[str, Any]) -> bool:
    map_name = clean_text(row.get("map_name")).lower()
    duration = clean_text(row.get("duration"))
    score1 = normalize_score_text(row.get("team1_score"))
    score2 = normalize_score_text(row.get("team2_score"))
    winner = clean_text(row.get("winner"))
    return map_name in {"", "-", "tbd"} and not winner and duration in {"", "-"} and (score1 in {"", "0"}) and (score2 in {"", "0"})


def parse_detail_maps_and_players(
    root: Node, detail: Dict[str, Any]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    map_rows: List[Dict[str, Any]] = []
    player_rows: List[Dict[str, Any]] = []
    map_index = 0
    for game in root.find_all(tag="div", class_name="vm-stats-game"):
        game_id = game.attrs.get("data-game-id", "")
        if not game_id or game_id == "all":
            continue
        map_index += 1
        header = game.first(class_name="vm-stats-game-header")
        if not header:
            continue
        map_node = header.first(class_name="map")
        duration = first_text(header, "map-duration")
        team_nodes = header.direct_children(tag="div", class_name="team")[:2]
        teams = [parse_map_score_team(item) for item in team_nodes]
        while len(teams) < 2:
            teams.append({"team_name": "", "score": "", "ct_score": "", "t_score": "", "is_winner": False})
        winner = teams[0]["team_name"] if teams[0]["is_winner"] else teams[1]["team_name"] if teams[1]["is_winner"] else ""
        map_row = {
            "match_id": detail["match_id"],
            "game_id": game_id,
            "map_index": map_index,
            "map_name": map_name_from_text(map_node.text() if map_node else ""),
            "duration": duration,
            "team1": teams[0]["team_name"],
            "team1_score": normalize_score_text(teams[0]["score"]),
            "team1_ct_score": normalize_score_text(teams[0]["ct_score"]),
            "team1_t_score": normalize_score_text(teams[0]["t_score"]),
            "team2": teams[1]["team_name"],
            "team2_score": normalize_score_text(teams[1]["score"]),
            "team2_ct_score": normalize_score_text(teams[1]["ct_score"]),
            "team2_t_score": normalize_score_text(teams[1]["t_score"]),
            "winner": winner,
            "source": "vlr_experiment",
            "source_url": detail["source_url"],
            "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        if is_placeholder_map_row(map_row):
            continue
        map_rows.append(map_row)

        for tr in game.find_all(tag="tr"):
            link = tr.first(tag="a", pred=lambda n: "/player/" in n.attrs.get("href", ""))
            if not link:
                continue
            cells = tr.direct_children(tag="td")
            if len(cells) < 10:
                continue
            player = player_from_cell(cells[0])
            values = [stat_both_text(cell) for cell in cells[2:]]
            values += [""] * 16
            player_rows.append(
                {
                    "match_id": detail["match_id"],
                    "game_id": game_id,
                    "map_index": map_index,
                    "map_name": map_row["map_name"],
                    "player_id": player["player_id"],
                    "player_slug": player["player_slug"],
                    "player_name": player["player_name"],
                    "team_abbrev": player["team_abbrev"],
                    "country": player["country"],
                    "agents": agents_from_cell(cells[1]),
                    "rating": values[0],
                    "acs": values[1],
                    "kills": values[2],
                    "deaths": values[3],
                    "assists": values[4],
                    "kd_diff": values[5],
                    "kast": values[6],
                    "adr": values[7],
                    "hs_pct": values[8],
                    "first_kills": values[9],
                    "first_deaths": values[10],
                    "source": "vlr_experiment",
                    "source_url": detail["source_url"],
                    "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
    return map_rows, player_rows


def parse_match_detail(html: str, source_url: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
    root = parse_html(html)
    detail = parse_detail_header(root, source_url)
    maps, players = parse_detail_maps_and_players(root, detail)
    return detail, maps, players


def parse_player_profile(html: str, source_url: str) -> Dict[str, Any]:
    root = parse_html(html)
    player_id, player_slug = parse_id_slug(urlparse(source_url).path, "player")
    title = root.first(class_name="wf-title")
    real_name = root.first(class_name="player-real-name")
    avatar = ""
    avatar_node = root.first(tag="div", pred=lambda n: n.has_class("wf-avatar") and n.has_class("mod-player"))
    if avatar_node:
        img = avatar_node.first(tag="img")
        if img:
            src = normalize_url(img.attrs.get("src", ""))
            if src and not src.endswith("/img/base/ph/sil.png"):
                avatar = src

    country = ""
    header = root.first(class_name="player-header")
    if header:
        country = country_from_flag(header)

    return {
        "player_id": player_id,
        "player_slug": player_slug,
        "player_name": title.text() if title else "",
        "real_name": real_name.text() if real_name else "",
        "country": country,
        "avatar": avatar,
        "source": "vlr_experiment",
        "source_player_url": source_url,
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


class VlrClient:
    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        *,
        refresh: bool = False,
        sleep_seconds: float = 1.5,
        retry_attempts: int = 3,
        timeout_seconds: int = 30,
    ) -> None:
        self.cache_dir = cache_dir
        self.refresh = refresh
        self.sleep_seconds = max(0.0, sleep_seconds)
        self.retry_attempts = max(1, int(retry_attempts or 1))
        self.timeout_seconds = max(5, int(timeout_seconds or 30))
        self.local = threading.local()
        self.request_lock = threading.Lock()
        self.last_request_ts = 0.0

    def session(self) -> requests.Session:
        session = getattr(self.local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update(
                {
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Encoding": "gzip, deflate",
                }
            )
            self.local.session = session
        return session

    def fetch(self, url: str) -> str:
        full_url = normalize_url(url)
        path = self.cache_dir / slug_from_url(full_url) if self.cache_dir else None
        if path and path.exists() and not self.refresh:
            return path.read_text(encoding="utf-8", errors="ignore")
        for attempt in range(1, self.retry_attempts + 1):
            with self.request_lock:
                elapsed = time.time() - self.last_request_ts
                if elapsed < self.sleep_seconds:
                    time.sleep(self.sleep_seconds - elapsed)
                self.last_request_ts = time.time()
            try:
                response = self.session().get(full_url, timeout=self.timeout_seconds)
                response.raise_for_status()
                if path:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(response.text, encoding="utf-8", errors="ignore")
                return response.text
            except requests.RequestException as exc:
                if attempt >= self.retry_attempts:
                    raise
                wait_seconds = min(
                    8.0,
                    max(0.5, self.sleep_seconds) * (2 ** (attempt - 1)) + attempt * 0.5,
                )
                log(
                    f"[WARN] fetch retry {attempt}/{self.retry_attempts - 1}: "
                    f"{full_url} ({exc}); wait={wait_seconds:.1f}s"
                )
                time.sleep(wait_seconds)
        raise requests.RequestException(f"fetch failed after retries: {full_url}")


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: List[str] = []
    seen = set()
    for row in rows:
        for key in row.keys():
            if key not in seen:
                fieldnames.append(key)
                seen.add(key)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def collect_pages(client: VlrClient, path: str, pages: int) -> List[Tuple[str, str]]:
    out = []
    for page in range(1, max(1, pages) + 1):
        suffix = "" if page == 1 else f"?page={page}"
        url = f"{BASE_URL}{path}{suffix}"
        try:
            out.append((url, client.fetch(url)))
        except requests.RequestException as exc:
            log(f"[WARN] list fetch failed: {url} ({exc})")
    return out


def parse_iso_date(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None


def collect_result_rows(
    client: VlrClient,
    *,
    pages: int,
    start_date: str = "",
    max_pages: int = 1000,
) -> List[Dict[str, Any]]:
    start_dt = parse_iso_date(start_date)
    if not start_dt:
        rows: List[Dict[str, Any]] = []
        for url, html in collect_pages(client, "/matches/results", pages):
            rows.extend(parse_match_list(html, "result", url))
        return rows

    page_limit = int(pages or 0)
    if page_limit <= 0:
        page_limit = int(max_pages or 1000)
    page_limit = max(1, page_limit)
    rows = []
    no_date_pages = 0
    skipped_undated_rows = 0
    for page in range(1, page_limit + 1):
        suffix = "" if page == 1 else f"?page={page}"
        url = f"{BASE_URL}/matches/results{suffix}"
        try:
            page_rows = parse_match_list(client.fetch(url), "result", url)
        except requests.RequestException as exc:
            log(f"[WARN] result fetch failed: {url} ({exc})")
            break
        if not page_rows:
            log(f"[vlr-experiment] result date crawl stopped at page={page}: empty page")
            break

        dated_rows = []
        page_undated_rows = 0
        for row in page_rows:
            row_dt = parse_iso_date(row.get("match_date"))
            if not row_dt:
                page_undated_rows += 1
                continue
            if row_dt >= start_dt:
                dated_rows.append(row)
        rows.extend(dated_rows)
        skipped_undated_rows += page_undated_rows

        dates = [parse_iso_date(row.get("match_date")) for row in page_rows]
        dates = [dt for dt in dates if dt]
        if dates:
            no_date_pages = 0
        else:
            no_date_pages += 1
            log(
                f"[WARN] result page={page} has no parseable dates; "
                f"skipped {page_undated_rows} rows to keep start_date filtering strict"
            )
            if no_date_pages >= 3:
                log("[WARN] result date crawl stopped after 3 consecutive undated pages")
                break
        if page % 25 == 0 or (dates and min(dates) < start_dt):
            min_text = min(dates).strftime("%Y-%m-%d") if dates else "unknown"
            max_text = max(dates).strftime("%Y-%m-%d") if dates else "unknown"
            log(f"[vlr-experiment] result pages {page}/{page_limit} date_range={min_text}..{max_text}")
        if dates and min(dates) < start_dt:
            log(f"[vlr-experiment] result date crawl reached start_date={start_dt.strftime('%Y-%m-%d')} at page={page}")
            break
    else:
        log(f"[vlr-experiment] result date crawl hit page cap={page_limit}")
    if skipped_undated_rows:
        log(f"[WARN] result date crawl skipped undated rows={skipped_undated_rows}")
    return rows


def fetch_urls(client: VlrClient, urls: List[str], *, workers: int, label: str) -> List[Tuple[str, str]]:
    if not urls:
        return []
    safe_workers = max(1, int(workers or 1))
    if safe_workers <= 1 or len(urls) <= 1:
        out = []
        for idx, url in enumerate(urls, 1):
            try:
                out.append((url, client.fetch(url)))
            except requests.RequestException as exc:
                log(f"[WARN] {label} fetch failed: {url} ({exc})")
            if idx % 25 == 0 or idx == len(urls):
                log(f"[vlr-experiment] {label} progress {idx}/{len(urls)}")
        return out

    out: List[Tuple[int, str, str]] = []
    with ThreadPoolExecutor(max_workers=min(safe_workers, len(urls))) as executor:
        future_map = {
            executor.submit(client.fetch, url): (idx, url)
            for idx, url in enumerate(urls)
        }
        for done, future in enumerate(as_completed(future_map), 1):
            idx, url = future_map[future]
            try:
                out.append((idx, url, future.result()))
            except requests.RequestException as exc:
                log(f"[WARN] {label} fetch failed: {url} ({exc})")
            if done % 25 == 0 or done == len(urls):
                log(f"[vlr-experiment] {label} progress {done}/{len(urls)}")
    return [(url, html) for _idx, url, html in sorted(out, key=lambda item: item[0])]


def take_limit(items: List[Any], limit: int) -> List[Any]:
    safe_limit = int(limit or 0)
    if safe_limit <= 0:
        return items
    return items[:safe_limit]


def dedupe_rows(rows: Iterable[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    out = []
    seen = set()
    for row in rows:
        value = str(row.get(key) or "").strip()
        if value and value in seen:
            continue
        if value:
            seen.add(value)
        out.append(row)
    return out


def build_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run VLR.gg extraction for Valorant data.")
    parser.add_argument("--preset", choices=sorted(PRESET_DEFAULTS), default="quick")
    parser.add_argument("--fixture-pages", type=int)
    parser.add_argument("--result-pages", type=int)
    parser.add_argument(
        "--result-start-date",
        default=None,
        help="For result crawling, keep paging until this YYYY-MM-DD date. Empty disables date-driven crawling.",
    )
    parser.add_argument(
        "--max-result-pages",
        type=int,
        help="Safety cap for date-driven result crawling when --result-pages is 0.",
    )
    parser.add_argument("--detail-limit", type=int, help="0 means all collected match URLs")
    parser.add_argument("--stats-limit", type=int, help="0 means all rows on the stats page")
    parser.add_argument("--player-profile-limit", type=int, help="0 means all discovered players")
    parser.add_argument("--skip-stats", action="store_true", default=None, help="Skip /stats player summary fetch for fast realtime cycles.")
    parser.add_argument("--skip-player-profiles", action="store_true", default=None, help="Skip player profile fetches for fast realtime cycles.")
    parser.add_argument("--detail-workers", type=int, help="Concurrent workers for match detail pages.")
    parser.add_argument("--profile-workers", type=int, help="Concurrent workers for player profile pages.")
    parser.add_argument("--sleep-seconds", type=float)
    parser.add_argument("--retry-attempts", type=int, help="Request retry attempts for unstable VLR connections.")
    parser.add_argument("--timeout-seconds", type=int, help="Request timeout seconds for VLR pages.")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--cache-dir", default="", help="Optional HTML cache directory. Disabled by default.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    args = parser.parse_args()
    defaults = PRESET_DEFAULTS[args.preset]
    for key, value in defaults.items():
        if getattr(args, key) is None:
            setattr(args, key, value)
    if args.result_start_date and not parse_iso_date(args.result_start_date):
        parser.error("--result-start-date must be YYYY-MM-DD, for example 2023-01-01")
    return args


def main() -> int:
    args = build_args()
    cache_dir = Path(args.cache_dir) if str(args.cache_dir or "").strip() else None
    client = VlrClient(
        cache_dir,
        refresh=bool(args.refresh),
        sleep_seconds=float(args.sleep_seconds),
        retry_attempts=int(args.retry_attempts),
        timeout_seconds=int(args.timeout_seconds),
    )
    output_dir = Path(args.output_dir)
    log(
        "[vlr-experiment] preset="
        f"{args.preset} fixture_pages={args.fixture_pages} result_pages={args.result_pages} "
        f"result_start_date={args.result_start_date or 'disabled'} "
        f"detail_limit={args.detail_limit} stats_limit={args.stats_limit} "
        f"player_profile_limit={args.player_profile_limit} "
        f"skip_stats={bool(args.skip_stats)} skip_profiles={bool(args.skip_player_profiles)} "
        f"detail_workers={args.detail_workers} profile_workers={args.profile_workers} "
        f"sleep={args.sleep_seconds} retries={args.retry_attempts} timeout={args.timeout_seconds}"
    )

    schedule_rows: List[Dict[str, Any]] = []
    for url, html in collect_pages(client, "/matches", args.fixture_pages):
        schedule_rows.extend(parse_match_list(html, "fixture", url))

    result_rows: List[Dict[str, Any]] = []
    result_rows.extend(
        collect_result_rows(
            client,
            pages=int(args.result_pages or 0),
            start_date=str(args.result_start_date or "").strip(),
            max_pages=int(args.max_result_pages or 1000),
        )
    )

    if args.skip_stats:
        player_stats_rows = []
        log("[vlr-experiment] skip stats page")
    else:
        stats_html = client.fetch(f"{BASE_URL}/stats")
        player_stats_rows = parse_stats_page(stats_html, f"{BASE_URL}/stats", args.stats_limit)

    detail_rows: List[Dict[str, Any]] = []
    map_rows: List[Dict[str, Any]] = []
    map_player_rows: List[Dict[str, Any]] = []
    player_profile_rows: List[Dict[str, Any]] = []
    detail_urls = [
        row["match_url"]
        for row in [*result_rows, *schedule_rows]
        if row.get("match_url")
    ]
    detail_urls = take_limit(list(dict.fromkeys(detail_urls)), int(args.detail_limit))
    for url, html in fetch_urls(client, detail_urls, workers=args.detail_workers, label="detail"):
        detail, maps, players = parse_match_detail(html, url)
        detail_rows.append(detail)
        map_rows.extend(maps)
        map_player_rows.extend(players)

    if args.skip_player_profiles:
        log("[vlr-experiment] skip player profiles")
    else:
        player_urls = []
        for row in [*player_stats_rows, *map_player_rows]:
            player_id = str(row.get("player_id") or "").strip()
            player_slug = str(row.get("player_slug") or "").strip()
            if player_id and player_slug:
                player_urls.append(f"{BASE_URL}/player/{player_id}/{player_slug}")
        player_urls = take_limit(list(dict.fromkeys(player_urls)), int(args.player_profile_limit))
        for url, html in fetch_urls(client, player_urls, workers=args.profile_workers, label="profile"):
            player_profile_rows.append(parse_player_profile(html, url))

    schedule_rows = dedupe_rows(schedule_rows, "match_id")
    result_rows = dedupe_rows(result_rows, "match_id")
    player_profile_rows = dedupe_rows(player_profile_rows, "player_id")
    write_csv(output_dir / "valorant_match_schedule_vlr_experiment.csv", schedule_rows)
    write_csv(output_dir / "valorant_match_result_vlr_experiment.csv", result_rows)
    write_csv(output_dir / "valorant_match_detail_vlr_experiment.csv", detail_rows)
    write_csv(output_dir / "valorant_match_map_stats_vlr_experiment.csv", map_rows)
    write_csv(output_dir / "valorant_match_player_stats_vlr_experiment.csv", map_player_rows)
    write_csv(output_dir / "valorant_player_stats_vlr_experiment.csv", player_stats_rows)
    write_csv(output_dir / "valorant_player_profile_vlr_experiment.csv", player_profile_rows)

    log("[vlr-experiment] done")
    log(f"  schedule_rows={len(schedule_rows)}")
    log(f"  result_rows={len(result_rows)}")
    log(f"  detail_rows={len(detail_rows)}")
    log(f"  map_rows={len(map_rows)}")
    log(f"  map_player_rows={len(map_player_rows)}")
    log(f"  player_stats_rows={len(player_stats_rows)}")
    log(f"  player_profile_rows={len(player_profile_rows)}")
    log(f"  output_dir={output_dir}")
    log(f"  cache_dir={cache_dir or 'disabled'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
