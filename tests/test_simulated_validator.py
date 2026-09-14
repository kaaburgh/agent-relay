from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path

from agent_relay.artifacts import ArtifactManager
from agent_relay.simulated_validator import SimulatedValidator, ValidationResultKind
from agent_relay.store import Store
from agent_relay.supervisor import SubprocessSupervisor


class SimulatedValidatorTests(unittest.TestCase):
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
        self.validator = SimulatedValidator(
            store=self.store,
            artifacts=self.artifacts,
            supervisor=SubprocessSupervisor(self.store),
        )

    def tearDown(self) -> None:
        self.store.close()

    def test_success_requires_complete_machine_readable_evidence(self) -> None:
        async def scenario() -> None:
            result = await self.validator.run(
                task_id="task-1", cwd=self.root, generation=1, candidate_sha="a" * 40,
                requested_cycles=3, behavior={"cycle_duration": 0.01},
            )
            self.assertEqual(result.kind, ValidationResultKind.SUCCESS)
            self.assertEqual(result.completed_cycles, 3)
            self.assertEqual(len(result.metrics), 3)
            self.assertTrue((result.evidence_dir / "runner-status.json").exists())
            self.assertTrue((result.evidence_dir / "cycles.csv").exists())
            self.assertTrue((result.evidence_dir / "summary.md").exists())
            self.assertEqual([row["cycle"] for row in result.metrics], ["1", "2", "3"])
        asyncio.run(scenario())

    def test_exit_zero_with_incomplete_cycles_never_passes(self) -> None:
        async def scenario() -> None:
            result = await self.validator.run(
                task_id="task-1", cwd=self.root, generation=1, candidate_sha="b" * 40,
                requested_cycles=3, behavior={"incomplete_cycles": 1},
            )
            self.assertEqual(result.process_result.returncode, 0)
            self.assertEqual(result.kind, ValidationResultKind.INCOMPLETE_EVIDENCE)
            self.assertIn("2/3 records", result.reason)
        asyncio.run(scenario())

    def test_exit_zero_without_summary_never_passes(self) -> None:
        async def scenario() -> None:
            result = await self.validator.run(
                task_id="task-1", cwd=self.root, generation=1, candidate_sha="c" * 40,
                requested_cycles=2, behavior={"omit_summary": True},
            )
            self.assertEqual(result.process_result.returncode, 0)
            self.assertEqual(result.kind, ValidationResultKind.INCOMPLETE_EVIDENCE)
            self.assertEqual(result.reason, "summary.md is missing")
        asyncio.run(scenario())

    def test_fail_cycle_and_crash_are_not_success(self) -> None:
        async def scenario() -> None:
            failed = await self.validator.run(
                task_id="task-1", cwd=self.root, generation=1, candidate_sha="d" * 40,
                requested_cycles=3, behavior={"fail_cycle": 2},
            )
            crashed = await self.validator.run(
                task_id="task-1", cwd=self.root, generation=1, candidate_sha="d" * 40,
                requested_cycles=3, behavior={"crash_cycle": 2},
            )
            self.assertEqual(failed.kind, ValidationResultKind.VALIDATION_FAILED)
            self.assertNotEqual(crashed.kind, ValidationResultKind.SUCCESS)
            self.assertNotEqual(failed.process_result.returncode, 0)
            self.assertNotEqual(crashed.process_result.returncode, 0)
        asyncio.run(scenario())

    def test_hang_with_orphan_child_is_killed_as_one_process_group(self) -> None:
        async def scenario() -> None:
            invocation = await self.validator.start(
                task_id="task-1", cwd=self.root, generation=1, candidate_sha="e" * 40,
                requested_cycles=3,
                behavior={"spawn_child": True, "hang_cycle": 2, "ignore_sigterm": True},
                timeout_seconds=0.25,
            )
            status_path = invocation.evidence_dir / "runner-status.json"
            child_pid = None
            for _ in range(100):
                if status_path.exists():
                    try:
                        child_pid = json.loads(status_path.read_text(encoding="utf-8")).get("child_pid")
                    except (json.JSONDecodeError, OSError):
                        pass
                if child_pid:
                    break
                await asyncio.sleep(0.01)
            self.assertIsNotNone(child_pid)
            result = await self.validator.finish(invocation)
            self.assertEqual(result.kind, ValidationResultKind.PROCESS_FAILURE)
            self.assertEqual(result.process_result.state, "TIMED_OUT")
            self.assertTrue(result.process_result.forced_kill)
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
                self.fail("validator child remained alive after process-group timeout cleanup")
        asyncio.run(scenario())

    def test_incremental_runner_status_is_visible_before_completion(self) -> None:
        async def scenario() -> None:
            invocation = await self.validator.start(
                task_id="task-1", cwd=self.root, generation=2, candidate_sha="f" * 40,
                requested_cycles=3, behavior={"cycle_duration": 0.08},
            )
            status_path = invocation.evidence_dir / "runner-status.json"
            observed_running = False
            for _ in range(100):
                if status_path.exists():
                    try:
                        status = json.loads(status_path.read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, OSError):
                        status = {}
                    if status.get("state") == "running":
                        observed_running = True
                        break
                await asyncio.sleep(0.01)
            self.assertTrue(observed_running)
            result = await self.validator.finish(invocation)
            self.assertEqual(result.kind, ValidationResultKind.SUCCESS)
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
