from __future__ import annotations

import asyncio
import os
import signal
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_relay.artifacts import ArtifactManager
from agent_relay.git_workspace import record_candidate_generation
from agent_relay.operator import cancel_task
from agent_relay.store import Store, StoreError
from agent_relay.supervisor import SubprocessSupervisor


def _pid_is_live(pid: int) -> bool:
    stat = Path(f"/proc/{pid}/stat")
    if not stat.exists():
        return False
    try:
        return stat.read_text(encoding="utf-8").split()[2] != "Z"
    except (FileNotFoundError, IndexError):
        return False


class FinalAuditProcessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.store = Store(self.root / "state.sqlite3")
        self.store.create_task(
            task_id="task-1",
            repository=str(self.root),
            baseline_ref="main",
            task_spec={"repository": str(self.root)},
        )
        self.artifacts = ArtifactManager(self.root / "artifacts", self.store)
        self.supervisor = SubprocessSupervisor(self.store)

    def tearDown(self) -> None:
        self.store.close()

    def test_noisy_process_retains_bounded_tail_without_blocking_child(self) -> None:
        async def scenario() -> None:
            layout = self.artifacts.create_attempt(task_id="task-1", kind="tool")
            handle = await self.supervisor.start(
                task_id="task-1",
                attempt_id=layout.attempt.attempt_id,
                argv=[
                    sys.executable,
                    "-c",
                    (
                        "import sys; "
                        "sys.stdout.write('A'*50000 + 'STDOUT-TAIL-SENTINEL\\n'); "
                        "sys.stderr.write('B'*50000 + 'STDERR-TAIL-SENTINEL\\n')"
                    ),
                ],
                cwd=self.root,
                stdout_path=layout.stdout_path,
                stderr_path=layout.stderr_path,
                max_output_bytes=2048,
                heartbeat_interval=0.02,
            )
            result = await handle.wait()
            self.assertEqual((result.state, result.returncode), ("SUCCEEDED", 0))
            self.assertLessEqual(layout.stdout_path.stat().st_size, 2048)
            self.assertLessEqual(layout.stderr_path.stat().st_size, 2048)
            stdout = layout.stdout_path.read_text(encoding="utf-8")
            stderr = layout.stderr_path.read_text(encoding="utf-8")
            self.assertIn("earlier output truncated", stdout)
            self.assertIn("earlier output truncated", stderr)
            self.assertIn("STDOUT-TAIL-SENTINEL", stdout)
            self.assertIn("STDERR-TAIL-SENTINEL", stderr)

        asyncio.run(scenario())

    def test_managed_wait_reconciles_external_operator_cancellation(self) -> None:
        async def scenario() -> None:
            layout = self.artifacts.create_attempt(task_id="task-1", kind="tool")
            handle = await self.supervisor.start(
                task_id="task-1",
                attempt_id=layout.attempt.attempt_id,
                argv=[sys.executable, "-c", "import time; time.sleep(30)"],
                cwd=self.root,
                stdout_path=layout.stdout_path,
                stderr_path=layout.stderr_path,
                heartbeat_interval=0.02,
                terminate_grace_seconds=0.05,
            )
            waiter = asyncio.create_task(handle.wait())
            await asyncio.sleep(0.05)
            cancelled = cancel_task(self.store, "task-1", grace_seconds=0.05)
            self.assertEqual(cancelled["action"], "cancelled")
            result = await asyncio.wait_for(waiter, timeout=2.0)
            self.assertEqual(result.state, "CANCELLED")
            row = self.store._conn.execute(
                "SELECT state,ended_at,exit_status FROM processes WHERE process_id=?",
                (result.process_id,),
            ).fetchone()
            self.assertEqual(row["state"], "CANCELLED")
            self.assertIsNotNone(row["ended_at"])
            self.assertEqual(row["exit_status"], result.returncode)
            attempt = self.store.get_attempt(layout.attempt.attempt_id)
            self.assertEqual(attempt.status, "CANCELLED")

        asyncio.run(scenario())

    def test_cancelled_attempt_rejects_late_provider_result_without_history_pollution(self) -> None:
        layout = self.artifacts.create_attempt(task_id="task-1", kind="writer")
        cancelled = self.store.finish_attempt(
            attempt_id=layout.attempt.attempt_id,
            status="CANCELLED",
            result={"reason": "operator cancellation"},
            exit_status=None,
        )
        before = self.store._conn.execute(
            "SELECT COUNT(*) FROM artifacts WHERE attempt_id=?",
            (layout.attempt.attempt_id,),
        ).fetchone()[0]
        returned = self.artifacts.finalize_attempt(
            layout,
            status="PROCESS_FAILURE",
            result={"reason": "late provider finish"},
            exit_status=-15,
        )
        after = self.store._conn.execute(
            "SELECT COUNT(*) FROM artifacts WHERE attempt_id=?",
            (layout.attempt.attempt_id,),
        ).fetchone()[0]
        self.assertEqual(returned.status, "CANCELLED")
        self.assertEqual(returned.result, cancelled.result)
        self.assertFalse(layout.result_path.exists())
        self.assertEqual(after, before)

    def test_cancellation_between_precheck_and_result_commit_cannot_publish_late_result(self) -> None:
        layout = self.artifacts.create_attempt(task_id="task-1", kind="writer")
        attempt_id = layout.attempt.attempt_id
        original_get = self.store.get_attempt
        injected = False
        cancelled = None

        def get_then_cancel(requested_id: int):
            nonlocal injected, cancelled
            row = original_get(requested_id)
            if requested_id == attempt_id and not injected:
                injected = True
                cancelled = self.store.finish_attempt(
                    attempt_id=attempt_id,
                    status="CANCELLED",
                    result={"reason": "operator cancellation in finalization race"},
                    exit_status=None,
                )
            return row

        before = self.store._conn.execute(
            "SELECT COUNT(*) FROM artifacts WHERE attempt_id=?",
            (attempt_id,),
        ).fetchone()[0]
        with patch.object(self.store, "get_attempt", side_effect=get_then_cancel):
            returned = self.artifacts.finalize_attempt(
                layout,
                status="SUCCESS",
                result={"handoff": "late provider success"},
                exit_status=0,
            )

        self.assertTrue(injected)
        self.assertIsNotNone(cancelled)
        self.assertEqual(returned.status, "CANCELLED")
        self.assertEqual(returned.result, cancelled.result)
        self.assertFalse(layout.result_path.exists())
        after = self.store._conn.execute(
            "SELECT COUNT(*) FROM artifacts WHERE attempt_id=?",
            (attempt_id,),
        ).fetchone()[0]
        self.assertEqual(after, before)

    def test_uncommitted_orphan_result_file_does_not_block_recovery_finalization(self) -> None:
        layout = self.artifacts.create_attempt(task_id="task-1", kind="writer")
        attempt_id = layout.attempt.attempt_id
        layout.result_path.write_text('{"partial":"crash residue"}\n', encoding="utf-8")
        result_rows = self.store._conn.execute(
            "SELECT COUNT(*) FROM artifacts WHERE attempt_id=? AND kind='result'",
            (attempt_id,),
        ).fetchone()[0]
        self.assertEqual(result_rows, 0)
        self.assertIsNone(self.store.get_attempt(attempt_id).ended_at)

        finished = self.artifacts.finalize_attempt(
            layout,
            status="SUCCESS",
            result={"recovered": True},
            exit_status=0,
        )

        self.assertEqual(finished.status, "SUCCESS")
        self.assertEqual(finished.result, {"recovered": True})
        self.assertIn('"recovered": true', layout.result_path.read_text(encoding="utf-8").lower())
        result_rows = self.store._conn.execute(
            "SELECT COUNT(*) FROM artifacts WHERE attempt_id=? AND kind='result'",
            (attempt_id,),
        ).fetchone()[0]
        self.assertEqual(result_rows, 1)

    def test_normal_parent_exit_does_not_leave_unmanaged_child_in_process_group(self) -> None:
        async def scenario() -> None:
            child_pid_file = self.root / "audit-child.pid"
            layout = self.artifacts.create_attempt(task_id="task-1", kind="tool")
            code = (
                "import pathlib,subprocess,sys; "
                "p=subprocess.Popen([sys.executable,'-c','import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)']); "
                f"pathlib.Path({str(child_pid_file)!r}).write_text(str(p.pid))"
            )
            handle = await self.supervisor.start(
                task_id="task-1",
                attempt_id=layout.attempt.attempt_id,
                argv=[sys.executable, "-c", code],
                cwd=self.root,
                stdout_path=layout.stdout_path,
                stderr_path=layout.stderr_path,
                terminate_grace_seconds=0.05,
                heartbeat_interval=0.02,
            )
            result = await asyncio.wait_for(handle.wait(), timeout=2.0)
            self.assertEqual(result.state, "SUCCEEDED")
            self.assertTrue(child_pid_file.exists())
            child_pid = int(child_pid_file.read_text(encoding="utf-8"))
            for _ in range(100):
                if not _pid_is_live(child_pid):
                    break
                await asyncio.sleep(0.01)
            else:
                self.fail("child process remained live after managed parent exited")

        asyncio.run(scenario())

    def test_operator_cancel_kills_group_even_after_leader_dies(self) -> None:
        async def scenario() -> None:
            child_pid_file = self.root / "operator-child.pid"
            child_ready = self.root / "operator-child.ready"
            layout = self.artifacts.create_attempt(task_id="task-1", kind="tool")
            child_code = (
                "import pathlib,signal,time; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                f"pathlib.Path({str(child_ready)!r}).write_text('ready'); "
                "time.sleep(30)"
            )
            parent_code = (
                "import pathlib,subprocess,sys,time; "
                f"p=subprocess.Popen([sys.executable,'-c',{child_code!r}]); "
                f"pathlib.Path({str(child_pid_file)!r}).write_text(str(p.pid)); "
                "time.sleep(30)"
            )
            handle = await self.supervisor.start(
                task_id="task-1",
                attempt_id=layout.attempt.attempt_id,
                argv=[sys.executable, "-c", parent_code],
                cwd=self.root,
                stdout_path=layout.stdout_path,
                stderr_path=layout.stderr_path,
                terminate_grace_seconds=0.05,
                heartbeat_interval=0.02,
            )
            for _ in range(200):
                if child_pid_file.exists() and child_ready.exists():
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(child_ready.exists())
            child_pid = int(child_pid_file.read_text(encoding="utf-8"))
            try:
                cancelled = cancel_task(self.store, "task-1", grace_seconds=0.05)
                self.assertEqual(cancelled["action"], "cancelled")
                for _ in range(100):
                    if not _pid_is_live(child_pid):
                        break
                    await asyncio.sleep(0.01)
                else:
                    self.fail("operator cancellation left a process-group child live")
                self.assertTrue(cancelled["terminated_processes"][0]["sigkill_sent"])
            finally:
                if _pid_is_live(child_pid):
                    try:
                        os.killpg(handle.process_group_id, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            await asyncio.wait_for(handle.wait(), timeout=2.0)

        asyncio.run(scenario())

    def test_idempotent_candidate_freeze_rejects_stale_writer_ownership(self) -> None:
        first = self.store.allocate_attempt(task_id="task-1", kind="writer")
        second = self.store.allocate_attempt(task_id="task-1", kind="writer")
        sha = "a" * 40
        frozen = record_candidate_generation(
            self.store,
            task_id="task-1",
            candidate_sha=sha,
            writer_attempt_id=first.attempt_id,
            expected_previous_generation=0,
        )
        repeated = record_candidate_generation(
            self.store,
            task_id="task-1",
            candidate_sha=sha,
            writer_attempt_id=first.attempt_id,
            expected_previous_generation=0,
        )
        self.assertEqual(repeated, frozen)
        with self.assertRaisesRegex(StoreError, "writer attempt"):
            record_candidate_generation(
                self.store,
                task_id="task-1",
                candidate_sha=sha,
                writer_attempt_id=second.attempt_id,
                expected_previous_generation=0,
            )
        with self.assertRaisesRegex(StoreError, "stale candidate generation"):
            record_candidate_generation(
                self.store,
                task_id="task-1",
                candidate_sha=sha,
                writer_attempt_id=first.attempt_id,
                expected_previous_generation=1,
            )

    def test_production_source_avoids_local_shell_and_destructive_git_cleanup(self) -> None:
        package_root = Path(__file__).resolve().parents[1] / "agent_relay"
        source = "\n".join(path.read_text(encoding="utf-8") for path in package_root.glob("*.py"))
        self.assertNotIn("shell=True", source)
        self.assertNotIn('"reset", "--hard"', source)
        self.assertNotIn('"clean", "-f', source)


if __name__ == "__main__":
    unittest.main()
