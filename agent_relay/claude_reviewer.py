from __future__ import annotations

import json
import subprocess
import sys
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping

from .artifacts import ArtifactManager, AttemptLayout
from .git_workspace import GitWorkspaceManager
from .models import ProviderConfig
from .review import ReviewParseError, StructuredReview, validate_review
from .review_package import ReviewPackage
from .store import Store
from .supervisor import ProcessResult, SubprocessSupervisor


class ClaudeReviewerResultKind(StrEnum):
    SUCCESS = "SUCCESS"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    FAILURE = "FAILURE"
    MALFORMED = "MALFORMED"
    PROCESS_FAILURE = "PROCESS_FAILURE"


@dataclass(frozen=True)
class ClaudeReviewerInvocation:
    layout: AttemptLayout
    invocation_id: str
    process: Any
    reviewer_worktree: Path
    generation: int
    candidate_sha: str
    before_head: str
    before_status: str
    argv: tuple[str, ...]


@dataclass(frozen=True)
class ClaudeReviewerResult:
    kind: ClaudeReviewerResultKind
    invocation_id: str
    attempt_id: int
    generation: int
    candidate_sha: str
    process_result: ProcessResult
    session_id: str | None
    review: StructuredReview | None
    usage: Mapping[str, Any]
    stdout_path: Path
    stderr_path: Path
    reason: str | None = None


_PROVIDER_UNAVAILABLE_MARKERS = (
    "rate limit",
    "rate_limit",
    "too many requests",
    "http 429",
    "status 429",
    "quota exceeded",
    "usage limit",
    "temporarily unavailable",
    "service unavailable",
    "overloaded",
    "capacity unavailable",
)

_REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "verdict": {
            "type": "string",
            "enum": [
                "APPROVE",
                "APPROVE_WITH_FOLLOWUPS",
                "REQUEST_CHANGES",
                "BLOCKED_BY_MISSING_EVIDENCE",
            ],
        },
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "severity": {
                        "type": "string",
                        "enum": ["CRITICAL", "HIGH", "MEDIUM", "LOW"],
                    },
                    "title": {"type": "string", "minLength": 1},
                    "file": {"type": ["string", "null"]},
                    "symbol": {"type": ["string", "null"]},
                    "problem": {"type": "string", "minLength": 1},
                    "failure_scenario": {"type": ["string", "null"]},
                    "required_action": {"type": "string", "minLength": 1},
                },
                "required": [
                    "severity",
                    "title",
                    "file",
                    "symbol",
                    "problem",
                    "failure_scenario",
                    "required_action",
                ],
            },
        },
        "summary": {"type": "string", "minLength": 1},
    },
    "required": ["verdict", "findings", "summary"],
}


def _git_text(worktree: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(worktree), *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"git command failed: {args!r}: {completed.stderr.strip()}")
    return completed.stdout


def _provider_unavailable(text: str) -> bool:
    normalized = text.lower()
    return any(marker in normalized for marker in _PROVIDER_UNAVAILABLE_MARKERS)


