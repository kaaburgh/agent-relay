from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .evidence import ReviewEvidence, latest_validation
from .store import Store, StoreError, TaskRow
from .workflow import ReviewVerdict, WorkflowFacts, WorkflowStage, WorkflowStateMachine


class GuardrailAction(StrEnum):
    DONE = "DONE"
    REWORK = "REWORK"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class GuardrailDecision:
    action: GuardrailAction
    task: TaskRow
    correction_requests: int
    max_correction_rounds: int
    reason: str | None = None


def max_correction_rounds(task: TaskRow) -> int:
    value = task.task_json.get("max_correction_rounds", 3)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise StoreError("persisted task has invalid max_correction_rounds")
    return value


def correction_request_count(store: Store, task_id: str) -> int:
    row = store._conn.execute(
        "SELECT COUNT(*) AS count FROM reviews WHERE task_id=? AND verdict='REQUEST_CHANGES'",
        (task_id,),
    ).fetchone()
    return int(row["count"])


def apply_review_guardrail(
    store: Store,
    workflow: WorkflowStateMachine,
    *,
    task_id: str,
    review: ReviewEvidence,
) -> GuardrailDecision:
    task = store.get_task(task_id)
    if task.stage != WorkflowStage.REVIEW.value:
        raise StoreError(f"review guardrail requires REVIEW stage, found {task.stage}")
    if task.current_generation != review.generation or task.current_candidate_sha != review.candidate_sha:
        raise StoreError("review guardrail received stale candidate evidence")

    try:
        verdict = ReviewVerdict(review.verdict)
    except ValueError as exc:
        raise StoreError(f"persisted review has unsupported verdict: {review.verdict!r}") from exc

    corrections = correction_request_count(store, task_id)
    limit = max_correction_rounds(task)
    validation = latest_validation(store, task_id, review.generation)
    validation_ok = (
        validation is not None
        and validation.status == "SUCCESS"
        and validation.candidate_sha == review.candidate_sha
    )

    if verdict in {ReviewVerdict.APPROVE, ReviewVerdict.APPROVE_WITH_FOLLOWUPS}:
        if not validation_ok:
            raise StoreError("approval cannot complete without successful current-candidate validation")
        final = workflow.transition(
            task_id,
            WorkflowStage.DONE,
            facts=WorkflowFacts(
                validation_required=True,
                validation_success=True,
                validation_generation=review.generation,
                validation_candidate_sha=review.candidate_sha,
                review_output_valid=True,
                review_verdict=verdict,
                review_generation=review.generation,
                review_candidate_sha=review.candidate_sha,
            ),
        )
        return GuardrailDecision(GuardrailAction.DONE, final, corrections, limit)

    if verdict == ReviewVerdict.BLOCKED_BY_MISSING_EVIDENCE:
        blocked = workflow.transition(
            task_id,
            WorkflowStage.BLOCKED,
            event_type="review_missing_evidence",
            payload={
                "review_id": review.review_id,
                "candidate_sha": review.candidate_sha,
                "generation": review.generation,
            },
        )
        return GuardrailDecision(
            GuardrailAction.BLOCKED,
            blocked,
            corrections,
            limit,
            "reviewer reported missing evidence",
        )

    if verdict != ReviewVerdict.REQUEST_CHANGES:
        raise StoreError(f"unsupported review verdict policy: {verdict.value}")

    if corrections > limit:
        blocked = workflow.transition(
            task_id,
            WorkflowStage.BLOCKED,
            event_type="correction_limit_reached",
            payload={
                "review_id": review.review_id,
                "candidate_sha": review.candidate_sha,
                "generation": review.generation,
                "correction_requests": corrections,
                "max_correction_rounds": limit,
            },
        )
        return GuardrailDecision(
            GuardrailAction.BLOCKED,
            blocked,
            corrections,
            limit,
            f"correction limit reached: {corrections} requests for {limit} allowed rounds",
        )

    rework = workflow.transition(
        task_id,
        WorkflowStage.REWORK,
        facts=WorkflowFacts(
            review_output_valid=True,
            review_verdict=verdict,
            review_generation=review.generation,
            review_candidate_sha=review.candidate_sha,
        ),
        payload={
            "review_id": review.review_id,
            "correction_round": corrections,
            "max_correction_rounds": limit,
        },
    )
    return GuardrailDecision(GuardrailAction.REWORK, rework, corrections, limit)
