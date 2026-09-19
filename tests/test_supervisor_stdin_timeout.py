from __future__ import annotations

import asyncio
import sys
import tempfile
import time
import unittest
from pathlib import Path

from agent_relay.artifacts import ArtifactManager
from agent_relay.store import Store
from agent_relay.supervisor import SubprocessSupervisor


class SupervisorStdinTimeoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.store = Store(self.root / "state.sqlite3")
        self.store.create_task(
            task_id="task-stdin-timeout",
            repository=str(self.root),
            baseline_ref="main",
            task_spec={"repository": str(self.root)},
        )
        self.artifacts = ArtifactManager(self.root / "artifacts", self.store)
        self.supervisor = SubprocessSupervisor(self.store)

    def tearDown(self) -> None:
        self.store.close()

    def test_stage_timeout_covers_blocked_stdin_delivery(self) -> None:
        async def scenario() -> None:
            layout = self.artifacts.create_attempt(
                task_id="task-stdin-timeout", kind="tool"
            )
            payload = "x" * (4 * 1024 * 1024)
            started = time.monotonic()
            handle = await self.supervisor.start(
                task_id="task-stdin-timeout",
                attempt_id=layout.attempt.attempt_id,
                argv=[sys.executable, "-c", "import time; time.sleep(30)"],
                cwd=self.root,
                stdout_path=layout.stdout_path,
                stderr_path=layout.stderr_path,
                stdin_text=payload,
                timeout_seconds=0.20,
                terminate_grace_seconds=0.05,
                heartbeat_interval=0.03,
            )
            start_elapsed = time.monotonic() - started
            result = await handle.wait()

            self.assertLess(start_elapsed, 2.0)
            self.assertEqual(result.state, "TIMED_OUT")
            self.assertTrue(result.timed_out)
            self.assertNotEqual(result.returncode, 0)

            row = self.store._conn.execute(
                "SELECT state,ended_at,exit_status FROM processes WHERE process_id=?",
                (result.process_id,),
            ).fetchone()
            self.assertEqual(row["state"], "TIMED_OUT")
            self.assertIsNotNone(row["ended_at"])
            self.assertNotEqual(row["exit_status"], 0)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
