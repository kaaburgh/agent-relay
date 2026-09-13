from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping, Sequence

from .artifacts import ArtifactManager, AttemptLayout
from .git_workspace import GitWorkspaceManager
from .store import Store
from .supervisor import ProcessResult, SubprocessSupervisor


class WriterResultKind(StrEnum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    MALFORMED = "MALFORMED"
    PROCESS_FAILURE = "PROCESS_FAILURE"


@dataclass(frozen=True)
class SimulatedWriterResult:
    kind: WriterResultKind
    attempt_id: int
    process_result: ProcessResult
    candidate_sha: str | None
    provider_result_path: Path
    payload: Mapping[str, Any] | None = None
    reason: str | None = None


@dataclass(frozen=True)
class SimulatedWriterInvocation:
    layout: AttemptLayout
    provider_result_path: Path
    process: Any
    baseline_sha: str
    writer_worktree: Path


class SimulatedWriterProvider:
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
        writer_worktree: str | Path,
        baseline_sha: str,
        behavior: Sequence[Mapping[str, Any]],
        generation: int | None = None,
        timeout_seconds: float | None = None,
    ) -> SimulatedWriterInvocation:
        worktree = Path(writer_worktree)
        layout = self.artifacts.create_attempt(
            task_id=task_id,
            kind="writer",
            generation=generation,
            inputs={"behavior": list(behavior), "baseline_sha": baseline_sha},
            command=["simulated-writer"],
        )
        script_path = layout.directory / "writer-behavior.json"
        provider_result_path = layout.directory / "provider-result.json"
        with script_path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(list(behavior), stream, indent=2, sort_keys=True)
            stream.write("\n")
        self.store.register_artifact(
            task_id=task_id,
            attempt_id=layout.attempt.attempt_id,
            kind="simulation_script",
            path=str(script_path.relative_to(self.artifacts.root)),
        )
        argv = [
            sys.executable,
            "-m",
            "agent_relay.simulated_writer_worker",
            "--script",
            str(script_path),
            "--worktree",
            str(worktree),
            "--result",
            str(provider_result_path),
        ]
        process = await self.supervisor.start(
            task_id=task_id,
            attempt_id=layout.attempt.attempt_id,
            argv=argv,
            cwd=worktree,
            stdout_path=layout.stdout_path,
            stderr_path=layout.stderr_path,
            timeout_seconds=timeout_seconds,
            heartbeat_interval=0.05,
            terminate_grace_seconds=0.1,
        )
        return SimulatedWriterInvocation(
            layout=layout,
            provider_result_path=provider_result_path,
            process=process,
            baseline_sha=baseline_sha,
            writer_worktree=worktree,
        )

    async def finish(self, invocation: SimulatedWriterInvocation) -> SimulatedWriterResult:
        process_result = await invocation.process.wait()
        attempt_id = invocation.layout.attempt.attempt_id
        payload: Mapping[str, Any] | None = None
        candidate_sha: str | None = None
        reason: str | None = None

        if invocation.provider_result_path.exists():
            self.store.register_artifact(
                task_id=invocation.layout.attempt.task_id,
                attempt_id=attempt_id,
                kind="provider_result",
                path=str(invocation.provider_result_path.relative_to(self.artifacts.root)),
            )

        if process_result.state not in {"SUCCEEDED", "FAILED"}:
            kind = WriterResultKind.PROCESS_FAILURE
            reason = process_result.state
        else:
            try:
                raw = invocation.provider_result_path.read_text(encoding="utf-8")
                parsed = json.loads(raw)
                if not isinstance(parsed, Mapping):
                    raise ValueError("provider result must be an object")
                payload = dict(parsed)
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                kind = WriterResultKind.MALFORMED
                reason = str(exc)
            else:
                status = payload.get("status")
                if status == "provider_unavailable":
                    kind = WriterResultKind.PROVIDER_UNAVAILABLE
                    reason = str(payload.get("reason", "provider unavailable"))
                elif process_result.returncode != 0:
                    kind = WriterResultKind.PROCESS_FAILURE
                    reason = f"process exit {process_result.returncode}"
                elif status == "failure":
                    kind = WriterResultKind.FAILURE
                    reason = str(payload.get("reason", "simulated writer failure"))
                elif status == "success":
                    try:
                        candidate_sha = self.git.detect_candidate(
                            invocation.writer_worktree, invocation.baseline_sha
                        )
                    except Exception as exc:
                        kind = WriterResultKind.FAILURE
                        reason = str(exc)
                    else:
                        kind = WriterResultKind.SUCCESS
                else:
                    kind = WriterResultKind.MALFORMED
                    reason = f"unsupported provider status: {status!r}"

        normalized = {
            "kind": kind.value,
            "candidate_sha": candidate_sha,
            "reason": reason,
            "provider_payload": dict(payload) if payload is not None else None,
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
        return SimulatedWriterResult(
            kind=kind,
            attempt_id=attempt_id,
            process_result=process_result,
            candidate_sha=candidate_sha,
            provider_result_path=invocation.provider_result_path,
            payload=payload,
            reason=reason,
        )

    async def run(self, **kwargs: Any) -> SimulatedWriterResult:
        invocation = await self.start(**kwargs)
        return await self.finish(invocation)