def _parse_outer_json(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid Claude JSON output: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ValueError("Claude JSON output root must be an object")
    return value


class ClaudeReviewerProvider:
    """Fresh, plan-mode Claude Code reviewer bound to one frozen candidate."""

    def __init__(
        self,
        *,
        store: Store,
        artifacts: ArtifactManager,
        supervisor: SubprocessSupervisor,
        git: GitWorkspaceManager,
        config: ProviderConfig,
    ) -> None:
        if config.provider != "claude":
            raise ValueError("ClaudeReviewerProvider requires provider='claude'")
        self.store = store
        self.artifacts = artifacts
        self.supervisor = supervisor
        self.git = git
        self.config = config

    def build_argv(self) -> tuple[str, ...]:
        executable = self.config.executable or "claude"
        tools = self.config.options.get("tools", "Read,Grep,Glob")
        if not isinstance(tools, str) or not tools.strip():
            raise ValueError("Claude reviewer tools must be a non-empty comma-separated string")
        if any(tool.strip() in {"Edit", "Write", "NotebookEdit"} for tool in tools.split(",")):
            raise ValueError("Claude reviewer tools must not include write tools")
        permission_mode = self.config.options.get("permission_mode", "plan")
        if permission_mode != "plan":
            raise ValueError("Claude reviewer requires permission_mode='plan'")

        argv: list[str] = [
            executable,
            "-p",
            "Perform the independent review using the review package supplied on stdin.",
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(_REVIEW_SCHEMA, sort_keys=True, separators=(",", ":")),
            "--permission-mode",
            "plan",
            "--tools",
            tools,
            "--no-session-persistence",
            "--bare",
            "--no-chrome",
        ]
        if self.config.model:
            argv.extend(["--model", self.config.model])
        max_turns = self.config.options.get("max_turns")
        if max_turns is not None:
            if isinstance(max_turns, bool) or not isinstance(max_turns, int) or max_turns <= 0:
                raise ValueError("Claude reviewer max_turns must be a positive integer")
            argv.extend(["--max-turns", str(max_turns)])
        max_budget = self.config.options.get("max_budget_usd")
        if max_budget is not None:
            if isinstance(max_budget, bool) or not isinstance(max_budget, (int, float)) or max_budget <= 0:
                raise ValueError("Claude reviewer max_budget_usd must be a positive number")
            argv.extend(["--max-budget-usd", str(float(max_budget))])
        return tuple(argv)

    async def start(
        self,
        *,
        task_id: str,
        reviewer_worktree: str | Path,
        generation: int,
        candidate_sha: str,
        package: ReviewPackage,
        timeout_seconds: float | None = None,
    ) -> ClaudeReviewerInvocation:
        worktree = Path(reviewer_worktree)
        before_head = self.git.head_sha(worktree)
        if before_head != candidate_sha:
            raise ValueError(
                f"reviewer worktree SHA mismatch: expected {candidate_sha}, found {before_head}"
            )
        if package.task_id != task_id or package.generation != generation:
            raise ValueError("review package task/generation does not match invocation")
        if package.candidate_sha != candidate_sha:
            raise ValueError("review package candidate SHA does not match invocation")
        before_status = _git_text(worktree, "status", "--porcelain=v1", "--untracked-files=all")
        if before_status.strip():
            raise ValueError("reviewer worktree must be clean before review")

        invocation_id = str(uuid.uuid4())
        argv = self.build_argv()
        layout = self.artifacts.create_attempt(
            task_id=task_id,
            kind="reviewer",
            generation=generation,
            candidate_sha=candidate_sha,
            inputs={
                "provider": "claude",
                "model": self.config.model,
                "invocation_id": invocation_id,
                "review_package": package.to_dict(),
            },
            command=list(argv),
        )
        protocol_path = layout.directory / "claude-protocol.json"
        launch_argv = (
            sys.executable,
            "-m",
            "agent_relay.protocol_capture",
            "--output",
            str(protocol_path),
            "--",
            *argv,
        )
        process = await self.supervisor.start(
            task_id=task_id,
            attempt_id=layout.attempt.attempt_id,
            argv=launch_argv,
            cwd=worktree,
            stdout_path=layout.stdout_path,
            stderr_path=layout.stderr_path,
            stdin_text=package.prompt_payload(),
            timeout_seconds=timeout_seconds,
            heartbeat_interval=5.0,
            terminate_grace_seconds=5.0,
        )
        return ClaudeReviewerInvocation(
            layout=layout,
            invocation_id=invocation_id,
            process=process,
            reviewer_worktree=worktree,
            generation=generation,
            candidate_sha=candidate_sha,
            before_head=before_head,
            before_status=before_status,
            argv=argv,
        )

    async def finish(self, invocation: ClaudeReviewerInvocation) -> ClaudeReviewerResult:
        process_result = await invocation.process.wait()
        attempt_id = invocation.layout.attempt.attempt_id
        stdout = invocation.layout.stdout_path
        stderr = invocation.layout.stderr_path
        protocol = invocation.layout.directory / "claude-protocol.json"
        self.store.register_artifact(
            task_id=invocation.layout.attempt.task_id,
            attempt_id=attempt_id,
            kind="claude_stdout_tail",
            path=str(stdout.relative_to(self.artifacts.root)),
        )
        if protocol.exists():
            self.store.register_artifact(
                task_id=invocation.layout.attempt.task_id,
                attempt_id=attempt_id,
                kind="claude_json",
                path=str(protocol.relative_to(self.artifacts.root)),
            )
        self.store.register_artifact(
            task_id=invocation.layout.attempt.task_id,
            attempt_id=attempt_id,
            kind="claude_stderr",
            path=str(stderr.relative_to(self.artifacts.root)),
        )

        payload: Mapping[str, Any] | None = None
        parse_error: str | None = None
        try:
            payload = _parse_outer_json(protocol)
        except ValueError as exc:
            parse_error = str(exc)
        stderr_text = stderr.read_text(encoding="utf-8", errors="replace") if stderr.exists() else ""
        payload_text = json.dumps(payload, ensure_ascii=False) if payload is not None else ""
        unavailable_text = "\n".join([stderr_text, payload_text, parse_error or ""])

        session_id: str | None = None
        review: StructuredReview | None = None
        usage: Mapping[str, Any] = {}
        reason: str | None = None
        if payload is not None:
            raw_session = payload.get("session_id")
            if isinstance(raw_session, str) and raw_session:
                session_id = raw_session
            raw_usage = payload.get("usage")
            if isinstance(raw_usage, Mapping):
                usage = dict(raw_usage)

        if process_result.state not in {"SUCCEEDED", "FAILED"}:
            kind = ClaudeReviewerResultKind.PROCESS_FAILURE
            reason = process_result.state
        elif process_result.returncode != 0:
            if _provider_unavailable(unavailable_text):
                kind = ClaudeReviewerResultKind.PROVIDER_UNAVAILABLE
            else:
                kind = ClaudeReviewerResultKind.PROCESS_FAILURE
            reason = (stderr_text or payload_text or f"Claude exited {process_result.returncode}").strip()
        elif parse_error is not None:
            kind = ClaudeReviewerResultKind.MALFORMED
            reason = parse_error
        elif payload is None:
            kind = ClaudeReviewerResultKind.MALFORMED
            reason = "Claude output could not be parsed"
        elif payload.get("is_error") is True:
            message = str(payload.get("result") or payload.get("error") or "Claude result reported an error")
            kind = (
                ClaudeReviewerResultKind.PROVIDER_UNAVAILABLE
                if _provider_unavailable(message)
                else ClaudeReviewerResultKind.FAILURE
            )
            reason = message
        elif session_id is None:
            kind = ClaudeReviewerResultKind.MALFORMED
            reason = "Claude JSON output is missing session_id"
        else:
            try:
                review = validate_review(payload.get("structured_output"))
            except ReviewParseError as exc:
                kind = ClaudeReviewerResultKind.MALFORMED
                reason = str(exc)
            else:
                kind = ClaudeReviewerResultKind.SUCCESS

        after_head = self.git.head_sha(invocation.reviewer_worktree)
        after_status = _git_text(
            invocation.reviewer_worktree,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        )
        worktree_changed = (
            after_head != invocation.before_head
            or after_status != invocation.before_status
        )
        if worktree_changed:
            kind = ClaudeReviewerResultKind.FAILURE
            reason = "Claude reviewer modified its supposedly read-only worktree"
            review = None

        normalized = {
            "kind": kind.value,
            "invocation_id": invocation.invocation_id,
            "generation": invocation.generation,
            "candidate_sha": invocation.candidate_sha,
            "session_id": session_id,
            "review": review.to_dict() if review is not None else None,
            "usage": dict(usage),
            "worktree_unchanged": not worktree_changed,
            "reason": reason,
            "process": {
                "state": process_result.state,
                "returncode": process_result.returncode,
                "pid": process_result.pid,
                "process_group_id": process_result.process_group_id,
                "started_at": process_result.started_at,
                "ended_at": process_result.ended_at,
            },
        }
        self.artifacts.finalize_attempt(
            invocation.layout,
            status=kind.value,
            result=normalized,
            exit_status=process_result.returncode,
        )
        return ClaudeReviewerResult(
            kind=kind,
            invocation_id=invocation.invocation_id,
            attempt_id=attempt_id,
            generation=invocation.generation,
            candidate_sha=invocation.candidate_sha,
            process_result=process_result,
            session_id=session_id,
            review=review,
            usage=dict(usage),
            stdout_path=stdout,
            stderr_path=stderr,
            reason=reason,
        )

    async def run(self, **kwargs: Any) -> ClaudeReviewerResult:
        invocation = await self.start(**kwargs)
        return await self.finish(invocation)
