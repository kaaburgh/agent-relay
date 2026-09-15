from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_relay.artifacts import ArtifactManager
from agent_relay.git_workspace import GitWorkspaceManager, candidate_generations
from agent_relay.runtime_safety import (
    capture_process_identity,
    ensure_runtime_safety_guards,
    persist_process_identity,
)
from agent_relay.store import Store, utc_now
from agent_relay.workflow import WorkflowStage, WorkflowStateMachine
from agent_relay.writer_recovery import WriterRecoveryError, reconcile_writer_attempt


class WriterRecoveryTests(unittest.TestCase):
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
        WorkflowStateMachine(self.store).transition("task-1", WorkflowStage.WORK)
        self.git = GitWorkspaceManager(self.root / "managed")
        self.workspaces = self.git.create_writer_worktree(
            task_id="task-1", repository=self.repo, baseline_ref="main"
        )
        self.artifact_root = self.root / "artifacts"
        self.artifacts = ArtifactManager(self.artifact_root, self.store)

    def tearDown(self) -> None:
        try:
            self.store.close()
        except Exception:
            pass

    @staticmethod
    def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)

    def _record_detached_process(self, *, attempt_id: int, process: subprocess.Popen[bytes]) -> None:
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
                    attempt_id,
                    process.pid,
                    os.getpgid(process.pid),
                    json.dumps(["simulated-writer-detached"]),
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
                (process.pid, attempt_id),
            )

    def test_separate_worker_finishes_after_store_closes_and_new_store_recovers_once(self) -> None:
        behavior = [
            {"sleep": 0.25},
            {"modify_file": {"path": "example.txt", "content": "recovered candidate\n"}},
            {"commit": {"message": "candidate completed while orchestrator absent"}},
            {"result": {"status": "success", "handoff": "durable"}},
        ]
        layout = self.artifacts.create_attempt(
            task_id="task-1",
            kind="writer",
            inputs={"behavior": behavior, "baseline_sha": self.workspaces.baseline_sha},
            command=["simulated-writer-detached"],
        )
        script = layout.directory / "writer-behavior.json"
        provider_result = layout.directory / "provider-result.json"
        script.write_text(json.dumps(behavior), encoding="utf-8")
        out = layout.stdout_path.open("ab", buffering=0)
        err = layout.stderr_path.open("ab", buffering=0)
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "agent_relay.simulated_writer_worker",
                "--script",
                str(script),
                "--worktree",
                str(self.workspaces.writer),
                "--result",
                str(provider_result),
            ],
            cwd=self.workspaces.writer,
            stdout=out,
            stderr=err,
            start_new_session=True,
        )
        out.close()
        err.close()
        self._record_detached_process(attempt_id=layout.attempt.attempt_id, process=process)

        while_live = reconcile_writer_attempt(
            self.store,
            artifact_root=self.artifact_root,
            git=self.git,
            task_id="task-1",
            writer_worktree=self.workspaces.writer,
            baseline_sha=self.workspaces.baseline_sha,
            attempt_id=layout.attempt.attempt_id,
            expected_previous_generation=0,
        )
        self.assertEqual(while_live.action, "RUNNING")
        self.assertEqual(self.store.get_attempt(layout.attempt.attempt_id).status, "RUNNING")
        self.assertEqual(len(self.store.attempts("task-1", "writer")), 1)

        self.store.close()
        self.store = None  # type: ignore[assignment]
        self.assertEqual(process.wait(timeout=10), 0)
        self.assertTrue(provider_result.exists())
        candidate_sha = self._git(self.workspaces.writer, "rev-parse", "HEAD").stdout.strip()
        self.assertNotEqual(candidate_sha, self.workspaces.baseline_sha)

        self.store = Store(self.db)
        recovered = reconcile_writer_attempt(
            self.store,
            artifact_root=self.artifact_root,
            git=self.git,
            task_id="task-1",
            writer_worktree=self.workspaces.writer,
            baseline_sha=self.workspaces.baseline_sha,
            attempt_id=layout.attempt.attempt_id,
            expected_previous_generation=0,
        )
        self.assertEqual(recovered.action, "RECOVERED")
        self.assertEqual(recovered.candidate.candidate_sha, candidate_sha)
        self.assertEqual(recovered.candidate.generation, 1)
        self.assertEqual(len(self.store.attempts("task-1", "writer")), 1)
        self.assertEqual(len(candidate_generations(self.store, "task-1")), 1)
        attempt = self.store.get_attempt(layout.attempt.attempt_id)
        self.assertEqual(attempt.status, "SUCCESS")
        self.assertIsNotNone(attempt.ended_at)
        self.assertTrue(layout.result_path.exists())
        process_row = self.store._conn.execute(
            "SELECT state,exit_status FROM processes WHERE attempt_id=?",
            (layout.attempt.attempt_id,),
        ).fetchone()
        self.assertEqual(process_row["state"], "SUCCEEDED")
        self.assertEqual(process_row["exit_status"], 0)

        event_count = len(self.store.events("task-1"))
        again = reconcile_writer_attempt(
            self.store,
            artifact_root=self.artifact_root,
            git=self.git,
            task_id="task-1",
            writer_worktree=self.workspaces.writer,
            baseline_sha=self.workspaces.baseline_sha,
            attempt_id=layout.attempt.attempt_id,
            expected_previous_generation=0,
        )
        self.assertEqual(again.action, "ALREADY_RECOVERED")
        self.assertEqual(again.candidate.candidate_sha, candidate_sha)
        self.assertEqual(len(self.store.attempts("task-1", "writer")), 1)
        self.assertEqual(len(candidate_generations(self.store, "task-1")), 1)
        self.assertEqual(len(self.store.events("task-1")), event_count)
        event_types = [event.event_type for event in self.store.events("task-1")]
        self.assertEqual(event_types.count("candidate_commit_detected"), 1)
        self.assertEqual(event_types.count("writer_recovered"), 1)

    def test_capture_failed_process_cannot_be_promoted_to_recovered_candidate(self) -> None:
        behavior = [
            {"modify_file": {"path": "example.txt", "content": "must not publish\n"}},
            {"commit": {"message": "candidate before capture failure"}},
            {"result": {"status": "success", "handoff": "looks-good"}},
        ]
        layout = self.artifacts.create_attempt(
            task_id="task-1",
            kind="writer",
            inputs={"behavior": behavior, "baseline_sha": self.workspaces.baseline_sha},
            command=["simulated-writer-detached"],
        )
        script = layout.directory / "writer-behavior.json"
        provider_result = layout.directory / "provider-result.json"
        script.write_text(json.dumps(behavior), encoding="utf-8")
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "agent_relay.simulated_writer_worker",
                "--script",
                str(script),
                "--worktree",
                str(self.workspaces.writer),
                "--result",
                str(provider_result),
            ],
            cwd=self.workspaces.writer,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        self.assertEqual(completed.returncode, 0)
        self.assertTrue(provider_result.exists())
        now = utc_now()
        with self.store._transaction():
            self.store._conn.execute(
                """
                INSERT INTO processes(
                    task_id,attempt_id,pid,process_group_id,state,command_json,
                    started_at,ended_at,exit_status,last_liveness_at
                ) VALUES (?,?,1,1,'CAPTURE_FAILED','[]',?,?,0,?)
                """,
                ("task-1", layout.attempt.attempt_id, now, now, now),
            )
            self.store._conn.execute(
                "UPDATE attempts SET status='RUNNING',pid=1 WHERE attempt_id=?",
                (layout.attempt.attempt_id,),
            )

        with self.assertRaisesRegex(WriterRecoveryError, "CAPTURE_FAILED"):
            reconcile_writer_attempt(
                self.store,
                artifact_root=self.artifact_root,
                git=self.git,
                task_id="task-1",
                writer_worktree=self.workspaces.writer,
                baseline_sha=self.workspaces.baseline_sha,
                attempt_id=layout.attempt.attempt_id,
                expected_previous_generation=0,
            )

        self.assertEqual(candidate_generations(self.store, "task-1"), ())
        self.assertEqual(self.store.get_attempt(layout.attempt.attempt_id).status, "RUNNING")


if __name__ == "__main__":
    unittest.main()