from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .store import Store, StoreError


class RuntimeSafetyError(StoreError):
    pass


@dataclass(frozen=True)
class ProcessIdentity:
    boot_id: str
    start_time_ticks: int
    process_group_id: int
    session_id: int


def ensure_runtime_safety_guards(store: Store) -> None:
    """Install durable runtime guards without changing semantic event history.

    This mirrors the evidence module's dynamically-installed DB guards: older databases
    remain readable, while any process-owning runtime upgrades itself before launching work.
    """
    with store._transaction():
        store._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS process_identities (
                process_id INTEGER PRIMARY KEY REFERENCES processes(process_id) ON DELETE RESTRICT,
                boot_id TEXT NOT NULL,
                start_time_ticks INTEGER NOT NULL,
                process_group_id INTEGER NOT NULL,
                session_id INTEGER NOT NULL
            )
            """
        )
        # A task may have historical writer attempts, but only one may own execution now.
        store._conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_writer_owner
            ON attempts(task_id)
            WHERE kind='writer' AND ended_at IS NULL AND status IN ('LAUNCHING','RUNNING')
            """
        )
        # One durable attempt cannot own two concurrently running OS processes.
        store._conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_one_running_process_per_attempt
            ON processes(attempt_id)
            WHERE attempt_id IS NOT NULL AND state='RUNNING'
            """
        )


def _read_boot_id() -> str:
    path = Path("/proc/sys/kernel/random/boot_id")
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeSafetyError(f"cannot read Linux boot identity: {exc}") from exc
    if not value:
        raise RuntimeSafetyError("Linux boot identity is empty")
    return value


def _read_proc_stat(pid: int) -> tuple[str, int, int, int]:
    path = Path(f"/proc/{pid}/stat")
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ProcessLookupError(pid) from exc
    except OSError as exc:
        raise RuntimeSafetyError(f"cannot read process identity for pid {pid}: {exc}") from exc
    close = raw.rfind(")")
    if close < 0:
        raise RuntimeSafetyError(f"malformed /proc/{pid}/stat")
    fields = raw[close + 2 :].split()
    # fields[0] is stat field 3 (state); pgrp/session are 5/6 and starttime is 22.
    if len(fields) <= 19:
        raise RuntimeSafetyError(f"short /proc/{pid}/stat")
    try:
        return fields[0], int(fields[2]), int(fields[3]), int(fields[19])
    except ValueError as exc:
        raise RuntimeSafetyError(f"invalid numeric process identity for pid {pid}") from exc


def capture_process_identity(pid: int) -> ProcessIdentity:
    state, process_group_id, session_id, start_time_ticks = _read_proc_stat(pid)
    if state == "Z":
        raise ProcessLookupError(pid)
    return ProcessIdentity(
        boot_id=_read_boot_id(),
        start_time_ticks=start_time_ticks,
        process_group_id=process_group_id,
        session_id=session_id,
    )


def persist_process_identity(
    store: Store,
    *,
    process_id: int,
    identity: ProcessIdentity,
) -> None:
    """Persist identity inside the caller's existing write transaction."""
    store._conn.execute(
        """
        INSERT INTO process_identities(
            process_id,boot_id,start_time_ticks,process_group_id,session_id
        ) VALUES (?,?,?,?,?)
        """,
        (
            process_id,
            identity.boot_id,
            identity.start_time_ticks,
            identity.process_group_id,
            identity.session_id,
        ),
    )


def load_process_identity(store: Store, process_id: int) -> ProcessIdentity | None:
    table = store._conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='process_identities'"
    ).fetchone()
    if table is None:
        return None
    row = store._conn.execute(
        "SELECT * FROM process_identities WHERE process_id=?", (process_id,)
    ).fetchone()
    if row is None:
        return None
    return ProcessIdentity(
        boot_id=row["boot_id"],
        start_time_ticks=int(row["start_time_ticks"]),
        process_group_id=int(row["process_group_id"]),
        session_id=int(row["session_id"]),
    )


def _owned_group_member_exists(identity: ProcessIdentity) -> bool:
    proc = Path("/proc")
    try:
        entries = tuple(proc.iterdir())
    except OSError:
        return False
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            state, pgrp, session_id, _start = _read_proc_stat(int(entry.name))
        except (ProcessLookupError, RuntimeSafetyError):
            continue
        if (
            state != "Z"
            and pgrp == identity.process_group_id
            and session_id == identity.session_id
        ):
            return True
    return False


def process_ownership_is_live(store: Store, process_row: Any) -> bool:
    """Return true only when the persisted process identity still owns this PID/group.

    Legacy rows without an identity are intentionally not treated as live after restart.
    Numeric PID/PGID existence alone is not durable ownership proof.
    """
    identity = load_process_identity(store, int(process_row["process_id"]))
    if identity is None:
        return False
    try:
        if _read_boot_id() != identity.boot_id:
            return False
    except RuntimeSafetyError:
        return False

    pid = int(process_row["pid"])
    try:
        state, pgrp, session_id, start_time_ticks = _read_proc_stat(pid)
    except ProcessLookupError:
        return _owned_group_member_exists(identity)
    except RuntimeSafetyError:
        return False
    return (
        state != "Z"
        and start_time_ticks == identity.start_time_ticks
        and pgrp == identity.process_group_id
        and session_id == identity.session_id
    )


def process_identity_debug(store: Store, process_id: int) -> str:
    identity = load_process_identity(store, process_id)
    if identity is None:
        return "unverified legacy process identity"
    return json.dumps(
        {
            "boot_id": identity.boot_id,
            "start_time_ticks": identity.start_time_ticks,
            "process_group_id": identity.process_group_id,
            "session_id": identity.session_id,
        },
        sort_keys=True,
    )
