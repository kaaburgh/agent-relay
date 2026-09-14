from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from agent_relay.artifacts import ArtifactManager
from agent_relay.shadps4_validator import (
    ShadPS4BloodborneValidator,
    ShadPS4ValidationResultKind,
)
from agent_relay.store import Store
from agent_relay.supervisor import SubprocessSupervisor


_FAKE_HARNESS = r'''
import argparse
import csv
import json
import os
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--run-id", required=True)
parser.add_argument("--requested-cycles", required=True, type=int)
parser.add_argument("--evidence-dir", required=True)
parser.add_argument("--mode", required=True)
args = parser.parse_args()

root = Path(args.evidence_dir)
root.mkdir(parents=True, exist_ok=True)
status_path = root / "runner-status.json"
summary_path = root / "summary.md"
requested = args.requested_cycles

if args.mode == "crash":
    os._exit(23)

if args.mode == "hang":
    status_path.write_text(json.dumps({
        "run_id": args.run_id,
        "requested_cycles": requested,
        "completed_cycles": 0,
        "state": "running",
    }), encoding="utf-8")
    while True:
        time.sleep(60)

records = [{"cycle": index, "status": "ok", "metric": index * 10} for index in range(1, requested + 1)]
state = "completed"
completed = requested
status_run_id = args.run_id
status_requested = requested
exit_code = 0

if args.mode == "incomplete":
    records = records[:-1]
elif args.mode == "failed-state":
    state = "failed"
elif args.mode == "failed-cycle":
    records[min(1, len(records) - 1)]["status"] = "failed"
elif args.mode == "wrong-run-id":
    status_run_id = "wrong-run"
elif args.mode == "wrong-requested":
    status_requested = requested + 1
elif args.mode == "nonzero-failure":
    state = "failed"
    exit_code = 2

status_path.write_text(json.dumps({
    "run_id": status_run_id,
    "requested_cycles": status_requested,
    "completed_cycles": completed,
    "state": state,
}), encoding="utf-8")

if args.mode == "success-json":
    (root / "cycles.json").write_text(json.dumps({"cycles": records}), encoding="utf-8")
else:
    with (root / "cycles.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["cycle", "status", "metric"])
        writer.writeheader()
        writer.writerows(records)

summary_path.write_text(f"run={args.run_id} completed={completed}/{requested}\n", encoding="utf-8")
raise SystemExit(exit_code)
'''


