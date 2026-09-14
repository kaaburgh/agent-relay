from __future__ import annotations

import argparse
import os
import sys
from typing import Sequence


ABANDONED_LAUNCH_EXIT = 125


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gate-fd", required=True, type=int)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("launch gate: missing command", file=sys.stderr)
        return 64

    try:
        signal_byte = os.read(args.gate_fd, 1)
    finally:
        try:
            os.close(args.gate_fd)
        except OSError:
            pass
    if signal_byte != b"1":
        # Parent disappeared or declined the durable launch claim. Never exec side effects.
        return ABANDONED_LAUNCH_EXIT

    try:
        os.execvpe(command[0], command, os.environ)
    except OSError as exc:
        print(f"launch gate: exec failed: {exc}", file=sys.stderr)
        return 127


if __name__ == "__main__":
    raise SystemExit(main())
