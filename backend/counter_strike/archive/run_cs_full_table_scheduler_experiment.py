from __future__ import annotations

import argparse
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List


SCRIPT_VERSION = "cs-full-table-scheduler-exp-v1"


@dataclass
class Task:
    name: str
    every_seconds: int
    cmd: List[str]
    last_run_ts: float = 0.0


def build_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="CS2 full-table realtime scheduler (experimental)."
    )
    parser.add_argument("--lookback-days", type=int, default=10)
    parser.add_argument("--future-days", type=int, default=90)
    parser.add_argument("--tick-seconds", type=int, default=5)
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def script_path(base_dir: Path, name: str) -> Path:
    return base_dir / name


def build_tasks(base_dir: Path, lookback_days: int, future_days: int) -> List[Task]:
    py = sys.executable or "python3"

    # High-frequency match chain: schedule/result/detail + MySQL upsert.
    static_window_runner = script_path(base_dir, "run_cs_static_window_experiment.py")
    tasks: List[Task] = [
        Task(
            name="match_window_sync",
            every_seconds=30,
            cmd=[
                py,
                str(static_window_runner),
                "--lookback-days",
                str(max(1, lookback_days)),
                "--future-days",
                str(max(1, future_days)),
                "--schedule-pages",
                "120",
                "--result-pages",
                "120",
                "--detail-max-matches",
                "12000",
                "--detail-workers",
                "24",
                "--detail-stale-hours",
                "1",
                "--once",
            ],
        ),
    ]

    # Medium/low frequency extension points.
    # If script does not exist, scheduler logs SKIP and continues.
    extension_tasks = [
        ("team_rank_stats_refresh", 1800, "refresh_team_rank_and_stats.py"),
        ("team_player_relation_refresh", 900, "refresh_team_player_relation.py"),
        ("player_profile_refresh", 1800, "refresh_player_profiles.py"),
        ("player_recent_refresh", 3600, "refresh_player_recent_data.py"),
        ("player_history_refresh", 21600, "refresh_player_history_honor.py"),
    ]
    for name, freq, filename in extension_tasks:
        tasks.append(
            Task(
                name=name,
                every_seconds=freq,
                cmd=[py, str(script_path(base_dir, filename)), "--once"],
            )
        )
    return tasks


def run_task(task: Task) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    target = Path(task.cmd[1]) if len(task.cmd) > 1 else None
    if target and not target.exists():
        print(f"[full-table-scheduler] {ts} task={task.name} skip=missing_script path={target.name}")
        return

    try:
        proc = subprocess.run(  # noqa: S603
            task.cmd,
            capture_output=True,
            text=True,
            check=False,
        )
        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        print(
            f"[full-table-scheduler] {ts} task={task.name} rc={proc.returncode} "
            f"stdout_lines={len(stdout.splitlines()) if stdout else 0} "
            f"stderr_lines={len(stderr.splitlines()) if stderr else 0}"
        )
        if stdout:
            for line in stdout.splitlines()[-6:]:
                print(f"[full-table-scheduler][{task.name}][out] {line}")
        if stderr:
            for line in stderr.splitlines()[-6:]:
                print(f"[full-table-scheduler][{task.name}][err] {line}")
    except Exception as exc:  # pragma: no cover
        print(f"[full-table-scheduler] {ts} task={task.name} failed={exc}")


def main() -> int:
    args = build_args()
    base_dir = Path(__file__).resolve().parent
    tasks = build_tasks(base_dir, args.lookback_days, args.future_days)
    tick = max(1, int(args.tick_seconds))

    print(
        "[full-table-scheduler] start "
        f"version={SCRIPT_VERSION} "
        f"lookback_days={max(1, args.lookback_days)} "
        f"future_days={max(1, args.future_days)} "
        f"tick={tick}s once={bool(args.once)} "
        f"tasks={len(tasks)}"
    )

    try:
        while True:
            now_ts = time.time()
            for task in tasks:
                if task.last_run_ts <= 0 or now_ts - task.last_run_ts >= max(1, task.every_seconds):
                    run_task(task)
                    task.last_run_ts = time.time()
            if args.once:
                break
            time.sleep(tick)
    except KeyboardInterrupt:
        print("[full-table-scheduler] interrupted by user, exiting")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

