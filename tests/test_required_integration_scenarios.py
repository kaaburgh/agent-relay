from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_relay.artifacts import ArtifactManager
from agent_relay.git_workspace import GitWorkspaceManager, record_candidate_generation
from agent_relay.provider_retry import (
    clear_provider_wait,
    record_provider_unavailable,
    resume_provider_if_due,
)
from agent_relay.resource_leases import (
    LeaseUnavailable,
    acquire_lease,
    configure_resource,
    release_lease,
)
from agent_relay.simulated_writer import SimulatedWriterProvider, WriterResultKind
from agent_relay.store import Store
from agent_relay.supervisor import SubprocessSupervisor
from agent_relay.workflow import WorkflowStage, WorkflowStateMachine

from test_guardrails import GuardrailIntegrationTests
from test_orchestrator_happy_path import HappyPathIntegrationTests
from test_orchestrator_rework import ReworkIntegrationTests
from test_validator_recovery import ValidatorRecoveryTests
from test_watchdog import WatchdogTests
from test_writer_recovery import WriterRecoveryTests


class RequiredIntegrationScenarios(unittest.TestCase):
    """One named test for every deterministic scenario in docs/spec.md.

    Existing scenario tests are deliberately re-executed here rather than merely referenced,
    so this class is a runnable acceptance matrix. Scenarios 5 and 9 add integration coverage
    that was previously only present at primitive level.
    """

    @staticmethod
    def _run_existing(case: type[unittest.TestCase], method: str) -> None:
        case(method).debug()

    def test_s01_happy_path_writer_validate_review_done(self) -> None:
        self._run_existing(
            HappyPathIntegrationTests,
            "test_writer_validator_independent_reviewer_reaches_done_with_exact_provenance",
        )

    def test_s02_review_correction_fresh_rework_and_review(self) -> None:
        self._run_existing(
            ReworkIntegrationTests,
            "test_request_changes_flows_to_fresh_rework_generation_and_fresh_review",
        )

    def test_s03_orchestrator_absent_while_writer_finishes_no_duplicate(self) -> None:
        self._run_existing(
            WriterRecoveryTests,
            "test_separate_worker_finishes_after_store_closes_and_new_store_recovers_once",
        )

    def test_s04_orchestrator_absent_during_external_validation_no_duplicate(self) -> None:
        self._run_existing(
            ValidatorRecoveryTests,
            "test_validator_finishes_while_store_closed_and_recovery_reuses_attempt_and_releases_lease",
        )

    def test_s05_real_provider_unavailable_wait_retry_then_writer_resumes(self) -> None:
        root = Path(tempfile.mkdtemp())
        repo = root / "source"
        repo.mkdir()
        self._git(repo, "init", "-b", "main")
        self._git(repo, "config", "user.email", "agent-relay@example.invalid")
        self._git(repo, "config", "user.name", "Agent Relay Scenario Test")
        (repo / "example.txt").write_text("baseline\n", encoding="utf-8")
        self._git(repo, "add", "example.txt")
        self._git(repo, "commit", "-m", "baseline")
        store = Store(root / "state.sqlite3")
        try:
            store.create_task(
                task_id="task-1",
                repository=str(repo),
                baseline_ref="main",
                task_spec={"repository": str(repo), "baseline": "main"},
            )
            workflow = WorkflowStateMachine(store)
            workflow.transition("task-1", WorkflowStage.WORK)
            git = GitWorkspaceManager(root / "managed")
            workspaces = git.create_writer_worktree(
                task_id="task-1", repository=repo, baseline_ref="main"
            )
            artifacts = ArtifactManager(root / "artifacts", store)
            provider = SimulatedWriterProvider(
                store=store,
                artifacts=artifacts,
                supervisor=SubprocessSupervisor(store),
                git=git,
            )

            async def scenario() -> None:
                unavailable = await provider.run(
                    task_id="task-1",
                    writer_worktree=workspaces.writer,
                    baseline_sha=workspaces.baseline_sha,
                    behavior=[{"provider_unavailable": "simulated quota"}],
                )
                self.assertEqual(unavailable.kind, WriterResultKind.PROVIDER_UNAVAILABLE)

                t0 = datetime(2026, 9, 14, 8, 0, tzinfo=timezone.utc)
                wait = record_provider_unavailable(
                    store,
                    task_id="task-1",
                    provider="simulated-writer",
                    reason=unavailable.reason or "unavailable",
                    now=t0,
                    base_delay_seconds=5,
                    max_delay_seconds=20,
                )
                self.assertEqual(store.get_task("task-1").stage, WorkflowStage.WAITING_PROVIDER.value)
                self.assertFalse(
                    resume_provider_if_due(store, task_id="task-1", now=t0 + timedelta(seconds=4))
                )
                self.assertTrue(
                    resume_provider_if_due(store, task_id="task-1", now=t0 + timedelta(seconds=5))
                )
                self.assertEqual(store.get_task("task-1").stage, WorkflowStage.WORK.value)

                success = await provider.run(
                    task_id="task-1",
                    writer_worktree=workspaces.writer,
                    baseline_sha=workspaces.baseline_sha,
                    behavior=[
                        {"modify_file": {"path": "example.txt", "content": "provider recovered\n"}},
                        {"commit": {"message": "provider recovered"}},
                        {"result": {"status": "success"}},
                    ],
                )
                self.assertEqual(success.kind, WriterResultKind.SUCCESS)
                self.assertIsNotNone(success.candidate_sha)
                self.assertTrue(
                    clear_provider_wait(
                        store,
                        task_id="task-1",
                        provider="simulated-writer",
                        now=t0 + timedelta(seconds=6),
                    )
                )
                candidate = record_candidate_generation(
                    store,
                    task_id="task-1",
                    candidate_sha=success.candidate_sha,
                    writer_attempt_id=success.attempt_id,
                    expected_previous_generation=0,
                )
                workflow.transition("task-1", WorkflowStage.VALIDATE)
                self.assertEqual(candidate.generation, 1)
                self.assertEqual(store.get_task("task-1").stage, WorkflowStage.VALIDATE.value)
                self.assertEqual(len(store.attempts("task-1", "writer")), 2)
                events = [event.event_type for event in store.events("task-1")]
                self.assertIn("provider_unavailable", events)
                self.assertIn("provider_retry_started", events)
                self.assertIn("provider_available", events)
                self.assertIn("candidate_commit_detected", events)

            asyncio.run(scenario())
        finally:
            store.close()

    def test_s06_malformed_reviewer_never_approves(self) -> None:
        self._run_existing(
            GuardrailIntegrationTests,
            "test_malformed_reviewer_output_blocks_and_never_creates_approval",
        )

    def test_s07_exit_zero_incomplete_validation_never_passes(self) -> None:
        self._run_existing(
            GuardrailIntegrationTests,
            "test_exit_zero_incomplete_validation_evidence_blocks_before_review",
        )

    def test_s08_hanging_tool_watchdog_cleans_group_and_preserves_evidence(self) -> None:
        self._run_existing(
            WatchdogTests,
            "test_stalled_validator_kills_whole_group_and_preserves_partial_evidence",
        )

    def test_s09_two_real_fake_runtimes_obey_capacity_one_lease(self) -> None:
        async def scenario() -> None:
            root = Path(tempfile.mkdtemp())
            store = Store(root / "state.sqlite3")
            try:
                for task_id in ("task-a", "task-b"):
                    store.create_task(
                        task_id=task_id,
                        repository=str(root),
                        baseline_ref="main",
                        task_spec={"repository": str(root)},
                    )
                configure_resource(store, "bloodborne-runtime", 1)
                artifacts = ArtifactManager(root / "artifacts", store)
                supervisor = SubprocessSupervisor(store)

                async def launch(task_id: str, attempt_id: int, run_id: str, directory: Path):
                    config = directory / "validator-config.json"
                    evidence = directory / "evidence" / run_id
                    config.write_text(
                        json.dumps(
                            {
                                "run_id": run_id,
                                "requested_cycles": 2,
                                "cycle_duration": 0.12,
                            }
                        ),
                        encoding="utf-8",
                    )
                    attempt = store.get_attempt(attempt_id)
                    layout_dir = artifacts.root / "tasks" / task_id / attempt.artifact_dir
                    return await supervisor.start(
                        task_id=task_id,
                        attempt_id=attempt_id,
                        argv=[
                            sys.executable,
                            "-m",
                            "agent_relay.simulated_validator_worker",
                            "--config",
                            str(config),
                            "--evidence-dir",
                            str(evidence),
                        ],
                        cwd=root,
                        stdout_path=layout_dir / "stdout.log",
                        stderr_path=layout_dir / "stderr.log",
                        heartbeat_interval=0.02,
                        terminate_grace_seconds=0.05,
                    )

                layout_a = artifacts.create_attempt(task_id="task-a", kind="validation")
                lease_a = acquire_lease(
                    store,
                    resource_name="bloodborne-runtime",
                    holder_id="runtime-a",
                    task_id="task-a",
                    attempt_id=layout_a.attempt.attempt_id,
                )
                run_a = str(uuid.uuid4())
                handle_a = await launch(
                    "task-a", layout_a.attempt.attempt_id, run_a, layout_a.directory
                )
                await asyncio.sleep(0.05)
                self.assertIsNone(handle_a.process.returncode)

                layout_b = artifacts.create_attempt(task_id="task-b", kind="validation")
                with self.assertRaises(LeaseUnavailable):
                    acquire_lease(
                        store,
                        resource_name="bloodborne-runtime",
                        holder_id="runtime-b-denied",
                        task_id="task-b",
                        attempt_id=layout_b.attempt.attempt_id,
                    )
                running_b = store._conn.execute(
                    "SELECT COUNT(*) FROM processes WHERE task_id='task-b' AND state='RUNNING'"
                ).fetchone()[0]
                self.assertEqual(running_b, 0)

                result_a = await handle_a.wait()
                self.assertEqual(result_a.state, "SUCCEEDED")
                release_lease(store, lease_id=lease_a.lease_id, holder_id=lease_a.holder_id)

                lease_b = acquire_lease(
                    store,
                    resource_name="bloodborne-runtime",
                    holder_id="runtime-b",
                    task_id="task-b",
                    attempt_id=layout_b.attempt.attempt_id,
                )
                handle_b = await launch(
                    "task-b", layout_b.attempt.attempt_id, str(uuid.uuid4()), layout_b.directory
                )
                result_b = await handle_b.wait()
                self.assertEqual(result_b.state, "SUCCEEDED")
                release_lease(store, lease_id=lease_b.lease_id, holder_id=lease_b.holder_id)

                rows = store._conn.execute(
                    "SELECT task_id,started_at,ended_at FROM processes ORDER BY process_id"
                ).fetchall()
                self.assertEqual([row["task_id"] for row in rows], ["task-a", "task-b"])
                self.assertLessEqual(rows[0]["ended_at"], rows[1]["started_at"])
            finally:
                store.close()

        asyncio.run(scenario())

    def test_s10_two_review_rework_rounds_before_approval_keep_history(self) -> None:
        self._run_existing(
            GuardrailIntegrationTests,
            "test_two_rework_rounds_then_approval_preserve_all_history",
        )

    def test_s11_correction_limit_blocks_instead_of_looping(self) -> None:
        self._run_existing(
            GuardrailIntegrationTests,
            "test_correction_limit_blocks_after_configured_rework_rounds",
        )

    def test_s12_worker_completes_while_supervisor_absent_checkpoint_is_reused(self) -> None:
        self._run_existing(
            WriterRecoveryTests,
            "test_separate_worker_finishes_after_store_closes_and_new_store_recovers_once",
        )

    @staticmethod
    def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
