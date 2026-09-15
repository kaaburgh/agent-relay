from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .store import Store, StoreError, utc_now


class RuntimeSafetyError(StoreError):
    pass


@dataclass(frozen=True)
class ProcessIdentity:
    boot_id: str
    start_time_ticks: int
    process_group_id: int
    session_id: int


def _ensure_launch_claim_columns(store: Store) -> None:
    columns = {
        row["name"]
        for row in store._conn.execute("PRAGMA table_info(launch_claims)").fetchall()
    }
    additions = {
        "expected_task_stage": "TEXT",
        "expected_task_updated_at": "TEXT",
        "expected_generation": "INTEGER",
        "authorized_at": "TEXT",
    }
    for name, sql_type in additions.items():
        if name not in columns:
            store._conn.execute(f"ALTER TABLE launch_claims ADD COLUMN {name} {sql_type}")


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
        store._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS launch_claims (
                attempt_id INTEGER PRIMARY KEY REFERENCES attempts(attempt_id) ON DELETE RESTRICT,
                task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
                owner_pid INTEGER NOT NULL,
                owner_boot_id TEXT NOT NULL,
                owner_start_time_ticks INTEGER NOT NULL,
                claimed_at TEXT NOT NULL,
                expected_task_stage TEXT,
                expected_task_updated_at TEXT,
                expected_generation INTEGER,
                authorized_at TEXT
            )
            """
        )
        _ensure_launch_claim_columns(store)
        store._conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_writer_owner
            ON attempts(task_id)
            WHERE kind='writer' AND ended_at IS NULL AND status IN ('LAUNCHING','RUNNING')
            """
        )
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


def _read_proc_fields(pid: int) -> list[str]:
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
    if len(fields) <= 19:
        raise RuntimeSafetyError(f"short /proc/{pid}/stat")
    return fields


def _read_proc_stat(pid: int) -> tuple[str, int, int, int]:
    fields = _read_proc_fields(pid)
    try:
        # fields[0] is stat field 3 (state); pgrp/session are 5/6 and starttime is 22.
        return fields[0], int(fields[2]), int(fields[3]), int(fields[19])
    except ValueError as exc:
        raise RuntimeSafetyError(f"invalid numeric process identity for pid {pid}") from exc


def _legacy_direct_child_is_live(pid: int) -> bool:
    """Compatibility only for old synthetic fixtures that never published identity.

    A real supervisor-managed RUNNING attempt is never left in CREATED state. We therefore
    permit PID-only liveness only for that impossible production combination, and only while
    the process is a direct child of this same Python process. It cannot authorize restart
    ownership or operator signalling.
    """
    try:
        fields = _read_proc_fields(pid)
        state = fields[0]
        parent_pid = int(fields[1])  # stat field 4
    except (ProcessLookupError, RuntimeSafetyError, ValueError):
        return False
    return state != "Z" and parent_pid == os.getpid()


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


def pid_matches_identity(pid: int, *, boot_id: str, start_time_ticks: int) -> bool:
    try:
        if _read_boot_id() != boot_id:
            return False
        state, _pgrp, _session, observed_start = _read_proc_stat(pid)
    except (ProcessLookupError, RuntimeSafetyError):
        return False
    return state != "Z" and observed_start == start_time_ticks


