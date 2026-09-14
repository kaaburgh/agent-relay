from __future__ import annotations

import asyncio
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_relay.evidence import latest_validation
from agent_relay.guardrails import GuardrailAction
from agent_relay.orchestrator import OrchestrationError, SimulationOrchestrator
from agent_relay.store import Store
from agent_relay.workflow import WorkflowStage


APPROVE = {"verdict": "APPROVE", "findings": [], "summary": "candidate accepted"}


def request_changes(round_number: int) -> dict[str, object]:
    return {
        "verdict": "REQUEST_CHANGES",
        "findings": [
            {
                "severity": "HIGH",
                "title": f"correction {round_number}",
                "problem": f"generation {round_number} still violates acceptance",
                "failure_scenario": "the requested marker is not yet final",
                "required_action": f"apply correction {round_number}",
            }
        ],
        "summary": f"correction round {round_number} required",
    }


def writer_behavior(number: int) -> list[dict[str, object]]:
    return [
        {"modify_file": {"path": "example.txt", "content": f"candidate {number}\n"}},
        {"commit": {"message": f"candidate {number}"}},
        {"result": {"status": "success"}},
    ]


class GuardrailIntegrationTests(unittest.TestCase):
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

    def tearDown(self) -> None:
        self.store.close()

    @staticmethod
    def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)

    def _create_task(self, *, max_rounds: int = 3) -> None:
        self.store.create_task(
            task_id="task-1",
            repository=str(self.repo),
            baseline_ref="main",
            task_spec={
                "repository": str(self.repo),
                "baseline": "main",
                "max_correction_rounds": max_rounds,
            },
        )

    def test_malformed_reviewer_output_blocks_and_never_creates_approval(self) -> None:
        self._create_task()

        async def scenario() -> None:
            orchestrator = SimulationOrchestrator(store=self.store, runtime_root=self.root / "runtime")
            with self.assertRaisesRegex(OrchestrationError, "malformed"):
                await orchestrator.run_happy_path(
                    "task-1",
                    writer_behavior=writer_behavior(1),
                    validation_cycles=2,
                    reviewer_behavior=[{"action": "raw", "text": "not-json APPROVE"}],
                )

            task = self.store.get_task("task-1")
            self.assertEqual(task.stage, WorkflowStage.BLOCKED.value)
            review_rows = self.store._conn.execute(
                "SELECT * FROM reviews WHERE task_id=?", ("task-1",)
            ).fetchall()
            self.assertEqual(review_rows, [])
            reviewer_attempts = self.store.attempts("task-1", "reviewer")
            self.assertEqual(len(reviewer_attempts), 1)
            self.assertEqual(reviewer_attempts[0].status, "MALFORMED")
            events = [event.event_type for event in self.store.events("task-1")]
            self.assertIn("review_invalid_output", events)
            self.assertNotIn("task_completed", events)

        asyncio.run(scenario())

    def test_exit_zero_incomplete_validation_evidence_blocks_before_review(self) -> None:
        self._create_task()

        async def scenario() -> None:
            orchestrator = SimulationOrchestrator(store=self.store, runtime_root=self.root / "runtime")
            with self.assertRaisesRegex(OrchestrationError, "incomplete evidence"):
                await orchestrator.run_happy_path(
                    "task-1",
                    writer_behavior=writer_behavior(1),
                    validation_cycles=3,
                    validation_behavior={"incomplete_cycles": 1},
                    reviewer_behavior=[{"action": "review", "value": APPROVE}],
                )

            task = self.store.get_task("task-1")
            self.assertEqual(task.stage, WorkflowStage.BLOCKED.value)
            validation = latest_validation(self.store, "task-1", 1)
            self.assertIsNotNone(validation)
            self.assertEqual(validation.status, "INCOMPLETE_EVIDENCE")
            self.assertEqual(validation.result["requested_cycles"], 3)
            self.assertEqual(validation.result["completed_cycles"], 3)
            self.assertEqual(self.store.attempts("task-1", "reviewer"), ())
            events = [event.event_type for event in self.store.events("task-1")]
            self.assertIn("validation_incomplete_evidence", events)
            self.assertNotIn("review_started", events)
            self.assertNotIn("task_completed", events)

        asyncio.run(scenario())

    def test_two_rework_rounds_then_approval_preserve_all_history(self) -> None:
        self._create_task(max_rounds=3)

        async def scenario() -> None:
            runtime = self.root / "runtime"
            orchestrator = SimulationOrchestrator(store=self.store, runtime_root=runtime)
            result = await orchestrator.run_correction_sequence(
                "task-1",
                writer_behaviors=[writer_behavior(1), writer_behavior(2), writer_behavior(3)],
                reviewer_behaviors=[
                    [{"action": "review", "value": request_changes(1)}],
                    [{"action": "review", "value": request_changes(2)}],
                    [{"action": "review", "value": APPROVE}],
                ],
                validation_cycles=2,
            )
            self.assertEqual(result.task.stage, WorkflowStage.DONE.value)
            self.assertEqual([candidate.generation for candidate in result.candidates], [1, 2, 3])
            self.assertEqual([review.verdict for review in result.reviews], ["REQUEST_CHANGES", "REQUEST_CHANGES", "APPROVE"])
            self.assertEqual([decision.action for decision in result.decisions], [GuardrailAction.REWORK, GuardrailAction.REWORK, GuardrailAction.DONE])
            self.assertEqual(len({review.run_id for review in result.reviews}), 3)
            self.assertEqual(len({review.attempt_id for review in result.reviews}), 3)
            self.assertEqual(len(result.validations), 3)
            for candidate, validation, review in zip(result.candidates, result.validations, result.reviews):
                self.assertEqual(validation.candidate_sha, candidate.candidate_sha)
                self.assertEqual(review.candidate_sha, candidate.candidate_sha)
                self.assertEqual(validation.generation, candidate.generation)
                self.assertEqual(review.generation, candidate.generation)

            writers = self.store.attempts("task-1", "writer")
            self.assertEqual(len(writers), 3)
            second_inputs = runtime / "artifacts" / "tasks" / "task-1" / "attempts" / "writer-002" / "inputs.json"
            third_inputs = runtime / "artifacts" / "tasks" / "task-1" / "attempts" / "writer-003" / "inputs.json"
            import json
            self.assertIn("correction 1", json.loads(second_inputs.read_text(encoding="utf-8"))["review_feedback"][0]["title"])
            self.assertIn("correction 2", json.loads(third_inputs.read_text(encoding="utf-8"))["review_feedback"][0]["title"])

            rows = self.store._conn.execute(
                "SELECT review_id,generation,verdict FROM reviews WHERE task_id=? ORDER BY review_id",
                ("task-1",),
            ).fetchall()
            self.assertEqual([(row["generation"], row["verdict"]) for row in rows], [(1, "REQUEST_CHANGES"), (2, "REQUEST_CHANGES"), (3, "APPROVE")])
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                self.store._conn.execute("DELETE FROM reviews WHERE review_id=?", (rows[0]["review_id"],))

            events = [event.event_type for event in self.store.events("task-1")]
            self.assertEqual(events.count("review_requested_changes"), 2)
            self.assertEqual(events.count("candidate_commit_detected"), 3)
            self.assertEqual(events.count("validation_finished"), 3)
            self.assertEqual(events.count("review_finished"), 3)
            self.assertEqual(events[-1], "task_completed")

        asyncio.run(scenario())

    def test_correction_limit_blocks_after_configured_rework_rounds(self) -> None:
        self._create_task(max_rounds=2)

        async def scenario() -> None:
            orchestrator = SimulationOrchestrator(store=self.store, runtime_root=self.root / "runtime")
            result = await orchestrator.run_correction_sequence(
                "task-1",
                writer_behaviors=[writer_behavior(1), writer_behavior(2), writer_behavior(3), writer_behavior(4)],
                reviewer_behaviors=[
                    [{"action": "review", "value": request_changes(1)}],
                    [{"action": "review", "value": request_changes(2)}],
                    [{"action": "review", "value": request_changes(3)}],
                    [{"action": "review", "value": request_changes(4)}],
                ],
                validation_cycles=1,
            )
            self.assertEqual(result.task.stage, WorkflowStage.BLOCKED.value)
            self.assertEqual(len(result.candidates), 3)
            self.assertEqual(len(result.validations), 3)
            self.assertEqual(len(result.reviews), 3)
            self.assertEqual(len(self.store.attempts("task-1", "writer")), 3)
            self.assertEqual([decision.action for decision in result.decisions], [GuardrailAction.REWORK, GuardrailAction.REWORK, GuardrailAction.BLOCKED])
            self.assertEqual(result.decisions[-1].correction_requests, 3)
            self.assertEqual(result.decisions[-1].max_correction_rounds, 2)

            rows = self.store._conn.execute(
                "SELECT generation,verdict FROM reviews WHERE task_id=? ORDER BY review_id",
                ("task-1",),
            ).fetchall()
            self.assertEqual([(row["generation"], row["verdict"]) for row in rows], [(1, "REQUEST_CHANGES"), (2, "REQUEST_CHANGES"), (3, "REQUEST_CHANGES")])
            events = self.store.events("task-1")
            event_types = [event.event_type for event in events]
            self.assertEqual(event_types.count("review_requested_changes"), 2)
            self.assertEqual(event_types.count("correction_limit_reached"), 1)
            self.assertNotIn("task_completed", event_types)
            limit_event = next(event for event in events if event.event_type == "correction_limit_reached")
            self.assertEqual(limit_event.payload["correction_requests"], 3)
            self.assertEqual(limit_event.payload["max_correction_rounds"], 2)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
