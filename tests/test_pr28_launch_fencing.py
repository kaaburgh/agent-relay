from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_relay.launch_gate import ABANDONED_LAUNCH_EXIT
from agent_relay.runtime_safety import (
    RuntimeSafetyError,
    authorize_launch_claim_in_transaction,
    claim_attempt_launch,
    ensure_runtime_safety_guards,
    release_attempt_launch_claim,
    verify_launch_claim_owned_by_current_process,
)
from agent_relay.store import Store
from agent_relay.workflow import WorkflowStage, WorkflowStateMachine


class PullRequest28LaunchFencingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.db = self.root / "state.sqlite3"
        self.store = Store(self.db)
        ensure_runtime_safety_guards(self.store)
        self.store.create_task(
            task_id="task-1",
            repository=str(self.root),
            baseline_ref="main",
            task_spec={"repository": str(self.root)},
        )
        WorkflowStateMachine(self.store).transition("task-1", WorkflowStage.WORK)

    def tearDown(self) -> None:
        self.store.close()

    def _writer_attempt(self) -> int:
        return self.store.allocate_attempt(task_id="task-1", kind="writer").attempt_id

    def _authorize_without_parent_signal(self, attempt_id: int) -> None:
        claim_attempt_launch(self.store, task_id="task-1", attempt_id=attempt_id)
        with self.store._transaction():
            authorize_launch_claim_in_transaction(self.store, attempt_id=attempt_id)
            updated = self.store._conn.execute(
                """
                UPDATE attempts SET status='RUNNING'
                WHERE attempt_id=? AND status='LAUNCHING' AND ended_at IS NULL
                """,
                (attempt_id,),
            )
            self.assertEqual(updated.rowcount, 1)

    def _run_gate_with_eof(self, *, attempt_id: int, marker: Path) -> int:
        read_fd, write_fd = os.pipe()
        try:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "agent_relay.launch_gate",
                    "--gate-fd",
                    str(read_fd),
                    "--state-db",
                    str(self.db),
                    "--attempt-id",
                    str(attempt_id),
                    "--",
                    sys.executable,
                    "-c",
                    f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')",
                ],
                cwd=self.root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                pass_fds=(read_fd,),
            )
        finally:
            os.close(read_fd)
            # Closing without writing simulates the orchestrator dying after spawning the gate.
            os.close(write_fd)
        return process.wait(timeout=10)

    def test_stale_writer_attempt_cannot_claim_after_stage_advance(self) -> None:
        attempt_id = self._writer_attempt()
        WorkflowStateMachine(self.store).transition("task-1", WorkflowStage.VALIDATE)

        with self.assertRaisesRegex(RuntimeSafetyError, "writer attempt cannot launch"):
            claim_attempt_launch(self.store, task_id="task-1", attempt_id=attempt_id)

        self.assertEqual(self.store.get_attempt(attempt_id).status, "CREATED")

    def test_claim_snapshot_fails_closed_when_task_is_cancelled_before_publication(self) -> None:
        attempt_id = self._writer_attempt()
        claim_attempt_launch(self.store, task_id="task-1", attempt_id=attempt_id)
        WorkflowStateMachine(self.store).transition("task-1", WorkflowStage.CANCELLED)

        with self.assertRaisesRegex(RuntimeSafetyError, "workflow state changed"):
            verify_launch_claim_owned_by_current_process(self.store, attempt_id=attempt_id)

        release_attempt_launch_claim(self.store, task_id="task-1", attempt_id=attempt_id)
        attempt = self.store.get_attempt(attempt_id)
        self.assertEqual(attempt.status, "CANCELLED")
        self.assertIsNotNone(attempt.ended_at)

    def test_durable_authorization_survives_parent_crash_before_gate_byte(self) -> None:
        attempt_id = self._writer_attempt()
        self._authorize_without_parent_signal(attempt_id)
        marker = self.root / "authorized-ran"

        returncode = self._run_gate_with_eof(attempt_id=attempt_id, marker=marker)

        self.assertEqual(returncode, 0)
        self.assertTrue(marker.exists())

    def test_gate_rechecks_task_snapshot_before_exec(self) -> None:
        attempt_id = self._writer_attempt()
        self._authorize_without_parent_signal(attempt_id)
        WorkflowStateMachine(self.store).transition("task-1", WorkflowStage.CANCELLED)
        marker = self.root / "cancelled-must-not-run"

        returncode = self._run_gate_with_eof(attempt_id=attempt_id, marker=marker)

        self.assertEqual(returncode, ABANDONED_LAUNCH_EXIT)
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()