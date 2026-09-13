from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping, Any

from .store import Store, TaskRow


class WorkflowStage(StrEnum):
    READY = "READY"
    WORK = "WORK"
    VALIDATE = "VALIDATE"
    REVIEW = "REVIEW"
    REWORK = "REWORK"
    WAITING_PROVIDER = "WAITING_PROVIDER"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    DONE = "DONE"


class ReviewVerdict(StrEnum):
    APPROVE = "APPROVE"
    APPROVE_WITH_FOLLOWUPS = "APPROVE_WITH_FOLLOWUPS"
    REQUEST_CHANGES = "REQUEST_CHANGES"
    BLOCKED_BY_MISSING_EVIDENCE = "BLOCKED_BY_MISSING_EVIDENCE"


class WorkflowError(RuntimeError):
    pass


class IllegalTransition(WorkflowError):
    pass


class InvariantViolation(WorkflowError):
    pass


@dataclass(frozen=True)
class WorkflowFacts:
    validation_required: bool = True
    validation_success: bool = False
    validation_generation: int | None = None
    validation_candidate_sha: str | None = None
    review_output_valid: bool = False
    review_verdict: ReviewVerdict | None = None
    review_generation: int | None = None
    review_candidate_sha: str | None = None


_ALLOWED: dict[WorkflowStage, frozenset[WorkflowStage]] = {
    WorkflowStage.READY: frozenset(
        {WorkflowStage.WORK, WorkflowStage.BLOCKED, WorkflowStage.FAILED, WorkflowStage.CANCELLED}
    ),
    WorkflowStage.WORK: frozenset(
        {
            WorkflowStage.VALIDATE,
            WorkflowStage.WAITING_PROVIDER,
            WorkflowStage.BLOCKED,
            WorkflowStage.FAILED,
            WorkflowStage.CANCELLED,
        }
    ),
    WorkflowStage.VALIDATE: frozenset(
        {
            WorkflowStage.REVIEW,
            WorkflowStage.WAITING_PROVIDER,
            WorkflowStage.BLOCKED,
            WorkflowStage.FAILED,
            WorkflowStage.CANCELLED,
        }
    ),
    WorkflowStage.REVIEW: frozenset(
        {
            WorkflowStage.DONE,
            WorkflowStage.REWORK,
            WorkflowStage.WAITING_PROVIDER,
            WorkflowStage.BLOCKED,
            WorkflowStage.FAILED,
            WorkflowStage.CANCELLED,
        }
    ),
    WorkflowStage.REWORK: frozenset(
        {
            WorkflowStage.VALIDATE,
            WorkflowStage.WAITING_PROVIDER,
            WorkflowStage.BLOCKED,
            WorkflowStage.FAILED,
            WorkflowStage.CANCELLED,
        }
    ),
    WorkflowStage.WAITING_PROVIDER: frozenset(
        {
            WorkflowStage.WORK,
            WorkflowStage.VALIDATE,
            WorkflowStage.REVIEW,
            WorkflowStage.REWORK,
            WorkflowStage.BLOCKED,
            WorkflowStage.FAILED,
            WorkflowStage.CANCELLED,
        }
    ),
    WorkflowStage.BLOCKED: frozenset(
        {
            WorkflowStage.WORK,
            WorkflowStage.VALIDATE,
            WorkflowStage.REVIEW,
            WorkflowStage.REWORK,
            WorkflowStage.FAILED,
            WorkflowStage.CANCELLED,
        }
    ),
    WorkflowStage.FAILED: frozenset(),
    WorkflowStage.CANCELLED: frozenset(),
    WorkflowStage.DONE: frozenset(),
}

_ACTIVE_STAGES = {
    WorkflowStage.WORK,
    WorkflowStage.VALIDATE,
    WorkflowStage.REVIEW,
    WorkflowStage.REWORK,
}

_DEFAULT_EVENT = {
    WorkflowStage.WORK: "stage_started",
    WorkflowStage.VALIDATE: "validation_started",
    WorkflowStage.REVIEW: "review_started",
    WorkflowStage.REWORK: "review_requested_changes",
    WorkflowStage.WAITING_PROVIDER: "provider_unavailable",
    WorkflowStage.BLOCKED: "task_blocked",
    WorkflowStage.FAILED: "task_failed",
    WorkflowStage.CANCELLED: "task_cancelled",
    WorkflowStage.DONE: "task_completed",
}


