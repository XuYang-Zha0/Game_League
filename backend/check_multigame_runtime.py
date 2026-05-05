from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.error import URLError, HTTPError
from urllib.parse import quote
from urllib.request import urlopen


def get_json(url: str, timeout: int = 15) -> Dict[str, Any]:
    try:
        with urlopen(url, timeout=timeout) as resp:  # nosec B310
            body = resp.read().decode("utf-8", errors="ignore")
            return {"ok": True, "status": getattr(resp, "status", 200), "json": json.loads(body)}
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


def dataset_counts(data: Dict[str, Any]) -> Tuple[int, int, int, int, int]:
    return (
        len(data.get("leaderboard") or []),
        len(data.get("matches") or []),
        len(data.get("tournaments") or []),
        len(data.get("teams") or []),
        len(data.get("players") or []),
    )


def run(base_url: str, project_root: Path) -> int:
    failures: List[str] = []

    # Health
    health = get_json(f"{base_url}/api/health")
    if not health.get("ok"):
        failures.append(f"health endpoint failed: {health.get('error')}")
    else:
        status = (health.get("json") or {}).get("status")
        if status != "ok":
            failures.append(f"health endpoint status unexpected: {status}")

    # CS2 / LOL / VALORANT dataset + fixture/result
    for game in ("cs2", "lol", "valorant"):
        ds = get_json(f"{base_url}/api/{game}/dataset")
        if not ds.get("ok"):
            failures.append(f"{game} dataset failed: {ds.get('error')}")
            continue
        payload = ds.get("json") or {}
        if not payload.get("success"):
            failures.append(f"{game} dataset success=false")
            continue
        data = payload.get("data") or {}
        lb, mm, tt, tm, pl = dataset_counts(data)
        if min(lb, mm, tt, tm, pl) <= 0:
            failures.append(
                f"{game} dataset seems empty: leaderboard={lb} matches={mm} tournaments={tt} teams={tm} players={pl}"
            )

        for view in ("fixture", "result"):
            ms = get_json(f"{base_url}/api/{game}/matches?view={view}&limit=20&offset=0")
            if not ms.get("ok"):
                failures.append(f"{game} matches({view}) failed: {ms.get('error')}")
                continue
            p = ms.get("json") or {}
            if not p.get("success"):
                failures.append(f"{game} matches({view}) success=false")

        # team/player detail sample
        teams = data.get("teams") or []
        players = data.get("players") or []
        if teams:
            team_key = quote(str(teams[0].get("teamId") or teams[0].get("team_id") or teams[0].get("name") or ""))
            if team_key:
                td = get_json(f"{base_url}/api/{game}/team/{team_key}")
                if not td.get("ok") or not (td.get("json") or {}).get("success"):
                    failures.append(f"{game} team detail sample failed")
        if players:
            player_key = quote(str(players[0].get("playerId") or players[0].get("player_id") or players[0].get("name") or ""))
            if player_key:
                pd = get_json(f"{base_url}/api/{game}/player/{player_key}")
                if not pd.get("ok") or not (pd.get("json") or {}).get("success"):
                    failures.append(f"{game} player detail sample failed")

    # Valorant fallback source check (frontend static source still available)
    raw_sources = project_root / "fronted" / "src" / "data" / "rawSources.js"
    if not raw_sources.exists():
        failures.append(f"valorant source missing: {raw_sources}")
    else:
        txt = raw_sources.read_text(encoding="utf-8", errors="ignore")
        if "valorant" not in txt:
            failures.append("valorant key missing in rawSources.js")

    if failures:
        print("[multigame-check] FAILED")
        for line in failures:
            print(f"- {line}")
        return 1

    print("[multigame-check] OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Runtime smoke-check for CS2 / LOL / VALORANT")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--project-root",
        default=str(Path(__file__).resolve().parents[1]),
        help="Game_League project root path",
    )
    args = parser.parse_args()
    return run(args.base_url.rstrip("/"), Path(args.project_root))


if __name__ == "__main__":
    raise SystemExit(main())
