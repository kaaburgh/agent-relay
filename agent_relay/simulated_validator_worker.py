from __future__ import annotations

import argparse
import csv
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temp.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(dict(value), stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def _append_cycle(path: Path, row: Mapping[str, Any]) -> None:
    new_file = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=["cycle", "status", "stack_vma_count", "vm_size_delta", "image_live_count", "gpu_memory"],
        )
        if new_file:
            writer.writeheader()
        writer.writerow(dict(row))
        stream.flush()
        os.fsync(stream.fileno())


def _status(path: Path, *, run_id: str, requested: int, completed: int, state: str, current: int | None = None, child_pid: int | None = None) -> None:
    value: dict[str, Any] = {
        "run_id": run_id,
        "requested_cycles": requested,
        "completed_cycles": completed,
        "state": state,
        "pid": os.getpid(),
        "updated_at": time.time(),
    }
    if current is not None:
        value["current_cycle"] = current
    if child_pid is not None:
        value["child_pid"] = child_pid
    _atomic_json(path, value)


def run(config: Mapping[str, Any], evidence_dir: Path) -> int:
    run_id = str(config.get("run_id") or uuid.uuid4())
    requested = int(config.get("requested_cycles", 3))
    duration = float(config.get("cycle_duration", 0.01))
    fail_cycle = config.get("fail_cycle")
    hang_cycle = config.get("hang_cycle")
    crash_cycle = config.get("crash_cycle")
    incomplete_cycles = int(config.get("incomplete_cycles", 0))
    final_exit = int(config.get("exit_code", 0))
    if config.get("ignore_sigterm"):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)

    evidence_dir.mkdir(parents=True, exist_ok=True)
    status_path = evidence_dir / "runner-status.json"
    cycles_path = evidence_dir / "cycles.csv"
    summary_path = evidence_dir / "summary.md"
    child: subprocess.Popen[str] | None = None
    if config.get("spawn_child"):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])

    completed = 0
    _status(status_path, run_id=run_id, requested=requested, completed=0, state="running", child_pid=child.pid if child else None)
    for cycle in range(1, requested + 1):
        _status(status_path, run_id=run_id, requested=requested, completed=completed, state="running", current=cycle, child_pid=child.pid if child else None)
        if hang_cycle == cycle:
            while True:
                time.sleep(60)
        if crash_cycle == cycle:
            os._exit(int(config.get("crash_exit_code", 23)))
        time.sleep(duration)
        if fail_cycle == cycle:
            _append_cycle(cycles_path, {
                "cycle": cycle,
                "status": "failed",
                "stack_vma_count": 100 + cycle,
                "vm_size_delta": cycle * 10,
                "image_live_count": 20 + cycle,
                "gpu_memory": 1000 + cycle,
            })
            _status(status_path, run_id=run_id, requested=requested, completed=completed, state="failed", current=cycle, child_pid=child.pid if child else None)
            summary_path.write_text(f"# Simulated validation\n\nRun `{run_id}` failed at cycle {cycle}.\n", encoding="utf-8")
            return int(config.get("fail_exit_code", 2))
        if incomplete_cycles and cycle > requested - incomplete_cycles:
            completed += 1
            continue
        _append_cycle(cycles_path, {
            "cycle": cycle,
            "status": "ok",
            "stack_vma_count": 100 + cycle,
            "vm_size_delta": cycle * 10,
            "image_live_count": 20 + cycle,
            "gpu_memory": 1000 + cycle,
        })
        completed += 1

    _status(status_path, run_id=run_id, requested=requested, completed=completed, state="completed", child_pid=child.pid if child else None)
    if not config.get("omit_summary"):
        summary_path.write_text(
            f"# Simulated validation\n\nRun `{run_id}` completed {completed}/{requested} cycles.\n",
            encoding="utf-8",
        )
    return final_exit


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--evidence-dir", required=True)
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    if not isinstance(config, Mapping):
        raise ValueError("validator config must be an object")
    return run(config, Path(args.evidence_dir))


if __name__ == "__main__":
    sys.exit(main())
