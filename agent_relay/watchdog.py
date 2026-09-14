from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Iterable

from .supervisor import ManagedProcess, ProcessResult


class WatchdogError(ValueError):
    pass


def file_progress_token(paths: Iterable[str | Path]) -> tuple[tuple[str, bool, int | None, int | None], ...]:
    """Return a cheap token that changes when watched evidence files appear or change."""
    records: list[tuple[str, bool, int | None, int | None]] = []
    for value in paths:
        path = Path(value)
        try:
            stat = path.stat()
        except FileNotFoundError:
            records.append((str(path), False, None, None))
        else:
            records.append((str(path), True, stat.st_size, stat.st_mtime_ns))
    if not records:
        raise WatchdogError("at least one progress path is required")
    return tuple(records)


async def wait_with_file_progress_watchdog(
    process: ManagedProcess,
    *,
    progress_paths: Iterable[str | Path],
    stall_timeout_seconds: float,
    poll_interval_seconds: float = 1.0,
) -> ProcessResult:
    """Wait for a managed process, terminating its process group if evidence stops changing.

    Process liveness heartbeats deliberately do not count as progress. The watchdog observes
    only the supplied evidence paths, so a process can remain alive while still being judged
    stalled. The managed process's absolute stage-timeout deadline remains authoritative and
    is checked independently of stall progress. Polling emits no semantic events.
    """
    if stall_timeout_seconds <= 0:
        raise WatchdogError("stall_timeout_seconds must be positive")
    if poll_interval_seconds <= 0:
        raise WatchdogError("poll_interval_seconds must be positive")
    if poll_interval_seconds > stall_timeout_seconds:
        raise WatchdogError("poll interval must not exceed stall timeout")

    paths = tuple(Path(value) for value in progress_paths)
    token = file_progress_token(paths)
    last_progress = time.monotonic()
    waiter = asyncio.create_task(process.process.wait())
    try:
        while True:
            now = time.monotonic()
            deadline = process.timeout_deadline
            if deadline is not None and now >= deadline:
                return await process.expire_timeout()

            wait_seconds = poll_interval_seconds
            if deadline is not None:
                wait_seconds = min(wait_seconds, max(0.0, deadline - now))
            done, _ = await asyncio.wait({waiter}, timeout=wait_seconds)
            if done:
                return await process.wait()

            now = time.monotonic()
            deadline = process.timeout_deadline
            if deadline is not None and now >= deadline:
                return await process.expire_timeout()

            current = file_progress_token(paths)
            if current != token:
                token = current
                last_progress = now
                continue
            if now - last_progress >= stall_timeout_seconds:
                return await process.terminate(state="STALLED")
    finally:
        if not waiter.done():
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)
