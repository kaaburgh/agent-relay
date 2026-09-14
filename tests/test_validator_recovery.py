from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import uuid
import unittest
from pathlib import Path

from agent_relay.artifacts import ArtifactManager
from agent_relay.git_workspace import GitWorkspaceManager, record_candidate_generation
from agent_relay.resource_leases import acquire_lease, active_leases, configure_resource
from agent_relay.runtime_safety import (
    capture_process_identity,
    ensure_runtime_safety_guards,
    persist_process_identity,
)
from agent_relay.store import Store, utc_now
from agent_relay.validator_recovery import reconcile_validation_attempt
from agent_relay.workflow import WorkflowStage, WorkflowStateMachine


class ValidatorRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.repo = self.root / "source"
        self.repo.mkdir()
        self._git(self.repo, "init", "-b", "main")
        self._git(self.repo, "config", "user.email", "agent-relay@example.invalid")
        self._git(self.repo, "config", "user.name", "Agent Relay Recovery Test")
        (self.repo / "example.txt").write_text("baseline\n", encoding="utf-8")
        self._git(self.repo, "add", "example.txt")
        self._git(self.repo, "commit", "-m", "baseline")
        self.db = self.root / "state.sqlite3"
        self.store = Store(self.db)
        ensure_runtime_safety_guards(self.store)
        self.store.create_task(
            task_id="task-1",
            repository=str(self.repo),
            baseline_ref="main",
            task_spec={"repository": str(self.repo), "baseline": "main"},
        )
        self.workflow = WorkflowStateMachine(self.store)
        self.workflow.transition("task-1", WorkflowStage.WORK)
        self.git = GitWorkspaceManager(self.root / "managed")
        workspaces = self.git.create_writer_worktree(
            task_id="task-1", repository=self.repo, baseline_ref="main"
        )
        (workspaces.writer / "example.txt").write_text("candidate\n", encoding="utf-8")
        self._git(workspaces.writer, "add", "example.txt")
        self._git(workspaces.writer, "commit", "-m", "candidate")
        self.candidate_sha = self._git(workspaces.writer, "rev-parse", "HEAD").stdout.strip()
        writer_attempt = self.store.allocate_attempt(task_id="task-1", kind="writer")
        record_candidate_generation(
            self.store,
            task_id="task-1",
            candidate_sha=self.candidate_sha,
            writer_attempt_id=writer_attempt.attempt_id,
            expected_previous_generation=0,
        )
        self.workflow.transition("task-1", WorkflowStage.VALIDATE)
        self.artifact_root = self.root / "artifacts"
        self.artifacts = ArtifactManager(self.artifact_root, self.store)
        configure_resource(self.store, "expensive-runtime", 1)

    def tearDown(self) -> None:
        try:
            self.store.close()
        except Exception:
            pass

    @staticmethod
    def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)

    def _new_attempt(
        self,
        *,
        requested_cycles: int = 3,
        exit_code: int = 0,
    ) -> tuple[object, str, Path, Path]:
        run_id = str(uuid.uuid4())
        layout = self.artifacts.create_attempt(
            task_id="task-1",
            kind="validation",
            generation=1,
            candidate_sha=self.candidate_sha,
            inputs={"run_id": run_id, "requested_cycles": requested_cycles},
            command=["simulated-validator-detached"],
        )
        config_path = layout.directory / "validator-config.json"
        evidence_dir = layout.directory / "evidence" / run_id
        config_path.write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "requested_cycles": requested_cycles,
                    "cycle_duration": 0.12,
                    "exit_code": exit_code,
                }
            ),
            encoding="utf-8",
        )
        return layout, run_id, config_path, evidence_dir

    def _launch_detached(
        self,
        *,
        layout: object,
        run_id: str,
        config_path: Path,
        evidence_dir: Path,
    ) -> subprocess.Popen[bytes]:
        exit_checkpoint = layout.directory / "process-exit.json"
        out = layout.stdout_path.open("ab", buffering=0)
        err = layout.stderr_path.open("ab", buffering=0)
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "agent_relay.exit_checkpoint_worker",
                "--checkpoint",
                str(exit_checkpoint),
                "--run-id",
                run_id,
                "--",
                sys.executable,
                "-m",
                "agent_relay.simulated_validator_worker",
                "--config",
                str(config_path),
                "--evidence-dir",
                str(evidence_dir),
            ],
            cwd=self.root,
            stdout=out,
            stderr=err,
            start_new_session=True,
        )
        out.close()
        err.close()
        launched = utc_now()
        identity = capture_process_identity(process.pid)
        with self.store._transaction():
            cursor = self.store._conn.execute(
                """
                INSERT INTO processes(
                    task_id,attempt_id,pid,process_group_id,state,command_json,
                    started_at,last_liveness_at
                ) VALUES (?,?,?,?, 'RUNNING', ?, ?, ?)
                """,
                (
                    "task-1",
                    layout.attempt.attempt_id,
                    process.pid,
                    os.getpgid(process.pid),
                    json.dumps(["simulated-validator-detached"]),
                    launched,
                    launched,
                ),
            )
            persist_process_identity(
                self.store,
                process_id=int(cursor.lastrowid),
                identity=identity,
            )
            self.store._conn.execute(
                "UPDATE attempts SET pid=?,status='RUNNING' WHERE attempt_id=?",
                (process.pid, layout.attempt.attempt_id),
            )
        return process

    def test_validator_finishes_while_store_closed_and_recovery_reuses_attempt_and_releases_lease(self) -> None:
        layout, run_id, config_path, evidence_dir = self._new_attempt()
        lease = acquire_lease(
            self.store,
            resource_name="expensive-runtime",
            holder_id=f"validation-{layout.attempt.attempt_id}",
            task_id="task-1",
            attempt_id=layout.attempt.attempt_id,
        )
        process = self._launch_detached(
            layout=layout,
            run_id=run_id,
            config_path=config_path,
            evidence_dir=evidence_dir,
        )

        live = reconcile_validation_attempt(
            self.store,
            artifact_root=self.artifact_root,
            task_id="task-1",
            attempt_id=layout.attempt.attempt_id,
            generation=1,
            candidate_sha=self.candidate_sha,
            run_id=run_id,
            requested_cycles=3,
            evidence_dir=evidence_dir,
            lease=lease,
        )
        self.assertEqual(live.action, "RUNNING")
        self.assertEqual(self.store.get_attempt(layout.attempt.attempt_id).status, "RUNNING")
        self.assertEqual(len(self.store.attempts("task-1", "validation")), 1)

        self.store.close()
        self.store = None  # type: ignore[assignment]
        self.assertEqual(process.wait(timeout=10), 0)
        self.assertTrue((layout.directory / "process-exit.json").exists())
        self.assertTrue((evidence_dir / "runner-status.json").exists())
        self.assertTrue((evidence_dir / "cycles.csv").exists())
        self.assertTrue((evidence_dir / "summary.md").exists())

        self.store = Store(self.db)
        recovered_lease = active_leases(self.store, "expensive-runtime")
        self.assertEqual(len(recovered_lease), 1)
        recovered = reconcile_validation_attempt(
            self.store,
            artifact_root=self.artifact_root,
            task_id="task-1",
            attempt_id=layout.attempt.attempt_id,
            generation=1,
            candidate_sha=self.candidate_sha,
            run_id=run_id,
            requested_cycles=3,
            evidence_dir=evidence_dir,
            lease=recovered_lease[0],
        )
        self.assertEqual(recovered.action, "RECOVERED")
        self.assertEqual(recovered.validation.candidate_sha, self.candidate_sha)
        self.assertEqual(recovered.validation.generation, 1)
        self.assertEqual(len(self.store.attempts("task-1", "validation")), 1)
        self.assertEqual(active_leases(self.store, "expensive-runtime"), ())
        process_row = self.store._conn.execute(
            "SELECT state,exit_status FROM processes WHERE attempt_id=?",
            (layout.attempt.attempt_id,),
        ).fetchone()
        self.assertEqual((process_row["state"], process_row["exit_status"]), ("SUCCEEDED", 0))
        attempt = self.store.get_attempt(layout.attempt.attempt_id)
        self.assertEqual(attempt.status, "SUCCESS")
        self.assertIsNotNone(attempt.ended_at)
        self.assertTrue(layout.result_path.exists())
        validations = self.store._conn.execute(
            "SELECT validation_id FROM validations WHERE attempt_id=?",
            (layout.attempt.attempt_id,),
        ).fetchall()
        self.assertEqual(len(validations), 1)
        event_types = [event.event_type for event in self.store.events("task-1")]
        self.assertEqual(event_types.count("validation_finished"), 1)
        self.assertEqual(event_types.count("validation_recovered"), 1)
        self.assertEqual(event_types.count("resource_released"), 1)

    def test_complete_evidence_from_nonzero_exit_never_recovers_success(self) -> None:
        layout, run_id, config_path, evidence_dir = self._new_attempt(exit_code=7)
        lease = acquire_lease(
            self.store,
            resource_name="expensive-runtime",
            holder_id=f"nonzero-{layout.attempt.attempt_id}",
            task_id="task-1",
            attempt_id=layout.attempt.attempt_id,
        )
        process = self._launch_detached(
            layout=layout,
            run_id=run_id,
            config_path=config_path,
            evidence_dir=evidence_dir,
        )
        self.store.close()
        self.store = None  # type: ignore[assignment]
        self.assertEqual(process.wait(timeout=10), 7)

        self.store = Store(self.db)
        recovered_lease = active_leases(self.store, "expensive-runtime")[0]
        result = reconcile_validation_attempt(
            self.store,
            artifact_root=self.artifact_root,
            task_id="task-1",
            attempt_id=layout.attempt.attempt_id,
            generation=1,
            candidate_sha=self.candidate_sha,
            run_id=run_id,
            requested_cycles=3,
            evidence_dir=evidence_dir,
            lease=recovered_lease,
        )
        self.assertEqual(result.action, "AMBIGUOUS")
        self.assertIn("nonzero exit 7", result.reason)
        self.assertEqual(len(active_leases(self.store, "expensive-runtime")), 1)
        self.assertEqual(
            self.store._conn.execute(
                "SELECT COUNT(*) FROM validations WHERE attempt_id=?", (layout.attempt.attempt_id,)
            ).fetchone()[0],
            0,
        )
        self.assertEqual(self.store.get_attempt(layout.attempt.attempt_id).status, "RUNNING")

    def test_dead_process_with_incomplete_evidence_is_ambiguous_and_keeps_lease(self) -> None:
        layout, run_id, _config_path, evidence_dir = self._new_attempt(requested_cycles=2)
        lease = acquire_lease(
            self.store,
            resource_name="expensive-runtime",
            holder_id=f"ambiguous-{layout.attempt.attempt_id}",
            task_id="task-1",
            attempt_id=layout.attempt.attempt_id,
        )
        launched = utc_now()
        with self.store._transaction():
            self.store._conn.execute(
                """
                INSERT INTO processes(
                    task_id,attempt_id,pid,process_group_id,state,command_json,
                    started_at,last_liveness_at
                ) VALUES (?,?,?,?, 'RUNNING', ?, ?, ?)
                """,
                (
                    "task-1",
                    layout.attempt.attempt_id,
                    2147483646,
                    2147483646,
                    json.dumps(["dead-validator"]),
                    launched,
                    launched,
                ),
            )
        result = reconcile_validation_attempt(
            self.store,
            artifact_root=self.artifact_root,
            task_id="task-1",
            attempt_id=layout.attempt.attempt_id,
            generation=1,
            candidate_sha=self.candidate_sha,
            run_id=run_id,
            requested_cycles=2,
            evidence_dir=evidence_dir,
            lease=lease,
        )
        self.assertEqual(result.action, "AMBIGUOUS")
        self.assertIn("incomplete", result.reason)
        self.assertEqual(len(active_leases(self.store, "expensive-runtime")), 1)
        self.assertEqual(self.store.get_attempt(layout.attempt.attempt_id).status, "CREATED")
        self.assertEqual(
            self.store._conn.execute(
                "SELECT COUNT(*) FROM validations WHERE attempt_id=?", (layout.attempt.attempt_id,)
            ).fetchone()[0],
            0,
        )


if __name__ == "__main__":
    unittest.main()
