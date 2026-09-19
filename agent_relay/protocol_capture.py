from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence


PROTOCOL_CAPTURE_ERROR_EXIT = 125


def _normalized_exit(returncode: int) -> int:
    if 0 <= returncode <= 255:
        return returncode
    if returncode < 0:
        return min(255, 128 + abs(returncode))
    return PROTOCOL_CAPTURE_ERROR_EXIT


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("protocol capture: missing command", file=sys.stderr)
        return 64

    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        protocol = args.output.open("wb", buffering=0)
    except OSError as exc:
        print(f"protocol capture: cannot open output: {exc}", file=sys.stderr)
        return PROTOCOL_CAPTURE_ERROR_EXIT

    try:
        try:
            child = subprocess.Popen(
                command,
                stdin=sys.stdin.buffer,
                stdout=subprocess.PIPE,
                stderr=sys.stderr.buffer,
                close_fds=True,
            )
        except OSError as exc:
            print(f"protocol capture: exec failed: {exc}", file=sys.stderr)
            return 127

        assert child.stdout is not None
        forward_stdout = True
        try:
            while True:
                chunk = child.stdout.read(64 * 1024)
                if not chunk:
                    break
                protocol.write(chunk)
                if forward_stdout:
                    try:
                        sys.stdout.buffer.write(chunk)
                        sys.stdout.buffer.flush()
                    except (BrokenPipeError, OSError):
                        # The complete provider protocol remains durable even if the
                        # human-readable parent log disappears. Parent-loss input/recovery
                        # semantics are tracked separately by issue #37.
                        forward_stdout = False
        finally:
            child.stdout.close()

        returncode = child.wait()
        protocol.flush()
        os.fsync(protocol.fileno())
        return _normalized_exit(returncode)
    except OSError as exc:
        print(f"protocol capture: output failed: {exc}", file=sys.stderr)
        try:
            child.kill()  # type: ignore[possibly-undefined]
        except (NameError, OSError):
            pass
        return PROTOCOL_CAPTURE_ERROR_EXIT
    finally:
        protocol.close()


if __name__ == "__main__":
    raise SystemExit(main())
