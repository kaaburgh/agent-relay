from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_relay.store import Store
from agent_relay.workflow import (
    IllegalTransition,
    InvariantViolation,
    ReviewVerdict,
    WorkflowFacts,
    WorkflowStage,
    WorkflowStateMachine,
)


class WorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = Path(tempfile.mkdtemp()) / "state.sqlite3"
        self.store = Store(self.path)
        self.store.create_task(
            task_id="task-1",
            repository="/tmp/repo",
            baseline_ref="main",
            task_spec={"validation": True},
        )
        self.machine = WorkflowStateMachine(self.store)

    def tearDown(self) -> None:
        self.store.close()

    def _reach_validating_candidate(self, sha: str = "a" * 40, generation: int = 1) -> None:
        self.machine.transition("task-1", WorkflowStage.WORK)
        self.machine.transition("task-1", WorkflowStage.VALIDATE)
        self.store.update_task_with_event(
            task_id="task-1",
            stage=WorkflowStage.VALIDATE.value,
            stage_attempt=1,
            candidate_sha=sha,
            generation=generation,
            event_type="candidate_commit_detected",
        )

    def _approved_facts(self, sha: str = "a" * 40, generation: int = 1) -> WorkflowFacts:
        return WorkflowFacts(
            validation_success=True,
            validation_generation=generation,
            validation_candidate_sha=sha,
            review_output_valid=True,
            review_verdict=ReviewVerdict.APPROVE,
            review_generation=generation,
            review_candidate_sha=sha,
        )

    def test_normal_transitions_persist_and_emit_semantic_events(self) -> None:
        task = self.machine.transition("task-1", WorkflowStage.WORK)
        self.assertEqual(task.stage, "WORK")
        self.assertEqual(task.stage_attempt, 1)
        task = self.machine.transition("task-1", WorkflowStage.VALIDATE)
        self.assertEqual(task.stage, "VALIDATE")
        self.assertEqual(
            [event.event_type for event in self.store.events("task-1")],
            ["task_created", "stage_started", "validation_started"],
        )

    def test_illegal_transition_does_not_mutate_state_or_history(self) -> None:
        before = self.store.events("task-1")
        with self.assertRaises(IllegalTransition):
            self.machine.transition("task-1", WorkflowStage.REVIEW)
        self.assertEqual(self.store.get_task("task-1").stage, "READY")
        self.assertEqual(self.store.events("task-1"), before)

    def test_review_requires_frozen_candidate(self) -> None:
        self.machine.transition("task-1", WorkflowStage.WORK)
        self.machine.transition("task-1", WorkflowStage.VALIDATE)
        with self.assertRaisesRegex(InvariantViolation, "frozen candidate"):
            self.machine.transition(
                "task-1",
                WorkflowStage.REVIEW,
                facts=WorkflowFacts(validation_required=False),
            )

    def test_review_requires_validation_for_exact_candidate_generation(self) -> None:
        self._reach_validating_candidate()
        stale = WorkflowFacts(
            validation_success=True,
            validation_generation=1,
            validation_candidate_sha="b" * 40,
        )
        with self.assertRaisesRegex(InvariantViolation, "validation SHA"):
            self.machine.transition("task-1", WorkflowStage.REVIEW, facts=stale)
        valid = WorkflowFacts(
            validation_success=True,
            validation_generation=1,
            validation_candidate_sha="a" * 40,
        )
        self.assertEqual(
            self.machine.transition("task-1", WorkflowStage.REVIEW, facts=valid).stage,
            "REVIEW",
        )

    def test_done_requires_valid_approval_for_same_candidate_and_validation(self) -> None:
        self._reach_validating_candidate()
        validation = WorkflowFacts(
            validation_success=True,
            validation_generation=1,
            validation_candidate_sha="a" * 40,
        )
        self.machine.transition("task-1", WorkflowStage.REVIEW, facts=validation)
        done = self.machine.transition("task-1", WorkflowStage.DONE, facts=self._approved_facts())
        self.assertEqual(done.stage, "DONE")
        self.assertEqual(self.store.events("task-1")[-1].event_type, "task_completed")

    def test_malformed_review_output_can_never_approve(self) -> None:
        self._reach_validating_candidate()
        validation = WorkflowFacts(
            validation_success=True,
            validation_generation=1,
            validation_candidate_sha="a" * 40,
        )
        self.machine.transition("task-1", WorkflowStage.REVIEW, facts=validation)
        malformed = WorkflowFacts(
            validation_success=True,
            validation_generation=1,
            validation_candidate_sha="a" * 40,
            review_output_valid=False,
            review_verdict=ReviewVerdict.APPROVE,
            review_generation=1,
            review_candidate_sha="a" * 40,
        )
        with self.assertRaisesRegex(InvariantViolation, "valid structured reviewer output"):
            self.machine.transition("task-1", WorkflowStage.DONE, facts=malformed)
        self.assertEqual(self.store.get_task("task-1").stage, "REVIEW")

    def test_stale_review_cannot_complete_newer_candidate(self) -> None:
        self._reach_validating_candidate(sha="b" * 40, generation=2)
        validation = WorkflowFacts(
            validation_success=True,
            validation_generation=2,
            validation_candidate_sha="b" * 40,
        )
        self.machine.transition("task-1", WorkflowStage.REVIEW, facts=validation)
        stale = WorkflowFacts(
            validation_success=True,
            validation_generation=2,
            validation_candidate_sha="b" * 40,
            review_output_valid=True,
            review_verdict=ReviewVerdict.APPROVE,
            review_generation=1,
            review_candidate_sha="a" * 40,
        )
        with self.assertRaisesRegex(InvariantViolation, "review generation"):
            self.machine.transition("task-1", WorkflowStage.DONE, facts=stale)

    def test_rework_requires_valid_request_changes_for_current_candidate(self) -> None:
        self._reach_validating_candidate()
        validation = WorkflowFacts(
            validation_success=True,
            validation_generation=1,
            validation_candidate_sha="a" * 40,
        )
        self.machine.transition("task-1", WorkflowStage.REVIEW, facts=validation)
        request_changes = WorkflowFacts(
            validation_success=True,
            validation_generation=1,
            validation_candidate_sha="a" * 40,
            review_output_valid=True,
            review_verdict=ReviewVerdict.REQUEST_CHANGES,
            review_generation=1,
            review_candidate_sha="a" * 40,
        )
        task = self.machine.transition("task-1", WorkflowStage.REWORK, facts=request_changes)
        self.assertEqual(task.stage, "REWORK")
        self.assertEqual(self.store.events("task-1")[-1].event_type, "review_requested_changes")

    def test_terminal_state_cannot_regress(self) -> None:
        self.machine.transition("task-1", WorkflowStage.CANCELLED)
        with self.assertRaises(IllegalTransition):
            self.machine.transition("task-1", WorkflowStage.WORK)

    def test_persisted_transition_survives_reopen(self) -> None:
        self.machine.transition("task-1", WorkflowStage.WORK)
        self.store.close()
        with Store(self.path) as reopened:
            self.assertEqual(reopened.get_task("task-1").stage, "WORK")
            self.assertEqual(reopened.events("task-1")[-1].event_type, "stage_started")
        self.store = Store(self.path)


if __name__ == "__main__":
    unittest.main()
