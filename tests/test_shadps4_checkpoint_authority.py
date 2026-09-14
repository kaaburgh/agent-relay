from __future__ import annotations

import asyncio
import csv
import json
import tempfile
import unittest
from pathlib import Path

from agent_relay.artifacts import ArtifactManager
from agent_relay.git_workspace import record_candidate_generation
from agent_relay.resource_leases import acquire_lease, active_leases, configure_resource
from agent_relay.shadps4_validator import (
    ShadPS4BloodborneValidator,
    ShadPS4ValidationInvocation,
    ShadPS4ValidationResultKind,
)
from agent_relay.store import Store, utc_now
from agent_relay.supervisor import ProcessResult, SubprocessSupervisor
from agent_relay.validator_recovery import reconcile_validation_attempt


class _FinishedProcess:
    def __init__(self, result: ProcessResult) -> None:
        self._result = result

    async def wait(self) -> ProcessResult:
        return self._result


def _write_success_evidence(evidence_dir: Path, *, run_id: str, requested_cycles: int) -> None:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "runner-status.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "requested_cycles": requested_cycles,
                "completed_cycles": requested_cycles,
                "state": "completed",
            }
        ),
        encoding="utf-8",
    )
    with (evidence_dir / "cycles.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["cycle", "status"])
        writer.writeheader()
        for cycle in range(1, requested_cycles + 1):
            writer.writerow({"cycle": cycle, "status": "ok"})
    (evidence_dir / "summary.md").write_text(
        f"completed {requested_cycles}/{requested_cycles}\n", encoding="utf-8"
    )


class ShadPS4CheckpointAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.store = Store(self.root / "state.sqlite3")
        self.store.create_task(
            task_id="task-1",
            repository=str(self.root),
            baseline_ref="main",
            task_spec={"repository": str(self.root)},
        )
        self.artifact_root = self.root / "artifacts"
        self.artifacts = ArtifactManager(self.artifact_root, self.store)

    def tearDown(self) -> None:
        self.store.close()

    def test_normal_finish_uses_durable_harness_checkpoint_after_wrapper_failure(self) -> None:
        async def scenario() -> None:
            validator = ShadPS4BloodborneValidator(
                store=self.store,
                artifacts=self.artifacts,
                supervisor=SubprocessSupervisor(self.store),
            )
            layout = self.artifacts.create_attempt(
                task_id="task-1",
                kind="validation",
                generation=1,
                candidate_sha="a" * 40,
                inputs={"requested_cycles": 2},
                command=["fake-wrapper"],
            )
            run_id = "checkpoint-authority-normal"
            evidence_dir = layout.directory / "evidence" / run_id
            _write_success_evidence(evidence_dir, run_id=run_id, requested_cycles=2)
            checkpoint = layout.directory / "process-exit.json"
            checkpoint.write_text(
                json.dumps({"run_id": run_id, "exit_status": 0}) + "\n",
                encoding="utf-8",
            )
            wrapper_result = ProcessResult(
                process_id=999,
                pid=999,
                process_group_id=999,
                returncode=137,
                state="FAILED",
                started_at="2026-09-14T17:00:00+00:00",
                ended_at="2026-09-14T17:00:01+00:00",
            )
            invocation = ShadPS4ValidationInvocation(
                layout=layout,
                run_id=run_id,
                evidence_dir=evidence_dir,
                exit_checkpoint_path=checkpoint,
                process=_FinishedProcess(wrapper_result),
                generation=1,
                candidate_sha="a" * 40,
                requested_cycles=2,
                argv=("fake-harness",),
            )

            result = await validator.finish(invocation)

            self.assertEqual(result.kind, ShadPS4ValidationResultKind.SUCCESS)
            self.assertEqual(result.process_result.returncode, 137)
            attempt = self.store.get_attempt(layout.attempt.attempt_id)
            self.assertEqual(attempt.status, "SUCCESS")
            self.assertEqual(attempt.exit_status, 0)
            self.assertEqual(attempt.result["process"]["returncode"], 137)
            self.assertEqual(attempt.result["process"]["harness_exit_status"], 0)

        asyncio.run(scenario())

    def test_recovery_accepts_failed_wrapper_only_when_checkpoint_proves_harness_zero(self) -> None:
        writer = self.store.allocate_attempt(task_id="task-1", kind="writer")
        candidate_sha = "b" * 40
        record_candidate_generation(
            self.store,
            task_id="task-1",
            candidate_sha=candidate_sha,
            writer_attempt_id=writer.attempt_id,
            expected_previous_generation=0,
        )
        layout = self.artifacts.create_attempt(
            task_id="task-1",
            kind="validation",
            generation=1,
            candidate_sha=candidate_sha,
            inputs={"requested_cycles": 2},
            command=["fake-wrapper"],
        )
        run_id = "checkpoint-authority-recovery"
        evidence_dir = layout.directory / "evidence" / run_id
        _write_success_evidence(evidence_dir, run_id=run_id, requested_cycles=2)
        (layout.directory / "process-exit.json").write_text(
            json.dumps({"run_id": run_id, "exit_status": 0}) + "\n",
            encoding="utf-8",
        )
        configure_resource(self.store, "bloodborne-runtime", 1)
        lease = acquire_lease(
            self.store,
            resource_name="bloodborne-runtime",
            holder_id="checkpoint-authority",
            task_id="task-1",
            attempt_id=layout.attempt.attempt_id,
        )
        now = utc_now()
        with self.store._transaction():
            self.store._conn.execute(
                "UPDATE attempts SET status='RUNNING',pid=424242 WHERE attempt_id=?",
                (layout.attempt.attempt_id,),
            )
            self.store._conn.execute(
                """
                INSERT INTO processes(
                    task_id,attempt_id,pid,process_group_id,state,command_json,
                    started_at,ended_at,exit_status,last_liveness_at
                ) VALUES (?,?,?,?, 'FAILED','[]',?,?,?,?)
                """,
                (
                    "task-1",
                    layout.attempt.attempt_id,
                    424242,
                    424242,
                    now,
                    now,
                    137,
                    now,
                ),
            )

        recovered = reconcile_validation_attempt(
            self.store,
            artifact_root=self.artifact_root,
            task_id="task-1",
            attempt_id=layout.attempt.attempt_id,
            generation=1,
            candidate_sha=candidate_sha,
            run_id=run_id,
            requested_cycles=2,
            evidence_dir=evidence_dir,
            lease=lease,
        )

        self.assertEqual(recovered.action, "RECOVERED")
        self.assertEqual(self.store.get_attempt(layout.attempt.attempt_id).status, "SUCCESS")
        self.assertEqual(self.store.get_attempt(layout.attempt.attempt_id).exit_status, 0)
        process = self.store._conn.execute(
            "SELECT state,exit_status FROM processes WHERE attempt_id=?",
            (layout.attempt.attempt_id,),
        ).fetchone()
        self.assertEqual((process["state"], process["exit_status"]), ("FAILED", 137))
        self.assertEqual(active_leases(self.store, "bloodborne-runtime"), ())


if __name__ == "__main__":
    unittest.main()
