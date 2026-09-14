from __future__ import annotations

import csv
import json
import sys
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping, Sequence

from .artifacts import ArtifactManager, AttemptLayout
from .store import Store
from .supervisor import ProcessResult, SubprocessSupervisor
from .watchdog import wait_with_file_progress_watchdog


class ShadPS4ValidationResultKind(StrEnum):
    SUCCESS = "SUCCESS"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    INCOMPLETE_EVIDENCE = "INCOMPLETE_EVIDENCE"
    PROCESS_FAILURE = "PROCESS_FAILURE"


@dataclass(frozen=True)
class ShadPS4ValidationInvocation:
    layout: AttemptLayout
    run_id: str
    evidence_dir: Path
    exit_checkpoint_path: Path
    process: Any
    generation: int
    candidate_sha: str
    requested_cycles: int
    argv: tuple[str, ...]


@dataclass(frozen=True)
class ShadPS4ValidationResult:
    kind: ShadPS4ValidationResultKind
    run_id: str
    attempt_id: int
    generation: int
    candidate_sha: str
    requested_cycles: int
    completed_cycles: int
    process_result: ProcessResult
    runner_state: str | None
    cycle_records: tuple[Mapping[str, Any], ...]
    evidence_dir: Path
    status_path: Path | None
    cycles_path: Path | None
    summary_path: Path | None
    reason: str | None = None


_ALLOWED_PLACEHOLDERS = {"run_id", "requested_cycles", "evidence_dir"}
_SUCCESS_STATES = {"completed", "complete", "success", "succeeded", "done", "passed"}
_FAILURE_STATES = {"failed", "failure", "error", "crashed", "cancelled", "canceled", "timed_out", "timeout", "stalled"}
_SUCCESS_CYCLE_STATUSES = {"ok", "success", "succeeded", "passed", "complete", "completed"}
_FAILURE_CYCLE_STATUSES = {"failed", "failure", "error", "crashed", "timeout", "timed_out", "stalled"}
_CYCLE_ID_FIELDS = ("cycle", "cycle_id", "index")


def _validate_argv_template(template: Sequence[str]) -> None:
    if not template or any(not isinstance(item, str) or not item for item in template):
        raise ValueError("shadPS4 runner argv template must contain non-empty strings")
    for item in template:
        probe = item
        for name in _ALLOWED_PLACEHOLDERS:
            probe = probe.replace("{" + name + "}", "")
        if "{" in probe or "}" in probe:
            raise ValueError(f"unsupported shadPS4 argv placeholder in {item!r}")


def _render_argv(
    template: Sequence[str],
    *,
    run_id: str,
    requested_cycles: int,
    evidence_dir: Path,
) -> tuple[str, ...]:
    _validate_argv_template(template)
    values = {
        "run_id": run_id,
        "requested_cycles": str(requested_cycles),
        "evidence_dir": str(evidence_dir),
    }
    rendered: list[str] = []
    for item in template:
        for name, value in values.items():
            item = item.replace("{" + name + "}", value)
        rendered.append(item)
    return tuple(rendered)


