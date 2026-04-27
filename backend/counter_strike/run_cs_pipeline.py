"""Run Counter-Strike crawlers, then import data into MySQL.

Pipeline stages:
1) Execute all crawler scripts in order to generate CSV files.
2) Verify required CSV files exist.
3) Run ``import_cs_to_mysql.py`` only if stage 1-2 succeed.
"""

from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path


CRAWLER_SCRIPTS = [
    "team_basic.py",
    "team_player_relation.py",
    "team_rank_snapshot.py",
    "team_stat_snapshot.py",
    "event_basic.py",
    "matches_scrap.py",
    "matches_result.py",
    "match_result_detail.py",
]

REQUIRED_CSV_FILES = [
    "team_basic.csv",
    "team_player_relation.csv",
    "team_rank_snapshot.csv",
    "team_stat_snapshot.csv",
    "event_basic_5eplay.csv",
    "cs2_matches_5eplay.csv",
    "cs2_results_5eplay.csv",
    "cs2_result_details_5eplay.csv",
    "cs2_result_player_stats_5eplay.csv",
    "cs2_result_map_stats_5eplay.csv",
    "cs2_result_map_player_stats_5eplay.csv",
]

MIN_DATA_ROWS = {
    "team_basic.csv": 1,
    "team_player_relation.csv": 1,
    "team_rank_snapshot.csv": 1,
    "team_stat_snapshot.csv": 1,
    "event_basic_5eplay.csv": 1,
    "cs2_matches_5eplay.csv": 1,
    "cs2_results_5eplay.csv": 1,
    "cs2_result_details_5eplay.csv": 1,
    "cs2_result_player_stats_5eplay.csv": 1,
    "cs2_result_map_stats_5eplay.csv": 1,
    "cs2_result_map_player_stats_5eplay.csv": 1,
}

IMPORT_SCRIPT = "import_cs_to_mysql.py"


def run_script(base_dir: Path, script_name: str) -> int:
    script_path = base_dir / script_name
    if not script_path.exists():
        print(f"[FAILED] Missing script: {script_name}")
        return 1

    print(f"\n[START] {script_name}")
    try:
        subprocess.run(
            [sys.executable, str(script_path)],
            cwd=str(base_dir),
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        print(f"[FAILED] {script_name} (exit code {exc.returncode})")
        return exc.returncode

    print(f"[DONE] {script_name}")
    return 0


def verify_csv_outputs(cs_data_dir: Path) -> int:
    missing = [name for name in REQUIRED_CSV_FILES if not (cs_data_dir / name).exists()]
    if missing:
        print("[FAILED] Missing required CSV outputs:")
        for filename in missing:
            print(f"  - {filename}")
        return 1

    empty_or_short = []
    for filename, min_rows in MIN_DATA_ROWS.items():
        file_path = cs_data_dir / filename
        with file_path.open("r", encoding="utf-8-sig", newline="") as f:
            row_count = sum(1 for _ in csv.reader(f))
        data_rows = max(row_count - 1, 0)
        if data_rows < min_rows:
            empty_or_short.append((filename, data_rows, min_rows))

    if empty_or_short:
        print("[FAILED] CSV row-count verification failed:")
        for filename, data_rows, min_rows in empty_or_short:
            print(f"  - {filename}: data_rows={data_rows}, expected>={min_rows}")
        return 1

    print("[DONE] CSV output + row-count verification passed.")
    return 0


def main() -> int:
    base_dir = Path(__file__).resolve().parent
    cs_data_dir = base_dir / "cs_data"
    cs_data_dir.mkdir(parents=True, exist_ok=True)

    print(f"Output directory ready: {cs_data_dir}")
    print("\nStage 1/3: Run crawler scripts")
    for script_name in CRAWLER_SCRIPTS:
        exit_code = run_script(base_dir, script_name)
        if exit_code != 0:
            return exit_code

    print("\nStage 2/3: Verify CSV outputs")
    verify_code = verify_csv_outputs(cs_data_dir)
    if verify_code != 0:
        return verify_code

    print("\nStage 3/3: Import CSV files into MySQL")
    import_code = run_script(base_dir, IMPORT_SCRIPT)
    if import_code != 0:
        return import_code

    print("\nPipeline finished successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
