from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping


class SimulationScriptError(RuntimeError):
    pass


def _inside(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    resolved_root = root.resolve()
    if candidate != resolved_root and resolved_root not in candidate.parents:
        raise SimulationScriptError(f"path escapes writer worktree: {relative!r}")
    return candidate


def _git(worktree: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(worktree), *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SimulationScriptError(
            f"git command failed ({result.returncode}): {args!r}: {result.stderr.strip()}"
        )
    return result.stdout.strip()


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _write_result(path: Path, value: Mapping[str, Any]) -> None:
    _atomic_write(path, json.dumps(dict(value), sort_keys=True) + "\n")


def execute(script: list[Mapping[str, Any]], worktree: Path, result_path: Path) -> int:
    for index, action in enumerate(script):
        if not isinstance(action, Mapping) or len(action) != 1:
            raise SimulationScriptError(f"action {index} must contain exactly one operation")
        operation, value = next(iter(action.items()))

        if operation == "sleep":
            seconds = float(value)
            if seconds < 0:
                raise SimulationScriptError("sleep duration must be non-negative")
            time.sleep(seconds)
        elif operation == "modify_file":
            if not isinstance(value, Mapping):
                raise SimulationScriptError("modify_file value must be a mapping")
            relative = value.get("path")
            content = value.get("content")
            if not isinstance(relative, str) or not relative:
                raise SimulationScriptError("modify_file.path must be a non-empty string")
            if not isinstance(content, str):
                raise SimulationScriptError("modify_file.content must be a string")
            destination = _inside(worktree, relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")
        elif operation == "commit":
            if not isinstance(value, Mapping):
                raise SimulationScriptError("commit value must be a mapping")
            message = value.get("message", "simulated writer candidate")
            if not isinstance(message, str) or not message:
                raise SimulationScriptError("commit.message must be a non-empty string")
            _git(worktree, "add", "-A")
            _git(worktree, "commit", "-m", message)
        elif operation == "result":
            if not isinstance(value, Mapping):
                raise SimulationScriptError("result must be a mapping")
            status = value.get("status")
            if status not in {"success", "failure"}:
                raise SimulationScriptError("result.status must be success or failure")
            payload = dict(value)
            payload.setdefault("worker_pid", os.getpid())
            _write_result(result_path, payload)
            return 0
        elif operation in {"provider_unavailable", "rate_limit"}:
            reason = str(value) if value is not None else operation
            _write_result(
                result_path,
                {
                    "status": "provider_unavailable",
                    "reason": reason,
                    "signal": operation,
                    "worker_pid": os.getpid(),
                },
            )
            return 75
        elif operation == "malformed_result":
            _atomic_write(result_path, str(value))
            return 0
        elif operation == "crash":
            code = int(value) if value is not None else 23
            os._exit(code)
        elif operation == "hang":
            while True:
                time.sleep(3600)
        else:
            raise SimulationScriptError(f"unknown simulated writer operation: {operation}")

    _write_result(
        result_path,
        {"status": "success", "worker_pid": os.getpid(), "implicit_result": True},
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", required=True, type=Path)
    parser.add_argument("--worktree", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        value = json.loads(args.script.read_text(encoding="utf-8"))
        if not isinstance(value, list):
            raise SimulationScriptError("script root must be a list")
        return execute(value, args.worktree, args.result)
    except Exception as exc:
        print(f"simulated writer error: {exc}", file=sys.stderr)
        return 64


if __name__ == "__main__":
    raise SystemExit(main())
