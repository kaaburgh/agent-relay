from __future__ import annotations

import asyncio
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from .artifacts import redact
from .store import Store, StoreError, utc_now


class SupervisorError(RuntimeError):
    pass


DEFAULT_MAX_OUTPUT_BYTES = 8 * 1024 * 1024
_TRUNCATION_MARKER = b"[agent-relay: earlier output truncated; retaining tail]\n"
_CAPTURE_DRAIN_GRACE_SECONDS = 1.0
_LEADER_REAP_GRACE_SECONDS = 1.0


@dataclass(frozen=True)
class ProcessResult:
    process_id: int
    pid: int
    process_group_id: int
    returncode: int
    state: str
    started_at: str
    ended_at: str
    timed_out: bool = False
    forced_kill: bool = False
    stalled: bool = False


async def _capture_bounded_tail(
    stream: asyncio.StreamReader,
    path: Path,
    *,
    max_bytes: int,
) -> None:
    """Continuously drain a subprocess stream while bounding durable log growth."""
    tail_limit = max_bytes - len(_TRUNCATION_MARKER)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)

    existing_size = path.stat().st_size
    truncated = existing_size > max_bytes
    tail = bytearray()
    output = None
    stored_size = existing_size

    if truncated:
        with path.open("rb") as source:
            if existing_size > tail_limit:
                source.seek(-tail_limit, os.SEEK_END)
            tail.extend(source.read())
        if len(tail) > tail_limit:
            del tail[:-tail_limit]
        with path.open("wb") as sink:
            sink.write(_TRUNCATION_MARKER)
            sink.write(tail)
    else:
        output = path.open("ab", buffering=0)

    try:
        while True:
            chunk = await stream.read(64 * 1024)
            if not chunk:
                break
            if not truncated and stored_size + len(chunk) <= max_bytes:
                assert output is not None
                output.write(chunk)
                stored_size += len(chunk)
                continue

            if not truncated:
                assert output is not None
                output.close()
                output = None
                with path.open("rb") as source:
                    if stored_size > tail_limit:
                        source.seek(-tail_limit, os.SEEK_END)
                    tail.extend(source.read())
                truncated = True

            tail.extend(chunk)
            if len(tail) > tail_limit:
                del tail[:-tail_limit]
    finally:
        if output is not None:
            output.close()

    if truncated:
        with path.open("wb") as sink:
            sink.write(_TRUNCATION_MARKER)
            sink.write(tail[-tail_limit:])


