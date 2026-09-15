from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temp.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        _atomic_json(
            args.checkpoint,
            {"run_id": args.run_id, "exit_status": 64, "error": "missing command"},
        )
        return 64

    try:
        child = subprocess.Popen(command)
    except OSError as exc:
        _atomic_json(
            args.checkpoint,
            {"run_id": args.run_id, "exit_status": 127, "error": str(exc)},
        )
        return 127

    returncode = child.wait()
    _atomic_json(
        args.checkpoint,
        {"run_id": args.run_id, "exit_status": int(returncode)},
    )
    if returncode < 0:
        return min(255, 128 + (-returncode))
    return int(returncode)


if __name__ == "__main__":
    raise SystemExit(main())
