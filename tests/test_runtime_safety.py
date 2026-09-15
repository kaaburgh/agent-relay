from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_relay.artifacts import ArtifactManager
from agent_relay.operator import OperatorError, cancel_task
from agent_relay.runtime_safety import (
    ProcessIdentity,
    RuntimeSafetyError,
    capture_process_identity,
    ensure_runtime_safety_guards,
    persist_process_identity,
)
from agent_relay.store import Store, utc_now
from agent_relay.supervisor import SubprocessSupervisor
from agent_relay.workflow import WorkflowStage, WorkflowStateMachine


class RuntimeSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.store = Store(self.root / "state.sqlite3")
        self.store.create_task(
            task_id="task-1",
            repository=str(self.root),
            baseline_ref="main",
            task_spec={"repository": str(self.root)},
        )
        WorkflowStateMachine(self.store).transition("task-1", WorkflowStage.WORK)
        self.artifacts = ArtifactManager(self.root / "artifacts", self.store)

    def tearDown(self) -> None:
        self.store.close()

    def test_second_writer_cannot_execute_side_effects_before_durable_ownership(self) -> None:
        async def scenario() -> None:
            supervisor = SubprocessSupervisor(self.store)
            first = self.artifacts.create_attempt(task_id="task-1", kind="writer")
            second = self.artifacts.create_attempt(task_id="task-1", kind="writer")
            first_marker = self.root / "first-ran"
            second_marker = self.root / "second-ran"

            handle = await supervisor.start(
                task_id="task-1",
                attempt_id=first.attempt.attempt_id,
                argv=[
                    sys.executable,
                    "-c",
                    (
                        "from pathlib import Path; import time; "
                        f"Path({str(first_marker)!r}).write_text('ran'); time.sleep(30)"
                    ),
                ],
                cwd=self.root,
                stdout_path=first.stdout_path,
                stderr_path=first.stderr_path,
                heartbeat_interval=0.05,
                terminate_grace_seconds=0.05,
            )
            for _ in range(100):
                if first_marker.exists():
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(first_marker.exists())

            with self.assertRaises(RuntimeSafetyError):
                await supervisor.start(
                    task_id="task-1",
                    attempt_id=second.attempt.attempt_id,
                    argv=[
                        sys.executable,
                        "-c",
                        f"from pathlib import Path; Path({str(second_marker)!r}).write_text('ran')",
                    ],
                    cwd=self.root,
                    stdout_path=second.stdout_path,
                    stderr_path=second.stderr_path,
                )
            self.assertFalse(second_marker.exists())
            self.assertEqual(self.store.get_attempt(second.attempt.attempt_id).status, "CREATED")

            # Re-launching the exact same RUNNING attempt is also rejected before target exec.
            with self.assertRaises(RuntimeSafetyError):
                await supervisor.start(
                    task_id="task-1",
                    attempt_id=first.attempt.attempt_id,
                    argv=[sys.executable, "-c", "raise SystemExit(99)"],
                    cwd=self.root,
                    stdout_path=first.stdout_path,
                    stderr_path=first.stderr_path,
                )

            await handle.terminate()
            self.store.finish_attempt(
                attempt_id=first.attempt.attempt_id,
                status="CANCELLED",
                result={"reason": "test cleanup"},
            )

        asyncio.run(scenario())

    def test_pid_reuse_identity_mismatch_is_never_signalled(self) -> None:
        ensure_runtime_safety_guards(self.store)
        attempt = self.store.allocate_attempt(task_id="task-1", kind="writer")
        unrelated = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=self.root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            actual = capture_process_identity(unrelated.pid)
            deliberately_wrong = ProcessIdentity(
                boot_id=actual.boot_id,
                start_time_ticks=actual.start_time_ticks + 1,
                process_group_id=actual.process_group_id,
                session_id=actual.session_id,
            )
            now = utc_now()
            with self.store._transaction():
                cursor = self.store._conn.execute(
                    """
                    INSERT INTO processes(
                        task_id,attempt_id,pid,process_group_id,state,command_json,
                        started_at,last_liveness_at
                    ) VALUES (?,?,?,?, 'RUNNING','[]',?,?)
                    """,
                    (
                        "task-1",
                        attempt.attempt_id,
                        unrelated.pid,
                        os.getpgid(unrelated.pid),
                        now,
                        now,
                    ),
                )
                persist_process_identity(
                    self.store,
                    process_id=int(cursor.lastrowid),
                    identity=deliberately_wrong,
                )
                self.store._conn.execute(
                    "UPDATE attempts SET pid=?,status='RUNNING' WHERE attempt_id=?",
                    (unrelated.pid, attempt.attempt_id),
                )

            with self.assertRaisesRegex(OperatorError, "MISMATCH"):
                cancel_task(self.store, "task-1", grace_seconds=0.05)
            self.assertIsNone(unrelated.poll(), "identity mismatch must not kill unrelated process")
            self.assertEqual(self.store.get_task("task-1").stage, "WORK")
        finally:
            if unrelated.poll() is None:
                try:
                    os.killpg(os.getpgid(unrelated.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
            unrelated.wait(timeout=5)

    def test_capture_failure_gets_distinct_non_success_terminal_state(self) -> None:
        async def scenario() -> None:
            supervisor = SubprocessSupervisor(self.store)
            handle = await supervisor.start(
                task_id="task-1",
                argv=[sys.executable, "-c", "print('valid-looking-success')"],
                cwd=self.root,
                stdout_path=self.root / "capture.stdout",
                stderr_path=self.root / "capture.stderr",
                heartbeat_interval=0.05,
            )

            async def injected_capture_failure() -> None:
                raise OSError("injected capture failure")

            fault = asyncio.create_task(injected_capture_failure())
            handle.capture_tasks = (*handle.capture_tasks, fault)
            result = await handle.wait()
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.state, "CAPTURE_FAILED")
            row = self.store._conn.execute(
                "SELECT state,exit_status FROM processes WHERE process_id=?",
                (result.process_id,),
            ).fetchone()
            self.assertEqual((row["state"], row["exit_status"]), ("CAPTURE_FAILED", 0))

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