class ShadPS4BloodborneValidatorTests(unittest.TestCase):
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
        self.validator = ShadPS4BloodborneValidator(
            store=self.store,
            artifacts=self.artifacts,
            supervisor=SubprocessSupervisor(self.store),
        )
        self.harness = self.root / "fake_shadps4_harness.py"
        self.harness.write_text(textwrap.dedent(_FAKE_HARNESS), encoding="utf-8")

    def tearDown(self) -> None:
        self.store.close()

    def _argv(self, mode: str) -> list[str]:
        return [
            sys.executable,
            str(self.harness),
            "--run-id",
            "{run_id}",
            "--requested-cycles",
            "{requested_cycles}",
            "--evidence-dir",
            "{evidence_dir}",
            "--mode",
            mode,
        ]

    async def _run(self, mode: str, **kwargs: object):
        return await self.validator.run(
            task_id="task-1",
            cwd=self.root,
            generation=2,
            candidate_sha="a" * 40,
            requested_cycles=3,
            argv_template=self._argv(mode),
            **kwargs,
        )

    def test_success_csv_normalizes_evidence_and_preserves_attempt_provenance(self) -> None:
        async def scenario() -> None:
            result = await self._run("success-csv")
            self.assertEqual(result.kind, ShadPS4ValidationResultKind.SUCCESS)
            self.assertEqual(result.completed_cycles, 3)
            self.assertEqual(len(result.cycle_records), 3)
            self.assertEqual(result.runner_state, "completed")
            self.assertEqual(result.generation, 2)
            self.assertEqual(result.candidate_sha, "a" * 40)
            self.assertEqual(result.process_result.returncode, 0)
            self.assertTrue(result.status_path and result.status_path.exists())
            self.assertTrue(result.cycles_path and result.cycles_path.name == "cycles.csv")
            self.assertTrue(result.summary_path and result.summary_path.exists())

            attempt = self.store.get_attempt(result.attempt_id)
            self.assertEqual(attempt.status, "SUCCESS")
            self.assertEqual(attempt.generation, 2)
            self.assertEqual(attempt.exit_status, 0)
            self.assertEqual(attempt.result["run_id"], result.run_id)
            self.assertEqual(attempt.result["candidate_sha"], "a" * 40)
            self.assertNotIn("{run_id}", " ".join(attempt.result["process"].keys()))

            rows = self.store._conn.execute(
                "SELECT kind,path FROM artifacts WHERE attempt_id=? ORDER BY artifact_id",
                (result.attempt_id,),
            ).fetchall()
            kinds = {row["kind"] for row in rows}
            self.assertTrue({"rendered_command", "runner_status", "cycles", "summary"} <= kinds)
        asyncio.run(scenario())

    def test_success_json_cycle_evidence_is_supported(self) -> None:
        async def scenario() -> None:
            result = await self._run("success-json")
            self.assertEqual(result.kind, ShadPS4ValidationResultKind.SUCCESS)
            self.assertEqual(result.cycles_path.name, "cycles.json")
            self.assertEqual([int(row["cycle"]) for row in result.cycle_records], [1, 2, 3])
        asyncio.run(scenario())

    def test_exit_zero_incomplete_or_mismatched_evidence_never_passes(self) -> None:
        async def scenario() -> None:
            incomplete = await self._run("incomplete")
            wrong_run = await self._run("wrong-run-id")
            wrong_requested = await self._run("wrong-requested")
            self.assertEqual(incomplete.process_result.returncode, 0)
            self.assertEqual(incomplete.kind, ShadPS4ValidationResultKind.INCOMPLETE_EVIDENCE)
            self.assertIn("2/3 records", incomplete.reason)
            self.assertEqual(wrong_run.kind, ShadPS4ValidationResultKind.INCOMPLETE_EVIDENCE)
            self.assertIn("run_id mismatch", wrong_run.reason)
            self.assertEqual(wrong_requested.kind, ShadPS4ValidationResultKind.INCOMPLETE_EVIDENCE)
            self.assertIn("requested cycle count mismatch", wrong_requested.reason)
        asyncio.run(scenario())

    def test_explicit_runner_or_cycle_failure_is_validation_failure(self) -> None:
        async def scenario() -> None:
            failed_state = await self._run("failed-state")
            failed_cycle = await self._run("failed-cycle")
            nonzero = await self._run("nonzero-failure")
            self.assertEqual(failed_state.kind, ShadPS4ValidationResultKind.VALIDATION_FAILED)
            self.assertIn("runner state reports failure", failed_state.reason)
            self.assertEqual(failed_cycle.kind, ShadPS4ValidationResultKind.VALIDATION_FAILED)
            self.assertIn("reports failure status", failed_cycle.reason)
            self.assertEqual(nonzero.kind, ShadPS4ValidationResultKind.VALIDATION_FAILED)
            self.assertEqual(nonzero.process_result.returncode, 2)
        asyncio.run(scenario())

    def test_process_crash_and_watchdog_stall_never_succeed(self) -> None:
        async def scenario() -> None:
            crashed = await self._run("crash")
            stalled = await self._run(
                "hang",
                stall_timeout_seconds=0.12,
                watchdog_poll_interval_seconds=0.02,
            )
            self.assertNotEqual(crashed.kind, ShadPS4ValidationResultKind.SUCCESS)
            self.assertEqual(crashed.process_result.returncode, 23)
            self.assertEqual(stalled.kind, ShadPS4ValidationResultKind.PROCESS_FAILURE)
            self.assertEqual(stalled.process_result.state, "STALLED")
        asyncio.run(scenario())

    def test_stage_timeout_remains_process_failure(self) -> None:
        async def scenario() -> None:
            result = await self._run("hang", timeout_seconds=0.12)
            self.assertEqual(result.kind, ShadPS4ValidationResultKind.PROCESS_FAILURE)
            self.assertEqual(result.process_result.state, "TIMED_OUT")
        asyncio.run(scenario())

    def test_unknown_argv_placeholder_fails_closed(self) -> None:
        async def scenario() -> None:
            with self.assertRaisesRegex(ValueError, "unsupported shadPS4 argv placeholder"):
                await self.validator.start(
                    task_id="task-1",
                    cwd=self.root,
                    generation=1,
                    candidate_sha="b" * 40,
                    requested_cycles=1,
                    argv_template=[sys.executable, str(self.harness), "{unknown}"],
                )
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
