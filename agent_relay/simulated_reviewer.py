from __future__ import annotations

import json
import sys
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping, Sequence

from .artifacts import ArtifactManager, AttemptLayout
from .git_workspace import GitWorkspaceManager
from .review import ReviewParseError, StructuredReview, parse_review_output
from .store import Store
from .supervisor import ProcessResult, SubprocessSupervisor


class ReviewerResultKind(StrEnum):
    SUCCESS = "SUCCESS"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    FAILURE = "FAILURE"
    MALFORMED = "MALFORMED"
    PROCESS_FAILURE = "PROCESS_FAILURE"


@dataclass(frozen=True)
class SimulatedReviewerInvocation:
    layout: AttemptLayout
    invocation_id: str
    output_path: Path
    process: Any
    generation: int
    candidate_sha: str
    reviewer_worktree: Path


@dataclass(frozen=True)
class SimulatedReviewerResult:
    kind: ReviewerResultKind
    invocation_id: str
    attempt_id: int
    generation: int
    candidate_sha: str
    process_result: ProcessResult
    review: StructuredReview | None
    raw_output_path: Path
    reason: str | None = None


class SimulatedReviewerProvider:
    def __init__(
        self,
        *,
        store: Store,
        artifacts: ArtifactManager,
        supervisor: SubprocessSupervisor,
        git: GitWorkspaceManager,
    ) -> None:
        self.store = store
        self.artifacts = artifacts
        self.supervisor = supervisor
        self.git = git

    async def start(
        self,
        *,
        task_id: str,
        reviewer_worktree: str | Path,
        generation: int,
        candidate_sha: str,
        behavior: Sequence[Mapping[str, Any]],
        timeout_seconds: float | None = None,
    ) -> SimulatedReviewerInvocation:
        worktree = Path(reviewer_worktree)
        actual_sha = self.git.head_sha(worktree)
        if actual_sha != candidate_sha:
            raise ValueError(f"reviewer worktree SHA mismatch: expected {candidate_sha}, found {actual_sha}")
        invocation_id = str(uuid.uuid4())
        layout = self.artifacts.create_attempt(
            task_id=task_id,
            kind="reviewer",
            generation=generation,
            inputs={
                "behavior": list(behavior),
                "generation": generation,
                "candidate_sha": candidate_sha,
                "invocation_id": invocation_id,
            },
            command=["simulated-reviewer"],
        )
        script_path = layout.directory / "reviewer-behavior.json"
        output_path = layout.directory / "review-output.txt"
        with script_path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(list(behavior), stream, indent=2, sort_keys=True)
            stream.write("\n")
        self.store.register_artifact(
            task_id=task_id,
            attempt_id=layout.attempt.attempt_id,
            kind="simulation_script",
            path=str(script_path.relative_to(self.artifacts.root)),
        )
        process = await self.supervisor.start(
            task_id=task_id,
            attempt_id=layout.attempt.attempt_id,
            argv=[
                sys.executable,
                "-m",
                "agent_relay.simulated_reviewer_worker",
                "--script",
                str(script_path),
                "--output",
                str(output_path),
            ],
            cwd=worktree,
            stdout_path=layout.stdout_path,
            stderr_path=layout.stderr_path,
            timeout_seconds=timeout_seconds,
            heartbeat_interval=0.05,
            terminate_grace_seconds=0.1,
        )
        return SimulatedReviewerInvocation(
            layout=layout,
            invocation_id=invocation_id,
            output_path=output_path,
            process=process,
            generation=generation,
            candidate_sha=candidate_sha,
            reviewer_worktree=worktree,
        )

    async def finish(self, invocation: SimulatedReviewerInvocation) -> SimulatedReviewerResult:
        process_result = await invocation.process.wait()
        raw_exists = invocation.output_path.exists()
        review: StructuredReview | None = None
        reason: str | None = None

        if raw_exists:
            self.store.register_artifact(
                task_id=invocation.layout.attempt.task_id,
                attempt_id=invocation.layout.attempt.attempt_id,
                kind="review_output",
                path=str(invocation.output_path.relative_to(self.artifacts.root)),
                metadata={
                    "invocation_id": invocation.invocation_id,
                    "generation": invocation.generation,
                    "candidate_sha": invocation.candidate_sha,
                },
            )

        if process_result.state not in {"SUCCEEDED", "FAILED"}:
            kind = ReviewerResultKind.PROCESS_FAILURE
            reason = process_result.state
        elif not raw_exists:
            if process_result.returncode == 0:
                kind = ReviewerResultKind.MALFORMED
                reason = "reviewer exited successfully without output"
            else:
                kind = ReviewerResultKind.PROCESS_FAILURE
                reason = f"process exit {process_result.returncode} without output"
        else:
            raw = invocation.output_path.read_text(encoding="utf-8")
            try:
                marker = json.loads(raw)
            except json.JSONDecodeError:
                marker = None
            if isinstance(marker, Mapping) and marker.get("provider_unavailable") is True:
                kind = ReviewerResultKind.PROVIDER_UNAVAILABLE
                reason = str(marker.get("reason", "provider unavailable"))
            elif process_result.returncode != 0:
                kind = ReviewerResultKind.PROCESS_FAILURE
                reason = f"process exit {process_result.returncode}"
            elif isinstance(marker, Mapping) and marker.get("failure") is True:
                kind = ReviewerResultKind.FAILURE
                reason = str(marker.get("reason", "reviewer failure"))
            else:
                try:
                    review = parse_review_output(raw)
                except ReviewParseError as exc:
                    kind = ReviewerResultKind.MALFORMED
                    reason = str(exc)
                else:
                    kind = ReviewerResultKind.SUCCESS

        normalized = {
            "kind": kind.value,
            "invocation_id": invocation.invocation_id,
            "generation": invocation.generation,
            "candidate_sha": invocation.candidate_sha,
            "review": review.to_dict() if review is not None else None,
            "reason": reason,
            "process": {
                "state": process_result.state,
                "returncode": process_result.returncode,
                "pid": process_result.pid,
            },
        }
        self.artifacts.finalize_attempt(
            invocation.layout,
            status=kind.value,
            result=normalized,
            exit_status=process_result.returncode,
        )
        return SimulatedReviewerResult(
            kind=kind,
            invocation_id=invocation.invocation_id,
            attempt_id=invocation.layout.attempt.attempt_id,
            generation=invocation.generation,
            candidate_sha=invocation.candidate_sha,
            process_result=process_result,
            review=review,
            raw_output_path=invocation.output_path,
            reason=reason,
        )

    async def run(self, **kwargs: Any) -> SimulatedReviewerResult:
        invocation = await self.start(**kwargs)
        return await self.finish(invocation)
