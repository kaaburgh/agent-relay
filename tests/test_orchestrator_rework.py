from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_relay.evidence import latest_review, latest_validation
from agent_relay.git_workspace import candidate_generations
from agent_relay.orchestrator import SimulationOrchestrator
from agent_relay.store import Store
from agent_relay.workflow import WorkflowStage


REQUEST_CHANGES = {
    "verdict": "REQUEST_CHANGES",
    "findings": [
        {
            "severity": "HIGH",
            "title": "preserve marker",
            "problem": "generation one lacks the required marker",
            "failure_scenario": "acceptance check cannot identify corrected output",
            "required_action": "write the corrected marker in example.txt",
        }
    ],
    "summary": "one correction is required",
}
APPROVE = {"verdict": "APPROVE", "findings": [], "summary": "correction verified"}


class ReworkIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.repo = self.root / "source"
        self.repo.mkdir()
        self._git(self.repo, "init", "-b", "main")
        self._git(self.repo, "config", "user.email", "agent-relay@example.invalid")
        self._git(self.repo, "config", "user.name", "Agent Relay Test")
        (self.repo / "example.txt").write_text("baseline\n", encoding="utf-8")
        self._git(self.repo, "add", "example.txt")
        self._git(self.repo, "commit", "-m", "baseline")
        self.store = Store(self.root / "state.sqlite3")
        self.store.create_task(
            task_id="task-1",
            repository=str(self.repo),
            baseline_ref="main",
            task_spec={"repository": str(self.repo), "baseline": "main"},
        )

    def tearDown(self) -> None:
        self.store.close()

    @staticmethod
    def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)

    def test_request_changes_flows_to_fresh_rework_generation_and_fresh_review(self) -> None:
        async def scenario() -> None:
            runtime = self.root / "runtime"
            orchestrator = SimulationOrchestrator(store=self.store, runtime_root=runtime)
            result = await orchestrator.run_single_rework_cycle(
                "task-1",
                initial_writer_behavior=[
                    {"modify_file": {"path": "example.txt", "content": "candidate one\n"}},
                    {"commit": {"message": "candidate one"}},
                    {"result": {"status": "success"}},
                ],
                initial_reviewer_behavior=[{"action": "review", "value": REQUEST_CHANGES}],
                rework_writer_behavior=[
                    {"modify_file": {"path": "example.txt", "content": "candidate two corrected marker\n"}},
                    {"commit": {"message": "candidate two"}},
                    {"result": {"status": "success"}},
                ],
                final_reviewer_behavior=[{"action": "review", "value": APPROVE}],
            )
            self.assertEqual(result.task.stage, WorkflowStage.DONE.value)
            self.assertEqual(result.first_candidate.generation, 1)
            self.assertEqual(result.final_candidate.generation, 2)
            self.assertNotEqual(result.first_candidate.candidate_sha, result.final_candidate.candidate_sha)
            self.assertEqual(result.first_review.verdict, "REQUEST_CHANGES")
            self.assertEqual(result.final_review.verdict, "APPROVE")
            self.assertNotEqual(result.first_review.run_id, result.final_review.run_id)
            self.assertEqual(result.first_validation.candidate_sha, result.first_candidate.candidate_sha)
            self.assertEqual(result.first_review.candidate_sha, result.first_candidate.candidate_sha)
            self.assertEqual(result.final_validation.candidate_sha, result.final_candidate.candidate_sha)
            self.assertEqual(result.final_review.candidate_sha, result.final_candidate.candidate_sha)

            generations = candidate_generations(self.store, "task-1")
            self.assertEqual([g.generation for g in generations], [1, 2])
            self.assertEqual(latest_validation(self.store, "task-1", 1).candidate_sha, generations[0].candidate_sha)
            self.assertEqual(latest_review(self.store, "task-1", 1).verdict, "REQUEST_CHANGES")
            self.assertEqual(latest_validation(self.store, "task-1", 2).candidate_sha, generations[1].candidate_sha)
            self.assertEqual(latest_review(self.store, "task-1", 2).verdict, "APPROVE")

            writers = self.store.attempts("task-1", "writer")
            self.assertEqual(len(writers), 2)
            self.assertEqual(writers[1].generation, 1)
            feedback_path = runtime / "artifacts" / "tasks" / "task-1" / "attempts" / "writer-002" / "inputs.json"
            feedback = json.loads(feedback_path.read_text(encoding="utf-8"))["review_feedback"]
            self.assertEqual(feedback[0]["severity"], "HIGH")
            self.assertIn("corrected marker", feedback[0]["required_action"])

            review_rows = self.store._conn.execute(
                "SELECT review_id,generation,verdict FROM reviews WHERE task_id=? ORDER BY review_id", ("task-1",)
            ).fetchall()
            self.assertEqual([(row["generation"], row["verdict"]) for row in review_rows], [(1, "REQUEST_CHANGES"), (2, "APPROVE")])
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                self.store._conn.execute(
                    "UPDATE reviews SET verdict='APPROVE' WHERE review_id=?", (review_rows[0]["review_id"],)
                )
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                self.store._conn.execute(
                    "DELETE FROM reviews WHERE review_id=?", (review_rows[0]["review_id"],)
                )

            event_types = [event.event_type for event in self.store.events("task-1")]
            self.assertEqual(event_types.count("candidate_commit_detected"), 2)
            self.assertEqual(event_types.count("validation_finished"), 2)
            self.assertEqual(event_types.count("review_finished"), 2)
            self.assertIn("review_requested_changes", event_types)
            self.assertEqual(event_types[-1], "task_completed")
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
