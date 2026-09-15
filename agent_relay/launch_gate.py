from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path
from typing import Sequence


ABANDONED_LAUNCH_EXIT = 125


def _durably_authorized(state_db: Path, attempt_id: int) -> bool:
    if not state_db.is_file():
        return False
    try:
        connection = sqlite3.connect(
            f"file:{state_db}?mode=ro",
            uri=True,
            timeout=5.0,
        )
        connection.row_factory = sqlite3.Row
        try:
            row = connection.execute(
                """
                SELECT lc.authorized_at,
                       lc.expected_task_stage,
                       lc.expected_task_updated_at,
                       lc.expected_generation,
                       a.status AS attempt_status,
                       a.ended_at AS attempt_ended_at,
                       t.stage AS task_stage,
                       t.updated_at AS task_updated_at,
                       t.current_generation AS task_generation
                FROM launch_claims lc
                JOIN attempts a ON a.attempt_id=lc.attempt_id
                JOIN tasks t ON t.task_id=lc.task_id
                WHERE lc.attempt_id=?
                """,
                (attempt_id,),
            ).fetchone()
        finally:
            connection.close()
    except (OSError, sqlite3.Error):
        return False

    if row is None or row["authorized_at"] is None:
        return False
    if row["attempt_ended_at"] is not None or row["attempt_status"] != "RUNNING":
        return False
    return (
        row["expected_task_stage"] == row["task_stage"]
        and row["expected_task_updated_at"] == row["task_updated_at"]
        and row["expected_generation"] == row["task_generation"]
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gate-fd", required=True, type=int)
    parser.add_argument("--state-db", required=True, type=Path)
    parser.add_argument("--attempt-id", required=True, type=int)
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

    if signal_byte not in {b"", b"1"}:
        return ABANDONED_LAUNCH_EXIT

    # The byte is only a wake-up optimization. Durable SQLite authorization is the actual
    # execution fence, so a parent crash after commit but before writing the byte is safe:
    # EOF still allows the already-authorized target to execute, while every stale/cancelled
    # launch fails closed.
    if not _durably_authorized(args.state_db, args.attempt_id):
        return ABANDONED_LAUNCH_EXIT

    try:
        os.execvpe(command[0], command, os.environ)
    except OSError as exc:
        print(f"launch gate: exec failed: {exc}", file=sys.stderr)
        return 127


if __name__ == "__main__":
    raise SystemExit(main())