def _load_status(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"runner-status.json is unreadable: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ValueError("runner-status.json root must be an object")
    return value


def _load_cycles(path: Path) -> tuple[Mapping[str, Any], ...]:
    if path.suffix.lower() == ".csv":
        try:
            with path.open("r", encoding="utf-8", newline="") as stream:
                return tuple(dict(row) for row in csv.DictReader(stream))
        except OSError as exc:
            raise ValueError(f"cycles CSV is unreadable: {exc}") from exc
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cycles JSON is unreadable: {exc}") from exc
    if isinstance(value, Mapping):
        value = value.get("cycles")
    if not isinstance(value, list) or any(not isinstance(item, Mapping) for item in value):
        raise ValueError("cycles JSON must be a list of objects or an object with a cycles list")
    return tuple(dict(item) for item in value)


def _integer_field(value: Mapping[str, Any], *names: str) -> int | None:
    for name in names:
        raw = value.get(name)
        if raw is None:
            continue
        if isinstance(raw, bool):
            return None
        if isinstance(raw, int):
            return raw
        if isinstance(raw, str):
            try:
                return int(raw)
            except ValueError:
                return None
        return None
    return None


def _string_field(value: Mapping[str, Any], *names: str) -> str | None:
    for name in names:
        raw = value.get(name)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return None


def _cycle_sequence_error(records: Sequence[Mapping[str, Any]]) -> str | None:
    has_identifier = [any(name in record for name in _CYCLE_ID_FIELDS) for record in records]
    if not any(has_identifier):
        return None
    if not all(has_identifier):
        return "cycle identifiers are partially missing"
    sequence = [_integer_field(record, *_CYCLE_ID_FIELDS) for record in records]
    if any(value is None for value in sequence):
        return "cycle identifiers are invalid"
    expected = list(range(1, len(records) + 1))
    if sequence != expected:
        return "cycle sequence is incomplete or out of order"
    return None


def _cycle_failure(records: Sequence[Mapping[str, Any]]) -> str | None:
    for index, record in enumerate(records, start=1):
        raw = _string_field(record, "status", "result", "outcome")
        if raw is None:
            return f"cycle {index} is missing status/result/outcome"
        normalized = raw.lower()
        if normalized in _FAILURE_CYCLE_STATUSES:
            return f"cycle {index} reports failure status {raw!r}"
        if normalized not in _SUCCESS_CYCLE_STATUSES:
            return f"cycle {index} has unsupported status {raw!r}"
    return None


def _explicit_validation_failure(evidence_error: str) -> bool:
    if evidence_error.startswith("runner state reports failure:"):
        return True
    return evidence_error.startswith("cycle ") and " reports failure status " in evidence_error


def _load_exit_checkpoint(path: Path, run_id: str) -> tuple[int | None, str | None]:
    if not path.exists():
        return None, "process-exit.json is missing"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"process-exit.json is unreadable: {exc}"
    if not isinstance(value, Mapping):
        return None, "process-exit.json root must be an object"
    if _string_field(value, "run_id", "runId") != run_id:
        return None, "process-exit.json run_id mismatch"
    status = _integer_field(value, "exit_status", "exitStatus", "returncode")
    if status is None:
        return None, "process-exit.json exit status is missing or invalid"
    return status, None


class ShadPS4BloodborneValidator:
    """External adapter for the existing Bloodborne/shadPS4 benchmark harness."""

    def __init__(
        self,
        *,
        store: Store,
        artifacts: ArtifactManager,
        supervisor: SubprocessSupervisor,
    ) -> None:
        self.store = store
        self.artifacts = artifacts
        self.supervisor = supervisor

    async def start(
        self,
        *,
        task_id: str,
        cwd: str | Path,
        generation: int,
        candidate_sha: str,
        requested_cycles: int,
        argv_template: Sequence[str],
        env_additions: Mapping[str, str] | None = None,
        timeout_seconds: float | None = None,
        run_id: str | None = None,
    ) -> ShadPS4ValidationInvocation:
        if requested_cycles <= 0:
            raise ValueError("requested_cycles must be positive")
        if generation <= 0:
            raise ValueError("generation must be positive")
        if not candidate_sha.strip():
            raise ValueError("candidate_sha must not be empty")
        _validate_argv_template(argv_template)
        resolved_run_id = run_id or str(uuid.uuid4())
        layout = self.artifacts.create_attempt(
            task_id=task_id,
            kind="validation",
            generation=generation,
            candidate_sha=candidate_sha,
            inputs={
                "adapter": "shadps4-bloodborne",
                "run_id": resolved_run_id,
                "requested_cycles": requested_cycles,
                "generation": generation,
                "candidate_sha": candidate_sha,
            },
            command=list(argv_template),
        )
        evidence_dir = layout.directory / "evidence" / resolved_run_id
        evidence_dir.mkdir(parents=True, exist_ok=False)
        argv = _render_argv(
            argv_template,
            run_id=resolved_run_id,
            requested_cycles=requested_cycles,
            evidence_dir=evidence_dir,
        )
        rendered_path = layout.directory / "rendered-command.json"
        rendered_path.write_text(json.dumps(list(argv), indent=2) + "\n", encoding="utf-8")
        self.store.register_artifact(
            task_id=task_id,
            attempt_id=layout.attempt.attempt_id,
            kind="rendered_command",
            path=str(rendered_path.relative_to(self.artifacts.root)),
        )
        exit_checkpoint_path = layout.directory / "process-exit.json"
        wrapper_argv = (
            sys.executable,
            "-m",
            "agent_relay.exit_checkpoint_worker",
            "--checkpoint",
            str(exit_checkpoint_path),
            "--run-id",
            resolved_run_id,
            "--",
            *argv,
        )
        process = await self.supervisor.start(
            task_id=task_id,
            attempt_id=layout.attempt.attempt_id,
            argv=wrapper_argv,
            cwd=Path(cwd),
            stdout_path=layout.stdout_path,
            stderr_path=layout.stderr_path,
            env_additions=env_additions,
            timeout_seconds=timeout_seconds,
            heartbeat_interval=5.0,
            terminate_grace_seconds=5.0,
        )
        return ShadPS4ValidationInvocation(
            layout=layout,
            run_id=resolved_run_id,
            evidence_dir=evidence_dir,
            exit_checkpoint_path=exit_checkpoint_path,
            process=process,
            generation=generation,
            candidate_sha=candidate_sha,
            requested_cycles=requested_cycles,
            argv=argv,
        )

    def _read_evidence(
        self, invocation: ShadPS4ValidationInvocation
    ) -> tuple[
        int,
        str | None,
        tuple[Mapping[str, Any], ...],
        Path | None,
        Path | None,
        Path | None,
        str | None,
    ]:
        status_path = invocation.evidence_dir / "runner-status.json"
        summary_path = invocation.evidence_dir / "summary.md"
        csv_path = invocation.evidence_dir / "cycles.csv"
        json_path = invocation.evidence_dir / "cycles.json"
        cycles_path = csv_path if csv_path.exists() else json_path if json_path.exists() else None

        for kind, path in (
            ("runner_status", status_path if status_path.exists() else None),
            ("cycles", cycles_path),
            ("summary", summary_path if summary_path.exists() else None),
        ):
            if path is not None:
                existing = self.store._conn.execute(
                    "SELECT 1 FROM artifacts WHERE task_id=? AND attempt_id=? AND kind=? AND path=?",
                    (
                        invocation.layout.attempt.task_id,
                        invocation.layout.attempt.attempt_id,
                        kind,
                        str(path.relative_to(self.artifacts.root)),
                    ),
                ).fetchone()
                if existing is None:
                    self.store.register_artifact(
                        task_id=invocation.layout.attempt.task_id,
                        attempt_id=invocation.layout.attempt.attempt_id,
                        kind=kind,
                        path=str(path.relative_to(self.artifacts.root)),
                        metadata={
                            "run_id": invocation.run_id,
                            "generation": invocation.generation,
                            "candidate_sha": invocation.candidate_sha,
                        },
                    )

        if not status_path.exists():
            return 0, None, (), None, cycles_path, summary_path if summary_path.exists() else None, "runner-status.json is missing"
        try:
            status = _load_status(status_path)
        except ValueError as exc:
            return 0, None, (), status_path, cycles_path, summary_path if summary_path.exists() else None, str(exc)

        status_run_id = _string_field(status, "run_id", "runId")
        if status_run_id != invocation.run_id:
            return 0, _string_field(status, "state", "status"), (), status_path, cycles_path, summary_path if summary_path.exists() else None, "runner status run_id mismatch"
        requested = _integer_field(status, "requested_cycles", "requestedCycles", "cycles_requested")
        if requested != invocation.requested_cycles:
            return 0, _string_field(status, "state", "status"), (), status_path, cycles_path, summary_path if summary_path.exists() else None, "requested cycle count mismatch"
        completed = _integer_field(status, "completed_cycles", "completedCycles", "cycles_completed")
        if completed is None or completed < 0:
            return 0, _string_field(status, "state", "status"), (), status_path, cycles_path, summary_path if summary_path.exists() else None, "completed cycle count is missing or invalid"
        runner_state = _string_field(status, "state", "status")

        if cycles_path is None:
            return completed, runner_state, (), status_path, None, summary_path if summary_path.exists() else None, "cycles.csv/cycles.json is missing"
        try:
            records = _load_cycles(cycles_path)
        except ValueError as exc:
            return completed, runner_state, (), status_path, cycles_path, summary_path if summary_path.exists() else None, str(exc)
        if not summary_path.exists():
            return completed, runner_state, records, status_path, cycles_path, None, "summary.md is missing"
        if runner_state is None:
            return completed, None, records, status_path, cycles_path, summary_path, "runner state is missing"
        normalized_state = runner_state.lower()
        if normalized_state in _FAILURE_STATES:
            return completed, runner_state, records, status_path, cycles_path, summary_path, f"runner state reports failure: {runner_state}"
        if normalized_state not in _SUCCESS_STATES:
            return completed, runner_state, records, status_path, cycles_path, summary_path, f"runner state is not complete: {runner_state}"
        if completed != invocation.requested_cycles:
            return completed, runner_state, records, status_path, cycles_path, summary_path, f"completed {completed}/{invocation.requested_cycles} cycles"
        if len(records) != invocation.requested_cycles:
            return completed, runner_state, records, status_path, cycles_path, summary_path, f"cycle evidence contains {len(records)}/{invocation.requested_cycles} records"
        sequence_error = _cycle_sequence_error(records)
        if sequence_error is not None:
            return completed, runner_state, records, status_path, cycles_path, summary_path, sequence_error
        cycle_error = _cycle_failure(records)
        if cycle_error is not None:
            return completed, runner_state, records, status_path, cycles_path, summary_path, cycle_error
        return completed, runner_state, records, status_path, cycles_path, summary_path, None

    async def finish(
        self,
        invocation: ShadPS4ValidationInvocation,
        *,
        stall_timeout_seconds: float | None = None,
        watchdog_poll_interval_seconds: float = 5.0,
    ) -> ShadPS4ValidationResult:
        if stall_timeout_seconds is None:
            process_result = await invocation.process.wait()
        else:
            process_result = await wait_with_file_progress_watchdog(
                invocation.process,
                progress_paths=(
                    invocation.evidence_dir / "runner-status.json",
                    invocation.evidence_dir / "cycles.csv",
                    invocation.evidence_dir / "cycles.json",
                    invocation.evidence_dir / "summary.md",
                ),
                stall_timeout_seconds=stall_timeout_seconds,
                poll_interval_seconds=watchdog_poll_interval_seconds,
            )

        completed, runner_state, records, status_path, cycles_path, summary_path, evidence_error = self._read_evidence(invocation)
        checkpoint_status, checkpoint_error = _load_exit_checkpoint(
            invocation.exit_checkpoint_path, invocation.run_id
        )
        if invocation.exit_checkpoint_path.exists():
            relative = str(invocation.exit_checkpoint_path.relative_to(self.artifacts.root))
            existing = self.store._conn.execute(
                "SELECT 1 FROM artifacts WHERE task_id=? AND attempt_id=? AND kind='process_exit' AND path=?",
                (invocation.layout.attempt.task_id, invocation.layout.attempt.attempt_id, relative),
            ).fetchone()
            if existing is None:
                self.store.register_artifact(
                    task_id=invocation.layout.attempt.task_id,
                    attempt_id=invocation.layout.attempt.attempt_id,
                    kind="process_exit",
                    path=relative,
                    metadata={"run_id": invocation.run_id},
                )

        if process_result.state == "FAILED" and process_result.returncode == 0:
            kind = ShadPS4ValidationResultKind.PROCESS_FAILURE
            reason = "supervisor reported FAILED despite zero wrapper exit"
        elif process_result.state not in {"SUCCEEDED", "FAILED"}:
            kind = ShadPS4ValidationResultKind.PROCESS_FAILURE
            reason = process_result.state
        elif checkpoint_error is not None:
            if process_result.state == "FAILED" or process_result.returncode != 0:
                kind = ShadPS4ValidationResultKind.PROCESS_FAILURE
                reason = f"wrapper exit {process_result.returncode}; {checkpoint_error}"
            else:
                kind = ShadPS4ValidationResultKind.INCOMPLETE_EVIDENCE
                reason = checkpoint_error
        elif checkpoint_status != 0:
            if evidence_error is not None and _explicit_validation_failure(evidence_error):
                kind = ShadPS4ValidationResultKind.VALIDATION_FAILED
                reason = evidence_error
            elif evidence_error is None:
                kind = ShadPS4ValidationResultKind.VALIDATION_FAILED
                reason = f"harness exit {checkpoint_status}"
            else:
                kind = ShadPS4ValidationResultKind.PROCESS_FAILURE
                reason = f"harness exit {checkpoint_status}; {evidence_error}"
        elif evidence_error is not None:
            if _explicit_validation_failure(evidence_error):
                kind = ShadPS4ValidationResultKind.VALIDATION_FAILED
            else:
                kind = ShadPS4ValidationResultKind.INCOMPLETE_EVIDENCE
            reason = evidence_error
        else:
            # The wrapper may itself fail after atomically publishing the child's terminal
            # checkpoint. Once that checkpoint proves exit 0, it is the authoritative harness
            # outcome; wrapper lifecycle remains visible in process metadata below.
            kind = ShadPS4ValidationResultKind.SUCCESS
            reason = None

        authoritative_exit_status = (
            checkpoint_status if checkpoint_error is None and checkpoint_status is not None
            else process_result.returncode
        )
        normalized = {
            "kind": kind.value,
            "adapter": "shadps4-bloodborne",
            "run_id": invocation.run_id,
            "generation": invocation.generation,
            "candidate_sha": invocation.candidate_sha,
            "requested_cycles": invocation.requested_cycles,
            "completed_cycles": completed,
            "runner_state": runner_state,
            "cycle_records": [dict(record) for record in records],
            "evidence": {
                "runner_status": str(status_path.relative_to(self.artifacts.root)) if status_path else None,
                "cycles": str(cycles_path.relative_to(self.artifacts.root)) if cycles_path else None,
                "summary": str(summary_path.relative_to(self.artifacts.root)) if summary_path else None,
                "process_exit": str(invocation.exit_checkpoint_path.relative_to(self.artifacts.root)) if invocation.exit_checkpoint_path.exists() else None,
            },
            "reason": reason,
            "process": {
                "state": process_result.state,
                "returncode": process_result.returncode,
                "harness_exit_status": checkpoint_status if checkpoint_error is None else None,
                "pid": process_result.pid,
                "started_at": process_result.started_at,
                "ended_at": process_result.ended_at,
            },
        }
        self.artifacts.finalize_attempt(
            invocation.layout,
            status=kind.value,
            result=normalized,
            exit_status=authoritative_exit_status,
        )
        return ShadPS4ValidationResult(
            kind=kind,
            run_id=invocation.run_id,
            attempt_id=invocation.layout.attempt.attempt_id,
            generation=invocation.generation,
            candidate_sha=invocation.candidate_sha,
            requested_cycles=invocation.requested_cycles,
            completed_cycles=completed,
            process_result=process_result,
            runner_state=runner_state,
            cycle_records=records,
            evidence_dir=invocation.evidence_dir,
            status_path=status_path,
            cycles_path=cycles_path,
            summary_path=summary_path,
            reason=reason,
        )

    async def run(
        self,
        *,
        stall_timeout_seconds: float | None = None,
        watchdog_poll_interval_seconds: float = 5.0,
        **kwargs: Any,
    ) -> ShadPS4ValidationResult:
        invocation = await self.start(**kwargs)
        return await self.finish(
            invocation,
            stall_timeout_seconds=stall_timeout_seconds,
            watchdog_poll_interval_seconds=watchdog_poll_interval_seconds,
        )
