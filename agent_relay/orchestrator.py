from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .artifacts import ArtifactManager
from .evidence import ReviewEvidence, ValidationEvidence, record_review, record_validation
from .git_workspace import CandidateGeneration, GitWorkspaceManager, record_candidate_generation
from .simulated_reviewer import ReviewerResultKind, SimulatedReviewerProvider
from .simulated_validator import SimulatedValidator, ValidationResultKind
from .simulated_writer import SimulatedWriterProvider, WriterResultKind
from .store import Store, TaskRow
from .supervisor import SubprocessSupervisor
from .workflow import ReviewVerdict, WorkflowFacts, WorkflowStage, WorkflowStateMachine


class OrchestrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class HappyPathResult:
    task: TaskRow
    candidate: CandidateGeneration
    validation: ValidationEvidence
    review: ReviewEvidence


class SimulationOrchestrator:
    """Deterministic coordinator for simulated providers; workers never own workflow state."""

    def __init__(self, *, store: Store, runtime_root: str | Path) -> None:
        self.store = store
        self.runtime_root = Path(runtime_root)
        self.artifacts = ArtifactManager(self.runtime_root / "artifacts", store)
        self.git = GitWorkspaceManager(self.runtime_root / "git")
        self.supervisor = SubprocessSupervisor(store)
        self.writer = SimulatedWriterProvider(
            store=store, artifacts=self.artifacts, supervisor=self.supervisor, git=self.git
        )
        self.validator = SimulatedValidator(
            store=store, artifacts=self.artifacts, supervisor=self.supervisor
        )
        self.reviewer = SimulatedReviewerProvider(
            store=store, artifacts=self.artifacts, supervisor=self.supervisor, git=self.git
        )
        self.workflow = WorkflowStateMachine(store)

    async def run_happy_path(
        self,
        task_id: str,
        *,
        writer_behavior: Sequence[Mapping[str, Any]],
        validation_cycles: int = 3,
        validation_behavior: Mapping[str, Any] | None = None,
        reviewer_behavior: Sequence[Mapping[str, Any]],
    ) -> HappyPathResult:
        task = self.store.get_task(task_id)
        if task.stage != WorkflowStage.READY.value:
            raise OrchestrationError(f"happy-path run requires READY task, found {task.stage}")

        self.workflow.transition(task_id, WorkflowStage.WORK)
        workspaces = self.git.create_writer_worktree(
            task_id=task_id,
            repository=task.repository,
            baseline_ref=task.baseline_ref,
        )
        writer_result = await self.writer.run(
            task_id=task_id,
            writer_worktree=workspaces.writer,
            baseline_sha=workspaces.baseline_sha,
            behavior=writer_behavior,
        )
        if writer_result.kind != WriterResultKind.SUCCESS or not writer_result.candidate_sha:
            raise OrchestrationError(
                f"writer did not produce a committed candidate: {writer_result.kind}: {writer_result.reason}"
            )
        candidate = record_candidate_generation(
            self.store,
            task_id=task_id,
            candidate_sha=writer_result.candidate_sha,
            writer_attempt_id=writer_result.attempt_id,
            expected_previous_generation=0,
        )

        self.workflow.transition(task_id, WorkflowStage.VALIDATE)
        validation_result = await self.validator.run(
            task_id=task_id,
            cwd=workspaces.writer,
            generation=candidate.generation,
            candidate_sha=candidate.candidate_sha,
            requested_cycles=validation_cycles,
            behavior=validation_behavior,
        )
        validation = record_validation(
            self.store,
            task_id=task_id,
            generation=candidate.generation,
            candidate_sha=candidate.candidate_sha,
            attempt_id=validation_result.attempt_id,
            status=validation_result.kind.value,
            result={
                "run_id": validation_result.run_id,
                "requested_cycles": validation_cycles,
                "completed_cycles": validation_result.completed_cycles,
                "reason": validation_result.reason,
            },
        )
        if validation_result.kind != ValidationResultKind.SUCCESS:
            raise OrchestrationError(
                f"validation did not pass: {validation_result.kind}: {validation_result.reason}"
            )
        validation_facts = WorkflowFacts(
            validation_required=True,
            validation_success=True,
            validation_generation=candidate.generation,
            validation_candidate_sha=candidate.candidate_sha,
        )
        self.workflow.transition(task_id, WorkflowStage.REVIEW, facts=validation_facts)

        reviewer_worktree = self.git.create_reviewer_worktree(
            task_id=task_id,
            repository=task.repository,
            generation=candidate.generation,
            candidate_sha=candidate.candidate_sha,
        )
        reviewer_result = await self.reviewer.run(
            task_id=task_id,
            reviewer_worktree=reviewer_worktree,
            generation=candidate.generation,
            candidate_sha=candidate.candidate_sha,
            behavior=reviewer_behavior,
        )
        if reviewer_result.kind != ReviewerResultKind.SUCCESS or reviewer_result.review is None:
            raise OrchestrationError(
                f"review did not produce valid structured output: {reviewer_result.kind}: {reviewer_result.reason}"
            )
        review = record_review(
            self.store,
            task_id=task_id,
            generation=candidate.generation,
            candidate_sha=candidate.candidate_sha,
            attempt_id=reviewer_result.attempt_id,
            run_id=reviewer_result.invocation_id,
            review=reviewer_result.review,
            raw_result_path=str(reviewer_result.raw_output_path.relative_to(self.artifacts.root)),
        )
        if reviewer_result.review.verdict not in {
            ReviewVerdict.APPROVE,
            ReviewVerdict.APPROVE_WITH_FOLLOWUPS,
        }:
            raise OrchestrationError(
                f"happy path requires approval, got {reviewer_result.review.verdict.value}"
            )
        done_facts = WorkflowFacts(
            validation_required=True,
            validation_success=True,
            validation_generation=candidate.generation,
            validation_candidate_sha=candidate.candidate_sha,
            review_output_valid=True,
            review_verdict=reviewer_result.review.verdict,
            review_generation=candidate.generation,
            review_candidate_sha=candidate.candidate_sha,
        )
        final = self.workflow.transition(task_id, WorkflowStage.DONE, facts=done_facts)
        return HappyPathResult(task=final, candidate=candidate, validation=validation, review=review)