def claim_attempt_launch(store: Store, *, task_id: str, attempt_id: int) -> None:
    """Durably claim one attempt before any side-effecting command can exec."""
    owner = capture_process_identity(os.getpid())
    with store._transaction():
        task = store._conn.execute(
            "SELECT stage,updated_at,current_generation FROM tasks WHERE task_id=?",
            (task_id,),
        ).fetchone()
        if task is None:
            raise RuntimeSafetyError(f"unknown task: {task_id}")
        attempt = store._conn.execute(
            "SELECT task_id,kind,generation,status,ended_at,started_at FROM attempts WHERE attempt_id=?",
            (attempt_id,),
        ).fetchone()
        if attempt is None or attempt["task_id"] != task_id:
            raise RuntimeSafetyError("attempt does not belong to task")
        if attempt["ended_at"] is not None:
            raise RuntimeSafetyError("cannot launch a finalized attempt")

        if attempt["status"] == "LAUNCHING":
            claim = store._conn.execute(
                "SELECT * FROM launch_claims WHERE attempt_id=?", (attempt_id,)
            ).fetchone()
            if claim is not None and pid_matches_identity(
                int(claim["owner_pid"]),
                boot_id=claim["owner_boot_id"],
                start_time_ticks=int(claim["owner_start_time_ticks"]),
            ):
                raise RuntimeSafetyError("attempt already has a live launch owner")
            if claim is not None and claim["authorized_at"] is not None:
                raise RuntimeSafetyError("authorized launch ownership cannot be reclaimed speculatively")
            store._conn.execute("DELETE FROM launch_claims WHERE attempt_id=?", (attempt_id,))
            store._conn.execute(
                "UPDATE attempts SET status='CREATED' WHERE attempt_id=? AND status='LAUNCHING' AND ended_at IS NULL",
                (attempt_id,),
            )
            attempt = store._conn.execute(
                "SELECT task_id,kind,generation,status,ended_at,started_at FROM attempts WHERE attempt_id=?",
                (attempt_id,),
            ).fetchone()

        if attempt["status"] != "CREATED":
            raise RuntimeSafetyError(
                f"attempt is not launchable from status {attempt['status']!r}"
            )
        if attempt["started_at"] is None:
            raise RuntimeSafetyError("attempt has no durable allocation timestamp")
        # Attempt allocation snapshots the current task epoch implicitly: normal providers
        # allocate and immediately launch. Any task mutation after that allocation makes the
        # attempt stale, regardless of stage names or provider kind. This prevents delayed
        # callers from launching after cancellation/stage advance without coupling the
        # supervisor to the workflow enum or blocking standalone provider-component tests.
        if task["updated_at"] > attempt["started_at"]:
            raise RuntimeSafetyError("task workflow state changed since attempt allocation")

        if attempt["kind"] == "writer":
            active = store._conn.execute(
                """
                SELECT attempt_id FROM attempts
                WHERE task_id=? AND kind='writer' AND ended_at IS NULL
                  AND status IN ('LAUNCHING','RUNNING') AND attempt_id<>?
                LIMIT 1
                """,
                (task_id, attempt_id),
            ).fetchone()
            if active is not None:
                raise RuntimeSafetyError(
                    f"task already has active writer attempt {active['attempt_id']}"
                )

        cursor = store._conn.execute(
            "UPDATE attempts SET status='LAUNCHING' WHERE attempt_id=? AND status='CREATED' AND ended_at IS NULL",
            (attempt_id,),
        )
        if cursor.rowcount != 1:
            raise RuntimeSafetyError("attempt changed before launch claim could be committed")
        store._conn.execute(
            """
            INSERT INTO launch_claims(
                attempt_id,task_id,owner_pid,owner_boot_id,owner_start_time_ticks,claimed_at,
                expected_task_stage,expected_task_updated_at,expected_generation,authorized_at
            ) VALUES (?,?,?,?,?,?,?,?,?,NULL)
            """,
            (
                attempt_id,
                task_id,
                os.getpid(),
                owner.boot_id,
                owner.start_time_ticks,
                utc_now(),
                task["stage"],
                task["updated_at"],
                int(task["current_generation"]),
            ),
        )


def release_attempt_launch_claim(store: Store, *, task_id: str, attempt_id: int) -> None:
    owner = capture_process_identity(os.getpid())
    with store._transaction():
        claim = store._conn.execute(
            "SELECT * FROM launch_claims WHERE attempt_id=?", (attempt_id,)
        ).fetchone()
        if claim is None:
            return
        if claim["task_id"] != task_id:
            raise RuntimeSafetyError("launch claim belongs to another task")
        if (
            int(claim["owner_pid"]) != os.getpid()
            or claim["owner_boot_id"] != owner.boot_id
            or int(claim["owner_start_time_ticks"]) != owner.start_time_ticks
        ):
            raise RuntimeSafetyError("cannot release another process's launch claim")
        if claim["authorized_at"] is not None:
            raise RuntimeSafetyError("cannot roll back an already-authorized launch claim")

        task = store._conn.execute(
            "SELECT stage,updated_at,current_generation FROM tasks WHERE task_id=?",
            (task_id,),
        ).fetchone()
        store._conn.execute("DELETE FROM launch_claims WHERE attempt_id=?", (attempt_id,))
        if task is None:
            return
        snapshot_matches = (
            claim["expected_task_stage"] == task["stage"]
            and claim["expected_task_updated_at"] == task["updated_at"]
            and claim["expected_generation"] == task["current_generation"]
        )
        if snapshot_matches:
            store._conn.execute(
                "UPDATE attempts SET status='CREATED' WHERE attempt_id=? AND status='LAUNCHING' AND ended_at IS NULL",
                (attempt_id,),
            )
            return

        terminal_status = "CANCELLED" if task["stage"] == "CANCELLED" else "STALE_LAUNCH"
        store._conn.execute(
            """
            UPDATE attempts SET status=?,ended_at=?
            WHERE attempt_id=? AND status='LAUNCHING' AND ended_at IS NULL
            """,
            (terminal_status, utc_now(), attempt_id),
        )