class WorkflowStateMachine:
    def __init__(self, store: Store) -> None:
        self.store = store

    def transition(
        self,
        task_id: str,
        target: WorkflowStage,
        *,
        facts: WorkflowFacts | None = None,
        event_type: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> TaskRow:
        current = self.store.get_task(task_id)
        try:
            source = WorkflowStage(current.stage)
        except ValueError as exc:
            raise InvariantViolation(f"unknown persisted workflow stage: {current.stage}") from exc

        if target not in _ALLOWED[source]:
            raise IllegalTransition(f"illegal workflow transition: {source} -> {target}")

        self._assert_target_invariants(current, target, facts)
        stage_attempt = self._next_stage_attempt(current, source, target)
        semantic_event = event_type or _DEFAULT_EVENT[target]
        event_payload = {"from": source.value, "to": target.value, **dict(payload or {})}
        return self.store.update_task_with_event(
            task_id=task_id,
            stage=target.value,
            stage_attempt=stage_attempt,
            event_type=semantic_event,
            event_payload=event_payload,
            expected_stage=source.value,
            expected_candidate_sha=current.current_candidate_sha,
            expected_generation=current.current_generation,
            enforce_expected_candidate=True,
        )

    @staticmethod
    def _next_stage_attempt(
        current: TaskRow, source: WorkflowStage, target: WorkflowStage
    ) -> int:
        if target not in _ACTIVE_STAGES:
            return current.stage_attempt
        if source in {WorkflowStage.WAITING_PROVIDER, WorkflowStage.BLOCKED}:
            return current.stage_attempt + 1
        return 1

    def _assert_target_invariants(
        self,
        task: TaskRow,
        target: WorkflowStage,
        facts: WorkflowFacts | None,
    ) -> None:
        if target == WorkflowStage.REVIEW:
            self._assert_review_ready(task, facts)
        elif target == WorkflowStage.DONE:
            self._assert_done_ready(task, facts)
        elif target == WorkflowStage.REWORK:
            self._assert_rework_ready(task, facts)

    @staticmethod
    def _assert_candidate(task: TaskRow) -> None:
        if task.current_generation <= 0 or not task.current_candidate_sha:
            raise InvariantViolation("a frozen candidate generation/SHA is required")

    def _assert_review_ready(self, task: TaskRow, facts: WorkflowFacts | None) -> None:
        self._assert_candidate(task)
        if facts is None:
            raise InvariantViolation("validation facts are required before review")
        if facts.validation_required:
            if not facts.validation_success:
                raise InvariantViolation("required validation has not succeeded")
            if facts.validation_generation != task.current_generation:
                raise InvariantViolation("validation generation does not match current candidate")
            if facts.validation_candidate_sha != task.current_candidate_sha:
                raise InvariantViolation("validation SHA does not match current candidate")

    def _assert_review_evidence(self, task: TaskRow, facts: WorkflowFacts | None) -> WorkflowFacts:
        self._assert_candidate(task)
        if facts is None or not facts.review_output_valid:
            raise InvariantViolation("valid structured reviewer output is required")
        if facts.review_verdict is None:
            raise InvariantViolation("reviewer verdict is missing")
        if facts.review_generation != task.current_generation:
            raise InvariantViolation("review generation does not match current candidate")
        if facts.review_candidate_sha != task.current_candidate_sha:
            raise InvariantViolation("review SHA does not match current candidate")
        return facts

    def _assert_done_ready(self, task: TaskRow, facts: WorkflowFacts | None) -> None:
        facts = self._assert_review_evidence(task, facts)
        if facts.review_verdict not in {
            ReviewVerdict.APPROVE,
            ReviewVerdict.APPROVE_WITH_FOLLOWUPS,
        }:
            raise InvariantViolation("review gate has not approved the current candidate")
        if facts.validation_required:
            if not facts.validation_success:
                raise InvariantViolation("required validation has not succeeded")
            if facts.validation_generation != task.current_generation:
                raise InvariantViolation("validation generation does not match current candidate")
            if facts.validation_candidate_sha != task.current_candidate_sha:
                raise InvariantViolation("validation SHA does not match current candidate")

    def _assert_rework_ready(self, task: TaskRow, facts: WorkflowFacts | None) -> None:
        facts = self._assert_review_evidence(task, facts)
        if facts.review_verdict != ReviewVerdict.REQUEST_CHANGES:
            raise InvariantViolation("REWORK requires a valid REQUEST_CHANGES verdict")
