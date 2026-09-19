from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping, Sequence

from .artifacts import ArtifactManager, AttemptLayout
from .git_workspace import GitWorkspaceManager
from .models import ProviderConfig
from .store import Store
from .supervisor import ProcessResult, SubprocessSupervisor


class CodexWriterResultKind(StrEnum):
    SUCCESS = "SUCCESS"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    FAILURE = "FAILURE"
    MALFORMED = "MALFORMED"
    PROCESS_FAILURE = "PROCESS_FAILURE"


@dataclass(frozen=True)
class CodexStreamSummary:
    thread_id: str | None
    handoff: str | None
    usage: Mapping[str, int]
    error_messages: tuple[str, ...]
    event_count: int


@dataclass(frozen=True)
class CodexWriterResult:
    kind: CodexWriterResultKind
    attempt_id: int
    process_result: ProcessResult
    candidate_sha: str | None
    thread_id: str | None
    handoff: str | None
    usage: Mapping[str, int]
    stdout_path: Path
    stderr_path: Path
    reason: str | None = None


@dataclass(frozen=True)
class CodexWriterInvocation:
    layout: AttemptLayout
    process: Any
    writer_worktree: Path
    baseline_sha: str
    argv: tuple[str, ...]


_USAGE_KEYS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
)

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


def parse_codex_jsonl(path: str | Path) -> CodexStreamSummary:
    source = Path(path)
    thread_id: str | None = None
    handoff: str | None = None
    usage = {key: 0 for key in _USAGE_KEYS}
    errors: list[str] = []
    event_count = 0
    saw_turn_completed = False
    for line_no, raw_line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid Codex JSONL at line {line_no}: {exc.msg}") from exc
        if not isinstance(event, Mapping) or not isinstance(event.get("type"), str):
            raise ValueError(f"invalid Codex event at line {line_no}")
        event_count += 1
        event_type = event["type"]
        if event_type == "thread.started":
            value = event.get("thread_id")
            if not isinstance(value, str) or not value:
                raise ValueError("thread.started event is missing thread_id")
            if thread_id is not None and thread_id != value:
                raise ValueError("Codex stream changed thread_id during one invocation")
            thread_id = value
        elif event_type == "item.completed":
            item = event.get("item")
            if isinstance(item, Mapping) and item.get("type") == "agent_message":
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    handoff = text
            if isinstance(item, Mapping) and item.get("type") == "error":
                message = item.get("message")
                if isinstance(message, str) and message:
                    errors.append(message)
        elif event_type == "turn.completed":
            raw_usage = event.get("usage")
            if not isinstance(raw_usage, Mapping):
                raise ValueError("turn.completed event is missing usage")
            for key in _USAGE_KEYS:
                value = raw_usage.get(key, 0)
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise ValueError(f"turn.completed usage.{key} must be a non-negative integer")
                usage[key] += value
            saw_turn_completed = True
        elif event_type == "turn.failed":
            error = event.get("error")
            message = error.get("message") if isinstance(error, Mapping) else None
            errors.append(str(message or "Codex turn failed"))
        elif event_type == "error":
            message = event.get("message")
            errors.append(str(message or "Codex stream error"))
    if event_count == 0:
        raise ValueError("Codex JSONL stream is empty")
    if thread_id is None:
        raise ValueError("Codex JSONL stream has no thread.started event")
    if not saw_turn_completed and not errors:
        raise ValueError("Codex JSONL stream has no terminal turn.completed event")
    return CodexStreamSummary(
        thread_id=thread_id,
        handoff=handoff,
        usage=usage,
        error_messages=tuple(errors),
        event_count=event_count,
    )


def _provider_unavailable(text: str) -> bool:
    normalized = text.lower()
    return any(marker in normalized for marker in _PROVIDER_UNAVAILABLE_MARKERS)


def _toml_string(value: str) -> str:
    return json.dumps(value)


