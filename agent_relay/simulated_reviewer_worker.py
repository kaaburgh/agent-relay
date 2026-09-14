from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temp.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def _run(steps: Sequence[Mapping[str, Any]], output: Path) -> int:
    for step in steps:
        action = step.get("action")
        if action == "sleep":
            time.sleep(float(step.get("seconds", 0)))
        elif action == "review":
            payload = step.get("value")
            _atomic_write(output, json.dumps(payload, sort_keys=True) + "\n")
        elif action == "raw":
            _atomic_write(output, str(step.get("text", "")))
        elif action == "provider_unavailable":
            _atomic_write(output, json.dumps({"provider_unavailable": True, "reason": str(step.get("reason", "provider unavailable"))}) + "\n")
        elif action == "failure":
            _atomic_write(output, json.dumps({"failure": True, "reason": str(step.get("reason", "simulated reviewer failure"))}) + "\n")
            return int(step.get("exit_code", 1))
        elif action == "crash":
            return int(step.get("exit_code", 17))
        elif action == "hang":
            while True:
                time.sleep(60)
        else:
            raise ValueError(f"unsupported simulated reviewer action: {action!r}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    steps = json.loads(Path(args.script).read_text(encoding="utf-8"))
    if not isinstance(steps, list):
        raise ValueError("reviewer behavior must be a list")
    return _run(steps, Path(args.output))


if __name__ == "__main__":
    sys.exit(main())
