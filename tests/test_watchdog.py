from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

from agent_relay.artifacts import ArtifactManager
from agent_relay.simulated_validator import SimulatedValidator, ValidationResultKind
from agent_relay.store import Store
from agent_relay.supervisor import SubprocessSupervisor
from agent_relay.watchdog import file_progress_token, wait_with_file_progress_watchdog


class WatchdogTests(unittest.TestCase):
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
        self.validator = SimulatedValidator(
            store=self.store,
            artifacts=self.artifacts,
            supervisor=self.supervisor,
        )

    def tearDown(self) -> None:
        self.store.close()

    def test_file_progress_token_changes_when_evidence_appears_and_changes(self) -> None:
        path = self.root / "progress.json"
        missing = file_progress_token([path])
        path.write_text("one\n", encoding="utf-8")
        created = file_progress_token([path])
        time.sleep(0.001)
        path.write_text("two-two\n", encoding="utf-8")
        changed = file_progress_token([path])
        self.assertNotEqual(missing, created)
        self.assertNotEqual(created, changed)

    def test_long_run_with_regular_evidence_progress_is_not_stalled(self) -> None:
        async def scenario() -> None:
            progress = self.root / "progress.txt"
            layout = self.artifacts.create_attempt(task_id="task-1", kind="tool")
            code = (
                "import pathlib,time; "
                f"p=pathlib.Path({str(progress)!r}); "
                "[(p.write_text(str(i)), time.sleep(0.04)) for i in range(7)]"
            )
            handle = await self.supervisor.start(
                task_id="task-1",
                attempt_id=layout.attempt.attempt_id,
                argv=[sys.executable, "-c", code],
                cwd=self.root,
                stdout_path=layout.stdout_path,
                stderr_path=layout.stderr_path,
                heartbeat_interval=0.02,
                terminate_grace_seconds=0.05,
            )
            started = time.monotonic()
            result = await wait_with_file_progress_watchdog(
                handle,
                progress_paths=[progress],
                stall_timeout_seconds=0.1,
                poll_interval_seconds=0.02,
            )
            self.assertEqual(result.state, "SUCCEEDED")
            self.assertFalse(result.stalled)
            self.assertGreater(time.monotonic() - started, 0.2)

        asyncio.run(scenario())

    def test_stalled_validator_kills_whole_group_and_preserves_partial_evidence(self) -> None:
        async def scenario() -> None:
            result = await self.validator.run(
                task_id="task-1",
                cwd=self.root,
                generation=1,
                candidate_sha="a" * 40,
                requested_cycles=3,
                behavior={
                    "cycle_duration": 0.01,
                    "hang_cycle": 2,
                    "spawn_child": True,
                    "ignore_sigterm": True,
                },
                stall_timeout_seconds=0.12,
                watchdog_poll_interval_seconds=0.02,
            )
            self.assertEqual(result.kind, ValidationResultKind.PROCESS_FAILURE)
            self.assertEqual(result.process_result.state, "STALLED")
            self.assertTrue(result.process_result.stalled)
            self.assertTrue(result.process_result.forced_kill)
            self.assertFalse(result.process_result.timed_out)

            status_path = result.evidence_dir / "runner-status.json"
            cycles_path = result.evidence_dir / "cycles.csv"
            self.assertTrue(status_path.exists())
            self.assertTrue(cycles_path.exists())
            status = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(status["state"], "running")
            self.assertEqual(status["current_cycle"], 2)
            self.assertEqual(status["completed_cycles"], 1)
            self.assertIn("1,ok", cycles_path.read_text(encoding="utf-8"))

            child_pid = int(status["child_pid"])
            for _ in range(100):
                stat = Path(f"/proc/{child_pid}/stat")
                if not stat.exists():
                    break
                try:
                    state = stat.read_text().split()[2]
                except (FileNotFoundError, IndexError):
                    break
                if state == "Z":
                    break
                await asyncio.sleep(0.01)
            else:
                self.fail("validator child remained live after stall cleanup")

        asyncio.run(scenario())

    def test_timeout_crash_and_cancellation_remain_distinct_from_stall(self) -> None:
        async def scenario() -> None:
            timed_out = await self.validator.run(
                task_id="task-1",
                cwd=self.root,
                generation=1,
                candidate_sha="b" * 40,
                requested_cycles=2,
                behavior={"hang_cycle": 1},
                timeout_seconds=0.12,
                stall_timeout_seconds=0.5,
                watchdog_poll_interval_seconds=0.02,
            )
            self.assertEqual(timed_out.process_result.state, "TIMED_OUT")
            self.assertTrue(timed_out.process_result.timed_out)
            self.assertFalse(timed_out.process_result.stalled)

            crashed = await self.validator.run(
                task_id="task-1",
                cwd=self.root,
                generation=1,
                candidate_sha="c" * 40,
                requested_cycles=2,
                behavior={"crash_cycle": 1},
                stall_timeout_seconds=0.5,
                watchdog_poll_interval_seconds=0.02,
            )
            self.assertEqual(crashed.process_result.state, "FAILED")
            self.assertFalse(crashed.process_result.timed_out)
            self.assertFalse(crashed.process_result.stalled)

            layout = self.artifacts.create_attempt(task_id="task-1", kind="cancel-tool")
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
            cancelled = await handle.terminate(state="CANCELLED")
            self.assertEqual(cancelled.state, "CANCELLED")
            self.assertFalse(cancelled.timed_out)
            self.assertFalse(cancelled.stalled)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
