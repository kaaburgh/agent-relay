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


@dataclass(frozen=True)
class ReworkResult:
    task: TaskRow
    first_candidate: CandidateGeneration
    first_validation: ValidationEvidence
    first_review: ReviewEvidence
    final_candidate: CandidateGeneration
    final_validation: ValidationEvidence
    final_review: ReviewEvidence


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

    async def _validate(
        self,
        *,
        task_id: str,
        cwd: Path,
        candidate: CandidateGeneration,
        validation_cycles: int,
        validation_behavior: Mapping[str, Any] | None,
    ) -> ValidationEvidence:
        validation_result = await self.validator.run(
            task_id=task_id,
            cwd=cwd,
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
        return validation

    async def _review(
        self,
        *,
        task_id: str,
        repository: str,
        candidate: CandidateGeneration,
        behavior: Sequence[Mapping[str, Any]],
    ) -> tuple[ReviewEvidence, ReviewVerdict]:
        reviewer_worktree = self.git.create_reviewer_worktree(
            task_id=task_id,
            repository=repository,
            generation=candidate.generation,
            candidate_sha=candidate.candidate_sha,
        )
        reviewer_result = await self.reviewer.run(
            task_id=task_id,
            reviewer_worktree=reviewer_worktree,
            generation=candidate.generation,
            candidate_sha=candidate.candidate_sha,
            behavior=behavior,
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
        return review, reviewer_result.review.verdict

    @staticmethod
    def _validation_facts(candidate: CandidateGeneration) -> WorkflowFacts:
        return WorkflowFacts(
            validation_required=True,
            validation_success=True,
            validation_generation=candidate.generation,
            validation_candidate_sha=candidate.candidate_sha,
        )

    @staticmethod
    def _completion_facts(candidate: CandidateGeneration, verdict: ReviewVerdict) -> WorkflowFacts:
        return WorkflowFacts(
            validation_required=True,
            validation_success=True,
            validation_generation=candidate.generation,
            validation_candidate_sha=candidate.candidate_sha,
            review_output_valid=True,
            review_verdict=verdict,
            review_generation=candidate.generation,
            review_candidate_sha=candidate.candidate_sha,
        )

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
        validation = await self._validate(
            task_id=task_id,
            cwd=workspaces.writer,
            candidate=candidate,
            validation_cycles=validation_cycles,
            validation_behavior=validation_behavior,
        )
        self.workflow.transition(
            task_id, WorkflowStage.REVIEW, facts=self._validation_facts(candidate)
        )
        review, verdict = await self._review(
            task_id=task_id,
            repository=task.repository,
            candidate=candidate,
            behavior=reviewer_behavior,
        )
        if verdict not in {ReviewVerdict.APPROVE, ReviewVerdict.APPROVE_WITH_FOLLOWUPS}:
            raise OrchestrationError(f"happy path requires approval, got {verdict.value}")
        final = self.workflow.transition(
            task_id, WorkflowStage.DONE, facts=self._completion_facts(candidate, verdict)
        )
        return HappyPathResult(task=final, candidate=candidate, validation=validation, review=review)

    async def run_single_rework_cycle(
        self,
        task_id: str,
        *,
        initial_writer_behavior: Sequence[Mapping[str, Any]],
        initial_reviewer_behavior: Sequence[Mapping[str, Any]],
        rework_writer_behavior: Sequence[Mapping[str, Any]],
        final_reviewer_behavior: Sequence[Mapping[str, Any]],
        validation_cycles: int = 2,
    ) -> ReworkResult:
        task = self.store.get_task(task_id)
        if task.stage != WorkflowStage.READY.value:
            raise OrchestrationError(f"rework run requires READY task, found {task.stage}")
        self.workflow.transition(task_id, WorkflowStage.WORK)
        workspaces = self.git.create_writer_worktree(
            task_id=task_id, repository=task.repository, baseline_ref=task.baseline_ref
        )

        first_writer = await self.writer.run(
            task_id=task_id,
            writer_worktree=workspaces.writer,
            baseline_sha=workspaces.baseline_sha,
            behavior=initial_writer_behavior,
        )
        if first_writer.kind != WriterResultKind.SUCCESS or not first_writer.candidate_sha:
            raise OrchestrationError("initial writer did not produce candidate")
        first_candidate = record_candidate_generation(
            self.store,
            task_id=task_id,
            candidate_sha=first_writer.candidate_sha,
            writer_attempt_id=first_writer.attempt_id,
            expected_previous_generation=0,
        )
        self.workflow.transition(task_id, WorkflowStage.VALIDATE)
        first_validation = await self._validate(
            task_id=task_id,
            cwd=workspaces.writer,
            candidate=first_candidate,
            validation_cycles=validation_cycles,
            validation_behavior=None,
        )
        self.workflow.transition(
            task_id, WorkflowStage.REVIEW, facts=self._validation_facts(first_candidate)
        )
        first_review, first_verdict = await self._review(
            task_id=task_id,
            repository=task.repository,
            candidate=first_candidate,
            behavior=initial_reviewer_behavior,
        )
        if first_verdict != ReviewVerdict.REQUEST_CHANGES:
            raise OrchestrationError(
                f"single rework cycle requires REQUEST_CHANGES first, got {first_verdict.value}"
            )
        self.workflow.transition(
            task_id,
            WorkflowStage.REWORK,
            facts=WorkflowFacts(
                review_output_valid=True,
                review_verdict=first_verdict,
                review_generation=first_candidate.generation,
                review_candidate_sha=first_candidate.candidate_sha,
            ),
        )

        second_writer = await self.writer.run(
            task_id=task_id,
            writer_worktree=workspaces.writer,
            baseline_sha=first_candidate.candidate_sha,
            behavior=rework_writer_behavior,
            generation=first_candidate.generation,
            feedback=first_review.findings,
        )
        if second_writer.kind != WriterResultKind.SUCCESS or not second_writer.candidate_sha:
            raise OrchestrationError("rework writer did not produce a fresh committed candidate")
        final_candidate = record_candidate_generation(
            self.store,
            task_id=task_id,
            candidate_sha=second_writer.candidate_sha,
            writer_attempt_id=second_writer.attempt_id,
            expected_previous_generation=first_candidate.generation,
        )
        if final_candidate.generation != first_candidate.generation + 1:
            raise OrchestrationError("rework did not create the next candidate generation")

        self.workflow.transition(task_id, WorkflowStage.VALIDATE)
        final_validation = await self._validate(
            task_id=task_id,
            cwd=workspaces.writer,
            candidate=final_candidate,
            validation_cycles=validation_cycles,
            validation_behavior=None,
        )
        self.workflow.transition(
            task_id, WorkflowStage.REVIEW, facts=self._validation_facts(final_candidate)
        )
        final_review, final_verdict = await self._review(
            task_id=task_id,
            repository=task.repository,
            candidate=final_candidate,
            behavior=final_reviewer_behavior,
        )
        if final_verdict not in {ReviewVerdict.APPROVE, ReviewVerdict.APPROVE_WITH_FOLLOWUPS}:
            raise OrchestrationError(f"final re-review did not approve: {final_verdict.value}")
        final_task = self.workflow.transition(
            task_id,
            WorkflowStage.DONE,
            facts=self._completion_facts(final_candidate, final_verdict),
        )
        return ReworkResult(
            task=final_task,
            first_candidate=first_candidate,
            first_validation=first_validation,
            first_review=first_review,
            final_candidate=final_candidate,
            final_validation=final_validation,
            final_review=final_review,
        )
