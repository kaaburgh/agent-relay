from __future__ import annotations

import asyncio
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_relay.evidence import latest_review, latest_validation
from agent_relay.git_workspace import candidate_generations
from agent_relay.orchestrator import SimulationOrchestrator
from agent_relay.store import Store
from agent_relay.workflow import WorkflowStage


class HappyPathIntegrationTests(unittest.TestCase):
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

    def test_writer_validator_independent_reviewer_reaches_done_with_exact_provenance(self) -> None:
        async def scenario() -> None:
            orchestrator = SimulationOrchestrator(store=self.store, runtime_root=self.root / "runtime")
            result = await orchestrator.run_happy_path(
                "task-1",
                writer_behavior=[
                    {"modify_file": {"path": "example.txt", "content": "candidate\n"}},
                    {"commit": {"message": "candidate"}},
                    {"result": {"status": "success"}},
                ],
                validation_cycles=3,
                validation_behavior={"cycle_duration": 0.01},
                reviewer_behavior=[
                    {
                        "action": "review",
                        "value": {
                            "verdict": "APPROVE",
                            "findings": [],
                            "summary": "validated candidate is acceptable",
                        },
                    }
                ],
            )
            self.assertEqual(result.task.stage, WorkflowStage.DONE.value)
            self.assertEqual(result.candidate.generation, 1)
            self.assertEqual(result.validation.generation, 1)
            self.assertEqual(result.review.generation, 1)
            self.assertEqual(result.validation.candidate_sha, result.candidate.candidate_sha)
            self.assertEqual(result.review.candidate_sha, result.candidate.candidate_sha)
            self.assertEqual(result.review.verdict, "APPROVE")

            generations = candidate_generations(self.store, "task-1")
            self.assertEqual(len(generations), 1)
            persisted_validation = latest_validation(self.store, "task-1", 1)
            persisted_review = latest_review(self.store, "task-1", 1)
            self.assertIsNotNone(persisted_validation)
            self.assertIsNotNone(persisted_review)
            self.assertEqual(persisted_validation.candidate_sha, generations[0].candidate_sha)
            self.assertEqual(persisted_review.candidate_sha, generations[0].candidate_sha)

            event_types = [event.event_type for event in self.store.events("task-1")]
            self.assertEqual(
                event_types,
                [
                    "task_created",
                    "stage_started",
                    "candidate_commit_detected",
                    "validation_started",
                    "validation_finished",
                    "review_started",
                    "review_finished",
                    "task_completed",
                ],
            )
            self.assertEqual(len(self.store.attempts("task-1", "writer")), 1)
            self.assertEqual(len(self.store.attempts("task-1", "validation")), 1)
            self.assertEqual(len(self.store.attempts("task-1", "reviewer")), 1)
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
