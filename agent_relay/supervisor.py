from __future__ import annotations

import asyncio
import os
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence, TextIO

from .artifacts import redact
from .store import Store, StoreError, utc_now


class SupervisorError(RuntimeError):
    pass


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


class ManagedProcess:
    def __init__(
        self,
        *,
        store: Store,
        process_id: int,
        process: asyncio.subprocess.Process,
        process_group_id: int,
        stdout_file: TextIO,
        stderr_file: TextIO,
        started_at: str,
        timeout_seconds: float | None,
        terminate_grace_seconds: float,
        heartbeat_interval: float,
    ) -> None:
        self.store = store
        self.process_id = process_id
        self.process = process
        self.process_group_id = process_group_id
        self.stdout_file = stdout_file
        self.stderr_file = stderr_file
        self.started_at = started_at
        self.timeout_seconds = timeout_seconds
        self.terminate_grace_seconds = terminate_grace_seconds
        self.heartbeat_interval = heartbeat_interval
        self._finished = False
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    @property
    def pid(self) -> int:
        return int(self.process.pid)

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

    async def _terminate_group(self) -> bool:
        if self.process.returncode is not None:
            return False
        try:
            os.killpg(self.process_group_id, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(self.process.wait(), timeout=self.terminate_grace_seconds)
            return False
        except asyncio.TimeoutError:
            try:
                os.killpg(self.process_group_id, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await self.process.wait()
            return True

    async def _finish(
        self,
        state: str,
        *,
        timed_out: bool,
        forced_kill: bool,
        stalled: bool = False,
    ) -> ProcessResult:
        if self._finished:
            raise SupervisorError(f"process {self.process_id} has already been finalized")
        self._finished = True
        self._heartbeat_task.cancel()
        await asyncio.gather(self._heartbeat_task, return_exceptions=True)
        self.stdout_file.close()
        self.stderr_file.close()
        ended_at = utc_now()
        returncode = self.process.returncode
        if returncode is None:
            raise SupervisorError("cannot finalize a running process")
        with self.store._transaction():
            cursor = self.store._conn.execute(
                """
                UPDATE processes
                SET state=?, ended_at=?, exit_status=?, last_liveness_at=?
                WHERE process_id=? AND ended_at IS NULL
                """,
                (state, ended_at, returncode, ended_at, self.process_id),
            )
            if cursor.rowcount != 1:
                raise StoreError(f"process {self.process_id} is already finalized or missing")
        return ProcessResult(
            process_id=self.process_id,
            pid=self.pid,
            process_group_id=self.process_group_id,
            returncode=returncode,
            state=state,
            started_at=self.started_at,
            ended_at=ended_at,
            timed_out=timed_out,
            forced_kill=forced_kill,
            stalled=stalled,
        )

    async def wait(self) -> ProcessResult:
        try:
            if self.timeout_seconds is None:
                await self.process.wait()
            else:
                try:
                    await asyncio.wait_for(self.process.wait(), timeout=self.timeout_seconds)
                except asyncio.TimeoutError:
                    forced = await self._terminate_group()
                    return await self._finish("TIMED_OUT", timed_out=True, forced_kill=forced)
        except asyncio.CancelledError:
            forced = await self._terminate_group()
            await self._finish("CANCELLED", timed_out=False, forced_kill=forced)
            raise
        state = "SUCCEEDED" if self.process.returncode == 0 else "FAILED"
        return await self._finish(state, timed_out=False, forced_kill=False)

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
        timeout_seconds: float | None = None,
        terminate_grace_seconds: float = 5.0,
        heartbeat_interval: float = 30.0,
    ) -> ManagedProcess:
        if not argv or any(not isinstance(item, str) or not item for item in argv):
            raise SupervisorError("argv must contain non-empty strings")
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise SupervisorError("timeout_seconds must be positive when set")
        if terminate_grace_seconds <= 0:
            raise SupervisorError("terminate_grace_seconds must be positive")
        if heartbeat_interval <= 0:
            raise SupervisorError("heartbeat_interval must be positive")
        workdir = Path(cwd)
        if not workdir.is_dir():
            raise SupervisorError(f"cwd is not a directory: {workdir}")
        out_path = Path(stdout_path)
        err_path = Path(stderr_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        err_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_file = out_path.open("ab", buffering=0)
        stderr_file = err_path.open("ab", buffering=0)
        environment = os.environ.copy()
        environment.update(dict(env_additions or {}))
        started_at = utc_now()
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(workdir),
                env=environment,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=True,
            )
        except BaseException:
            stdout_file.close()
            stderr_file.close()
            raise
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
            stdout_file.close()
            stderr_file.close()
            raise
        return ManagedProcess(
            store=self.store,
            process_id=process_id,
            process=process,
            process_group_id=process_group_id,
            stdout_file=stdout_file,
            stderr_file=stderr_file,
            started_at=started_at,
            timeout_seconds=timeout_seconds,
            terminate_grace_seconds=terminate_grace_seconds,
            heartbeat_interval=heartbeat_interval,
        )