class CodexWriterProvider:
    """Authenticated local Codex CLI writer. The CLI syntax is isolated here."""

    def __init__(
        self,
        *,
        store: Store,
        artifacts: ArtifactManager,
        supervisor: SubprocessSupervisor,
        git: GitWorkspaceManager,
        config: ProviderConfig,
    ) -> None:
        if config.provider != "codex":
            raise ValueError("CodexWriterProvider requires provider='codex'")
        self.store = store
        self.artifacts = artifacts
        self.supervisor = supervisor
        self.git = git
        self.config = config

    def build_argv(self, writer_worktree: str | Path) -> tuple[str, ...]:
        executable = self.config.executable or "codex"
        json_flag = self.config.options.get("json_flag", "--experimental-json")
        if json_flag not in {"--experimental-json", "--json"}:
            raise ValueError("config.writer.options.json_flag must be --experimental-json or --json")
        sandbox = self.config.options.get("sandbox", "workspace-write")
        if sandbox not in {"read-only", "workspace-write", "danger-full-access"}:
            raise ValueError("unsupported Codex sandbox mode")
        approval_policy = self.config.options.get("approval_policy", "never")
        if not isinstance(approval_policy, str) or not approval_policy:
            raise ValueError("Codex approval_policy must be a non-empty string")

        argv: list[str] = [executable, "exec", json_flag]
        if self.config.options.get("ephemeral") is True:
            argv.append("--ephemeral")
        if self.config.model:
            argv.extend(["--model", self.config.model])
        argv.extend(["--sandbox", sandbox, "--cd", str(Path(writer_worktree))])
        if self.config.reasoning_effort:
            argv.extend(
                ["--config", f"model_reasoning_effort={_toml_string(self.config.reasoning_effort)}"]
            )
        argv.extend(["--config", f"approval_policy={_toml_string(approval_policy)}"])
        network_access = self.config.options.get("network_access")
        if network_access is not None:
            if not isinstance(network_access, bool):
                raise ValueError("Codex network_access must be boolean")
            argv.extend(
                [
                    "--config",
                    f"sandbox_workspace_write.network_access={'true' if network_access else 'false'}",
                ]
            )
        return tuple(argv)

    async def start(
        self,
        *,
        task_id: str,
        writer_worktree: str | Path,
        baseline_sha: str,
        prompt: str,
        generation: int | None = None,
        timeout_seconds: float | None = None,
    ) -> CodexWriterInvocation:
        if not prompt.strip():
            raise ValueError("Codex writer prompt must not be empty")
        worktree = Path(writer_worktree)
        argv = self.build_argv(worktree)
        layout = self.artifacts.create_attempt(
            task_id=task_id,
            kind="writer",
            generation=generation,
            inputs={
                "provider": "codex",
                "model": self.config.model,
                "reasoning_effort": self.config.reasoning_effort,
                "baseline_sha": baseline_sha,
                "prompt": prompt,
            },
            command=list(argv),
        )
        protocol_path = layout.directory / "codex-protocol.jsonl"
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
            stdin_text=prompt,
            timeout_seconds=timeout_seconds,
            heartbeat_interval=5.0,
            terminate_grace_seconds=5.0,
        )
        return CodexWriterInvocation(
            layout=layout,
            process=process,
            writer_worktree=worktree,
            baseline_sha=baseline_sha,
            argv=argv,
        )

    async def finish(self, invocation: CodexWriterInvocation) -> CodexWriterResult:
        process_result = await invocation.process.wait()
        attempt_id = invocation.layout.attempt.attempt_id
        stdout = invocation.layout.stdout_path
        stderr = invocation.layout.stderr_path
        protocol = invocation.layout.directory / "codex-protocol.jsonl"
        self.store.register_artifact(
            task_id=invocation.layout.attempt.task_id,
            attempt_id=attempt_id,
            kind="codex_stdout_tail",
            path=str(stdout.relative_to(self.artifacts.root)),
        )
        if protocol.exists():
            self.store.register_artifact(
                task_id=invocation.layout.attempt.task_id,
                attempt_id=attempt_id,
                kind="codex_jsonl",
                path=str(protocol.relative_to(self.artifacts.root)),
            )
        self.store.register_artifact(
            task_id=invocation.layout.attempt.task_id,
            attempt_id=attempt_id,
            kind="codex_stderr",
            path=str(stderr.relative_to(self.artifacts.root)),
        )

        summary: CodexStreamSummary | None = None
        parse_error: str | None = None
        try:
            summary = parse_codex_jsonl(protocol)
        except (OSError, ValueError) as exc:
            parse_error = str(exc)
        stderr_text = stderr.read_text(encoding="utf-8", errors="replace") if stderr.exists() else ""
        stream_errors = "\n".join(summary.error_messages) if summary is not None else ""
        unavailable_text = "\n".join([stderr_text, stream_errors, parse_error or ""])

        candidate_sha: str | None = None
        reason: str | None = None
        if process_result.state not in {"SUCCEEDED", "FAILED"}:
            kind = CodexWriterResultKind.PROCESS_FAILURE
            reason = process_result.state
        elif process_result.returncode != 0:
            if _provider_unavailable(unavailable_text):
                kind = CodexWriterResultKind.PROVIDER_UNAVAILABLE
                reason = (stream_errors or stderr_text or f"Codex exited {process_result.returncode}").strip()
            else:
                kind = CodexWriterResultKind.PROCESS_FAILURE
                reason = (stream_errors or stderr_text or f"Codex exited {process_result.returncode}").strip()
        elif parse_error is not None:
            kind = CodexWriterResultKind.MALFORMED
            reason = parse_error
        elif summary is None:
            kind = CodexWriterResultKind.MALFORMED
            reason = "Codex stream could not be parsed"
        elif summary.error_messages:
            if _provider_unavailable("\n".join(summary.error_messages)):
                kind = CodexWriterResultKind.PROVIDER_UNAVAILABLE
            else:
                kind = CodexWriterResultKind.FAILURE
            reason = "; ".join(summary.error_messages)
        elif not summary.handoff:
            kind = CodexWriterResultKind.MALFORMED
            reason = "Codex completed without a final agent_message handoff"
        else:
            try:
                candidate_sha = self.git.detect_candidate(
                    invocation.writer_worktree, invocation.baseline_sha
                )
            except Exception as exc:
                kind = CodexWriterResultKind.FAILURE
                reason = str(exc)
            else:
                if candidate_sha is None:
                    kind = CodexWriterResultKind.FAILURE
                    reason = "Codex completed without a committed candidate"
                else:
                    kind = CodexWriterResultKind.SUCCESS

        normalized = {
            "kind": kind.value,
            "candidate_sha": candidate_sha,
            "thread_id": summary.thread_id if summary else None,
            "handoff": summary.handoff if summary else None,
            "usage": dict(summary.usage) if summary else {},
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
        return CodexWriterResult(
            kind=kind,
            attempt_id=attempt_id,
            process_result=process_result,
            candidate_sha=candidate_sha,
            thread_id=summary.thread_id if summary else None,
            handoff=summary.handoff if summary else None,
            usage=dict(summary.usage) if summary else {},
            stdout_path=stdout,
            stderr_path=stderr,
            reason=reason,
        )

    async def run(self, **kwargs: Any) -> CodexWriterResult:
        invocation = await self.start(**kwargs)
        return await self.finish(invocation)
