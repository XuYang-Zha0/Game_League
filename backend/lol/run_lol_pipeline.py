"""Run LoL esports crawlers, then import CSV files into MySQL."""

from __future__ import annotations

import csv
import subprocess
import sys
from collections import Counter
from pathlib import Path


CRAWLER_SCRIPT = "lol_esports_gol.py"
REQUIRED_CSV_FILES = [
    "lol_event_basic.csv",
    "lol_team_basic.csv",
    "lol_player_basic.csv",
    "lol_match_result.csv",
    "lol_game_basic.csv",
    "lol_game_player_stats.csv",
]


def run_script(base_dir: Path, script_name: str, *args: str) -> int:
    script_path = base_dir / script_name
    if not script_path.exists():
        print(f"[FAILED] Missing script: {script_name}")
        return 1
    suffix = f" {' '.join(args)}" if args else ""
    print(f"\n[START] {script_name}{suffix}")
    try:
        subprocess.run([sys.executable, str(script_path), *args], cwd=str(base_dir), check=True)
    except subprocess.CalledProcessError as exc:
        print(f"[FAILED] {script_name} exit={exc.returncode}")
        return exc.returncode
    print(f"[DONE] {script_name}")
    return 0


def verify_csv_outputs(data_dir: Path) -> int:
    missing = [name for name in REQUIRED_CSV_FILES if not (data_dir / name).exists()]
    if missing:
        print("[FAILED] Missing required LoL CSV files:")
        for name in missing:
            print(f"  - {name}")
        return 1

    for name in REQUIRED_CSV_FILES:
        path = data_dir / name
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            row_count = sum(1 for _ in csv.reader(f))
        if row_count < 2:
            print(f"[FAILED] {name} has no data rows")
            return 1

    player_path = data_dir / "lol_player_basic.csv"
    with player_path.open("r", encoding="utf-8-sig", newline="") as f:
        player_rows = list(csv.DictReader(f))
    team_counts = Counter(row.get("team_id") or "" for row in player_rows)
    oversized = [(team_id, count) for team_id, count in team_counts.items() if team_id and count > 5]
    if oversized:
        print("[FAILED] lol_player_basic.csv has teams with more than 5 players:")
        for team_id, count in sorted(oversized, key=lambda item: (-item[1], item[0]))[:20]:
            print(f"  - {team_id}: {count}")
        return 1

    stats_path = data_dir / "lol_game_player_stats.csv"
    with stats_path.open("r", encoding="utf-8-sig", newline="") as f:
        stat_rows = list(csv.DictReader(f))
    if stat_rows:
        group_counts = Counter(
            (row.get("match_id") or "", row.get("game_number") or "", row.get("team_id") or "")
            for row in stat_rows
        )
        malformed = [(key, count) for key, count in group_counts.items() if all(key) and count != 5]
        if malformed:
            print("[FAILED] lol_game_player_stats.csv has match/game/team groups not equal to 5 players:")
            for key, count in malformed[:20]:
                print(f"  - {key}: {count}")
            return 1
    print("[DONE] LoL CSV output verification passed.")
    return 0


def main() -> int:
    base_dir = Path(__file__).resolve().parent
    data_dir = base_dir / "lol_data"
    data_dir.mkdir(parents=True, exist_ok=True)

    crawl_code = run_script(base_dir, CRAWLER_SCRIPT)
    if crawl_code != 0:
        return crawl_code

    verify_code = verify_csv_outputs(data_dir)
    if verify_code != 0:
        return verify_code

    print("[DONE] CSV generation complete.")
    print("[NEXT] Run import manually when ready:")
    print(f"       {sys.executable} {base_dir / 'import_lol_to_mysql.py'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