class ManagedProcess:
    def __init__(
        self,
        *,
        store: Store,
        process_id: int,
        process: asyncio.subprocess.Process,
        process_group_id: int,
        capture_tasks: tuple[asyncio.Task[None], ...],
        capture_streams: tuple[asyncio.StreamReader, ...],
        started_at: str,
        started_monotonic: float,
        timeout_seconds: float | None,
        terminate_grace_seconds: float,
        heartbeat_interval: float,
    ) -> None:
        self.store = store
        self.process_id = process_id
        self.process = process
        self.process_group_id = process_group_id
        self.capture_tasks = capture_tasks
        self.capture_streams = capture_streams
        self.started_at = started_at
        self.started_monotonic = started_monotonic
        self.timeout_seconds = timeout_seconds
        self.terminate_grace_seconds = terminate_grace_seconds
        self.heartbeat_interval = heartbeat_interval
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._finish_lock = asyncio.Lock()
        self._result: ProcessResult | None = None

    @property
    def pid(self) -> int:
        return int(self.process.pid)

    @property
    def timeout_deadline(self) -> float | None:
        if self.timeout_seconds is None:
            return None
        return self.started_monotonic + self.timeout_seconds

    async def _heartbeat_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.heartbeat_interval)
                if self.process.returncode is not None:
                    return
                with self.store._transaction():
                    self.store._conn.execute(
                        "UPDATE processes SET last_liveness_at=? WHERE process_id=? AND ended_at IS NULL",
                        (utc_now(), self.process_id),
                    )
        except asyncio.CancelledError:
            return

    async def _wait_for_leader_exit(self, deadline: float | None = None) -> bool:
        """Observe leader exit without waiting for inherited stdout/stderr pipes to close.

        asyncio's Process.wait() can remain pending after the direct child exits when one of
        its descendants inherited a PIPE fd. ``returncode`` is set by the child watcher as
        soon as the direct child exits, so it is the correct liveness signal for the leader.
        """
        while self.process.returncode is None:
            if deadline is None:
                await asyncio.sleep(0.01)
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(0.01, remaining))
        return True

    def _group_exists(self) -> bool:
        try:
            os.killpg(self.process_group_id, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    async def _terminate_group(self) -> bool:
        """Terminate every process still in the managed process group."""
        if not self._group_exists():
            if self.process.returncode is None:
                exited = await self._wait_for_leader_exit(
                    time.monotonic() + _LEADER_REAP_GRACE_SECONDS
                )
                if not exited:
                    raise SupervisorError("process group vanished but leader exit was not observed")
            return False
        try:
            os.killpg(self.process_group_id, signal.SIGTERM)
        except ProcessLookupError:
            if self.process.returncode is None:
                exited = await self._wait_for_leader_exit(
                    time.monotonic() + _LEADER_REAP_GRACE_SECONDS
                )
                if not exited:
                    raise SupervisorError("process group vanished but leader exit was not observed")
            return False

        deadline = time.monotonic() + self.terminate_grace_seconds
        while time.monotonic() < deadline:
            if not self._group_exists():
                if self.process.returncode is None:
                    await self._wait_for_leader_exit(
                        time.monotonic() + _LEADER_REAP_GRACE_SECONDS
                    )
                return False
            await asyncio.sleep(min(0.01, max(0.0, deadline - time.monotonic())))

        forced = False
        if self._group_exists():
            try:
                os.killpg(self.process_group_id, signal.SIGKILL)
                forced = True
            except ProcessLookupError:
                pass
        if self.process.returncode is None:
            exited = await self._wait_for_leader_exit(
                time.monotonic() + _LEADER_REAP_GRACE_SECONDS
            )
            if not exited:
                raise SupervisorError("leader did not exit after process-group termination")
        return forced

    async def _finish(
        self,
        state: str,
        *,
        timed_out: bool,
        forced_kill: bool,
        stalled: bool = False,
    ) -> ProcessResult:
        async with self._finish_lock:
            if self._result is not None:
                return self._result

            self._heartbeat_task.cancel()
            await asyncio.gather(self._heartbeat_task, return_exceptions=True)

            capture_failed = False
            if self.capture_tasks:
                capture_group = asyncio.gather(*self.capture_tasks, return_exceptions=True)
                try:
                    outcomes = await asyncio.wait_for(
                        capture_group, timeout=_CAPTURE_DRAIN_GRACE_SECONDS
                    )
                except asyncio.TimeoutError:
                    capture_failed = True
                    for stream in self.capture_streams:
                        transport = getattr(stream, "_transport", None)
                        if transport is not None:
                            transport.close()
                    for task in self.capture_tasks:
                        task.cancel()
                    await asyncio.gather(*self.capture_tasks, return_exceptions=True)
                else:
                    capture_failed = any(isinstance(value, BaseException) for value in outcomes)

            ended_at = utc_now()
            returncode = self.process.returncode
            if returncode is None:
                raise SupervisorError("cannot finalize a running process")

            requested_state = state
            if capture_failed and requested_state == "SUCCEEDED":
                requested_state = "FAILED"

            final_state = requested_state
            final_ended_at = ended_at
            final_exit_status: int | None = returncode
            durable_was_already_terminal = False
            with self.store._transaction():
                cursor = self.store._conn.execute(
                    """
                    UPDATE processes
                    SET state=?, ended_at=?, exit_status=?, last_liveness_at=?
                    WHERE process_id=? AND ended_at IS NULL
                    """,
                    (requested_state, ended_at, returncode, ended_at, self.process_id),
                )
                if cursor.rowcount != 1:
                    row = self.store._conn.execute(
                        "SELECT state,ended_at,exit_status FROM processes WHERE process_id=?",
                        (self.process_id,),
                    ).fetchone()
                    if row is None:
                        raise StoreError(f"process {self.process_id} is missing")
                    if row["ended_at"] is None:
                        raise StoreError(
                            f"process {self.process_id} changed without reaching a terminal state"
                        )
                    durable_was_already_terminal = True
                    final_state = row["state"]
                    final_ended_at = row["ended_at"]
                    final_exit_status = row["exit_status"]
                    if final_exit_status is None:
                        self.store._conn.execute(
                            """
                            UPDATE processes SET exit_status=?,last_liveness_at=?
                            WHERE process_id=? AND ended_at=? AND exit_status IS NULL
                            """,
                            (returncode, ended_at, self.process_id, final_ended_at),
                        )
                        final_exit_status = returncode

            result = ProcessResult(
                process_id=self.process_id,
                pid=self.pid,
                process_group_id=self.process_group_id,
                returncode=int(final_exit_status if final_exit_status is not None else returncode),
                state=final_state,
                started_at=self.started_at,
                ended_at=final_ended_at,
                timed_out=final_state == "TIMED_OUT",
                forced_kill=(forced_kill if not durable_was_already_terminal else False),
                stalled=final_state == "STALLED",
            )
            self._result = result
            return result

    async def expire_timeout(self) -> ProcessResult:
        if self.timeout_seconds is None:
            raise SupervisorError("cannot expire timeout for a process without a timeout")
        forced = await self._terminate_group()
        return await self._finish("TIMED_OUT", timed_out=True, forced_kill=forced)

    async def wait(self) -> ProcessResult:
        try:
            deadline = self.timeout_deadline
            exited = await self._wait_for_leader_exit(deadline)
            if not exited:
                return await self.expire_timeout()
        except asyncio.CancelledError:
            forced = await self._terminate_group()
            await self._finish("CANCELLED", timed_out=False, forced_kill=forced)
            raise

        # The direct child is gone, but descendants may still hold process-group membership
        # and inherited pipe descriptors. Clean them before draining/finalizing evidence.
        forced = await self._terminate_group()
        state = "SUCCEEDED" if self.process.returncode == 0 else "FAILED"
        return await self._finish(state, timed_out=False, forced_kill=forced)

    async def terminate(self, *, state: str = "TERMINATED") -> ProcessResult:
        if state not in {"TERMINATED", "STALLED", "CANCELLED"}:
            raise SupervisorError(f"unsupported explicit termination state: {state}")
        forced = await self._terminate_group()
        return await self._finish(
            state,
            timed_out=False,
            forced_kill=forced,
            stalled=state == "STALLED",
        )


class SubprocessSupervisor:
    def __init__(self, store: Store) -> None:
        self.store = store

    async def start(
        self,
        *,
        task_id: str,
        argv: Sequence[str],
        cwd: str | Path,
        stdout_path: str | Path,
        stderr_path: str | Path,
        attempt_id: int | None = None,
        env_additions: Mapping[str, str] | None = None,
        stdin_text: str | None = None,
        timeout_seconds: float | None = None,
        terminate_grace_seconds: float = 5.0,
        heartbeat_interval: float = 30.0,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    ) -> ManagedProcess:
        if not argv or any(not isinstance(item, str) or not item for item in argv):
            raise SupervisorError("argv must contain non-empty strings")
        if stdin_text is not None and not isinstance(stdin_text, str):
            raise SupervisorError("stdin_text must be a string when set")
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise SupervisorError("timeout_seconds must be positive when set")
        if terminate_grace_seconds <= 0:
            raise SupervisorError("terminate_grace_seconds must be positive")
        if heartbeat_interval <= 0:
            raise SupervisorError("heartbeat_interval must be positive")
        if isinstance(max_output_bytes, bool) or not isinstance(max_output_bytes, int):
            raise SupervisorError("max_output_bytes must be an integer")
        if max_output_bytes < len(_TRUNCATION_MARKER) + 128:
            raise SupervisorError("max_output_bytes is too small for bounded tail capture")

        workdir = Path(cwd)
        if not workdir.is_dir():
            raise SupervisorError(f"cwd is not a directory: {workdir}")
        out_path = Path(stdout_path)
        err_path = Path(stderr_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        err_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.touch(exist_ok=True)
        err_path.touch(exist_ok=True)
        same_output = out_path.resolve() == err_path.resolve()

        environment = os.environ.copy()
        environment.update(dict(env_additions or {}))
        started_at = utc_now()
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(workdir),
            env=environment,
            stdin=(asyncio.subprocess.PIPE if stdin_text is not None else asyncio.subprocess.DEVNULL),
            stdout=asyncio.subprocess.PIPE,
            stderr=(asyncio.subprocess.STDOUT if same_output else asyncio.subprocess.PIPE),
            start_new_session=True,
        )

        if process.stdout is None:
            try:
                os.killpg(int(process.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()
            raise SupervisorError("subprocess stdout pipe was not created")

        capture_streams: list[asyncio.StreamReader] = [process.stdout]
        capture_tasks: list[asyncio.Task[None]] = [
            asyncio.create_task(
                _capture_bounded_tail(process.stdout, out_path, max_bytes=max_output_bytes)
            )
        ]
        if not same_output:
            if process.stderr is None:
                try:
                    os.killpg(int(process.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
                for task in capture_tasks:
                    task.cancel()
                await asyncio.gather(*capture_tasks, return_exceptions=True)
                raise SupervisorError("subprocess stderr pipe was not created")
            capture_streams.append(process.stderr)
            capture_tasks.append(
                asyncio.create_task(
                    _capture_bounded_tail(process.stderr, err_path, max_bytes=max_output_bytes)
                )
            )

        started_monotonic = time.monotonic()
        process_group_id = int(process.pid)
        safe_command = redact(list(argv))
        try:
            with self.store._transaction():
                if self.store._conn.execute(
                    "SELECT 1 FROM tasks WHERE task_id=?", (task_id,)
                ).fetchone() is None:
                    raise StoreError(f"unknown task: {task_id}")
                if attempt_id is not None:
                    attempt = self.store._conn.execute(
                        "SELECT task_id, ended_at FROM attempts WHERE attempt_id=?", (attempt_id,)
                    ).fetchone()
                    if attempt is None or attempt["task_id"] != task_id:
                        raise StoreError("attempt does not belong to task")
                    if attempt["ended_at"] is not None:
                        raise StoreError("cannot attach process to finalized attempt")
                cursor = self.store._conn.execute(
                    """
                    INSERT INTO processes(
                        task_id, attempt_id, pid, process_group_id, state,
                        command_json, started_at, last_liveness_at
                    ) VALUES (?, ?, ?, ?, 'RUNNING', ?, ?, ?)
                    """,
                    (
                        task_id,
                        attempt_id,
                        process.pid,
                        process_group_id,
                        __import__("json").dumps(safe_command, sort_keys=True, separators=(",", ":")),
                        started_at,
                        started_at,
                    ),
                )
                process_id = int(cursor.lastrowid)
                if attempt_id is not None:
                    self.store._conn.execute(
                        "UPDATE attempts SET pid=?, status='RUNNING' WHERE attempt_id=?",
                        (process.pid, attempt_id),
                    )
        except BaseException:
            try:
                os.killpg(process_group_id, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()
            await asyncio.gather(*capture_tasks, return_exceptions=True)
            raise

        if stdin_text is not None:
            if process.stdin is None:
                try:
                    os.killpg(process_group_id, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
                await asyncio.gather(*capture_tasks, return_exceptions=True)
                raise SupervisorError("subprocess stdin pipe was not created")
            try:
                process.stdin.write(stdin_text.encode("utf-8"))
                await process.stdin.drain()
                process.stdin.close()
                await process.stdin.wait_closed()
            except (BrokenPipeError, ConnectionResetError) as exc:
                try:
                    os.killpg(process_group_id, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
                await asyncio.gather(*capture_tasks, return_exceptions=True)
                ended_at = utc_now()
                with self.store._transaction():
                    self.store._conn.execute(
                        """
                        UPDATE processes
                        SET state='FAILED',ended_at=?,exit_status=?,last_liveness_at=?
                        WHERE process_id=? AND ended_at IS NULL
                        """,
                        (ended_at, process.returncode, ended_at, process_id),
                    )
                    if attempt_id is not None:
                        self.store._conn.execute(
                            """
                            UPDATE attempts
                            SET status='PROCESS_FAILURE',ended_at=?,exit_status=?
                            WHERE attempt_id=? AND ended_at IS NULL
                            """,
                            (ended_at, process.returncode, attempt_id),
                        )
                raise SupervisorError("subprocess exited before stdin could be delivered") from exc

        return ManagedProcess(
            store=self.store,
            process_id=process_id,
            process=process,
            process_group_id=process_group_id,
            capture_tasks=tuple(capture_tasks),
            capture_streams=tuple(capture_streams),
            started_at=started_at,
            started_monotonic=started_monotonic,
            timeout_seconds=timeout_seconds,
            terminate_grace_seconds=terminate_grace_seconds,
            heartbeat_interval=heartbeat_interval,
        )
