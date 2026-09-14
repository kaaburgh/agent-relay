from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from .runtime_safety import ProcessIdentity, load_process_identity
from .store import Store


OwnershipState = Literal["LIVE", "DEAD", "MISMATCH", "UNVERIFIED"]


def _boot_id() -> str | None:
    try:
        value = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def _stat(pid: int) -> tuple[str, int, int, int] | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        return None
    close = raw.rfind(")")
    if close < 0:
        return None
    fields = raw[close + 2 :].split()
    if len(fields) <= 19:
        return None
    try:
        return fields[0], int(fields[2]), int(fields[3]), int(fields[19])
    except ValueError:
        return None


def _group_has_owned_member(identity: ProcessIdentity) -> bool:
    proc = Path("/proc")
    try:
        entries = tuple(proc.iterdir())
    except OSError:
        return False
    for entry in entries:
        if not entry.name.isdigit():
            continue
        stat = _stat(int(entry.name))
        if stat is None:
            continue
        state, pgrp, session, _start = stat
        if state != "Z" and pgrp == identity.process_group_id and session == identity.session_id:
            return True
    return False


def process_ownership_state(store: Store, process_row: Any) -> OwnershipState:
    identity = load_process_identity(store, int(process_row["process_id"]))
    if identity is None:
        return "UNVERIFIED"
    current_boot = _boot_id()
    if current_boot is None or current_boot != identity.boot_id:
        return "MISMATCH"

    pid = int(process_row["pid"])
    stat = _stat(pid)
    if stat is None:
        return "LIVE" if _group_has_owned_member(identity) else "DEAD"
    state, pgrp, session, start = stat
    if (
        state != "Z"
        and start == identity.start_time_ticks
        and pgrp == identity.process_group_id
        and session == identity.session_id
    ):
        return "LIVE"
    # A live process exists at the recorded PID but it is not the process we launched.
    return "MISMATCH"