def verify_launch_claim_owned_by_current_process(store: Store, *, attempt_id: int) -> None:
    owner = capture_process_identity(os.getpid())
    row = store._conn.execute(
        """
        SELECT lc.*, t.stage AS current_stage, t.updated_at AS current_updated_at,
               t.current_generation AS current_generation,
               a.status AS attempt_status, a.ended_at AS attempt_ended_at
        FROM launch_claims lc
        JOIN tasks t ON t.task_id=lc.task_id
        JOIN attempts a ON a.attempt_id=lc.attempt_id
        WHERE lc.attempt_id=?
        """,
        (attempt_id,),
    ).fetchone()
    if row is None:
        raise RuntimeSafetyError("launch claim disappeared before process publication")
    if (
        int(row["owner_pid"]) != os.getpid()
        or row["owner_boot_id"] != owner.boot_id
        or int(row["owner_start_time_ticks"]) != owner.start_time_ticks
    ):
        raise RuntimeSafetyError("launch claim ownership changed before process publication")
    if row["authorized_at"] is not None:
        raise RuntimeSafetyError("launch claim is already authorized")
    if row["attempt_ended_at"] is not None or row["attempt_status"] != "LAUNCHING":
        raise RuntimeSafetyError("attempt launch state changed before process publication")
    if (
        row["expected_task_stage"] != row["current_stage"]
        or row["expected_task_updated_at"] != row["current_updated_at"]
        or row["expected_generation"] != row["current_generation"]
    ):
        raise RuntimeSafetyError("task workflow state changed before process publication")


def authorize_launch_claim_in_transaction(store: Store, *, attempt_id: int) -> None:
    if not store._conn.in_transaction:
        raise RuntimeSafetyError("launch authorization requires an active store transaction")
    verify_launch_claim_owned_by_current_process(store, attempt_id=attempt_id)
    cursor = store._conn.execute(
        "UPDATE launch_claims SET authorized_at=? WHERE attempt_id=? AND authorized_at IS NULL",
        (utc_now(), attempt_id),
    )
    if cursor.rowcount != 1:
        raise RuntimeSafetyError("launch claim changed before authorization")


def clear_launch_claim_for_process_in_transaction(store: Store, *, process_id: int) -> None:
    if not store._conn.in_transaction:
        raise RuntimeSafetyError("launch-claim cleanup requires an active store transaction")
    store._conn.execute(
        """
        DELETE FROM launch_claims
        WHERE attempt_id=(SELECT attempt_id FROM processes WHERE process_id=?)
        """,
        (process_id,),
    )


def persist_process_identity(
    store: Store,
    *,
    process_id: int,
    identity: ProcessIdentity,
) -> None:
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
    identity = load_process_identity(store, int(process_row["process_id"]))
    if identity is None:
        attempt_id = process_row["attempt_id"]
        if attempt_id is None:
            return False
        attempt = store._conn.execute(
            "SELECT status,ended_at FROM attempts WHERE attempt_id=?", (attempt_id,)
        ).fetchone()
        if (
            attempt is not None
            and attempt["ended_at"] is None
            and attempt["status"] == "CREATED"
        ):
            return _legacy_direct_child_is_live(int(process_row["pid"]))
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
    if (
        start_time_ticks == identity.start_time_ticks
        and pgrp == identity.process_group_id
        and session_id == identity.session_id
    ):
        if state != "Z":
            return True
        return _owned_group_member_exists(identity)
    return False


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