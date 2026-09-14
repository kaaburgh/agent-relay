from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

from agent_relay.artifacts import ArtifactManager
from agent_relay.operator import cancel_task
from agent_relay.store import Store
from agent_relay.supervisor import SubprocessSupervisor


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

    def test_production_source_avoids_local_shell_and_destructive_git_cleanup(self) -> None:
        package_root = Path(__file__).resolve().parents[1] / "agent_relay"
        source = "\n".join(path.read_text(encoding="utf-8") for path in package_root.glob("*.py"))
        self.assertNotIn("shell=True", source)
        self.assertNotIn('"reset", "--hard"', source)
        self.assertNotIn('"clean", "-f', source)


if __name__ == "__main__":
    unittest.main()
