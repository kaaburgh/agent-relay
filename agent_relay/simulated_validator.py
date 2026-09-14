from __future__ import annotations

import csv
import json
import sys
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping

from .artifacts import ArtifactManager, AttemptLayout
from .store import Store
from .supervisor import ProcessResult, SubprocessSupervisor
from .watchdog import wait_with_file_progress_watchdog


class ValidationResultKind(StrEnum):
    SUCCESS = "SUCCESS"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    INCOMPLETE_EVIDENCE = "INCOMPLETE_EVIDENCE"
    PROCESS_FAILURE = "PROCESS_FAILURE"


@dataclass(frozen=True)
class SimulatedValidationInvocation:
    layout: AttemptLayout
    run_id: str
    evidence_dir: Path
    process: Any
    generation: int
    candidate_sha: str
    requested_cycles: int


@dataclass(frozen=True)
class SimulatedValidationResult:
    kind: ValidationResultKind
    run_id: str
    attempt_id: int
    generation: int
    candidate_sha: str
    process_result: ProcessResult
    completed_cycles: int
    metrics: tuple[Mapping[str, Any], ...]
    evidence_dir: Path
    reason: str | None = None


class SimulatedValidator:
    def __init__(self, *, store: Store, artifacts: ArtifactManager, supervisor: SubprocessSupervisor) -> None:
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
        requested_cycles: int = 3,
        behavior: Mapping[str, Any] | None = None,
        timeout_seconds: float | None = None,
    ) -> SimulatedValidationInvocation:
        if requested_cycles <= 0:
            raise ValueError("requested_cycles must be positive")
        run_id = str(uuid.uuid4())
        config = {"run_id": run_id, "requested_cycles": requested_cycles, **dict(behavior or {})}
        layout = self.artifacts.create_attempt(
            task_id=task_id,
            kind="validation",
            generation=generation,
            inputs={"generation": generation, "candidate_sha": candidate_sha, "config": config},
            command=["simulated-validator"],
        )
        config_path = layout.directory / "validator-config.json"
        evidence_dir = layout.directory / "evidence" / run_id
        with config_path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(config, stream, indent=2, sort_keys=True)
            stream.write("\n")
        self.store.register_artifact(
            task_id=task_id,
            attempt_id=layout.attempt.attempt_id,
            kind="validation_config",
            path=str(config_path.relative_to(self.artifacts.root)),
        )
        process = await self.supervisor.start(
            task_id=task_id,
            attempt_id=layout.attempt.attempt_id,
            argv=[sys.executable, "-m", "agent_relay.simulated_validator_worker", "--config", str(config_path), "--evidence-dir", str(evidence_dir)],
            cwd=Path(cwd),
            stdout_path=layout.stdout_path,
            stderr_path=layout.stderr_path,
            timeout_seconds=timeout_seconds,
            heartbeat_interval=0.05,
            terminate_grace_seconds=0.1,
        )
        return SimulatedValidationInvocation(
            layout=layout,
            run_id=run_id,
            evidence_dir=evidence_dir,
            process=process,
            generation=generation,
            candidate_sha=candidate_sha,
            requested_cycles=requested_cycles,
        )

    def _read_evidence(self, invocation: SimulatedValidationInvocation) -> tuple[int, tuple[Mapping[str, Any], ...], str | None]:
        status_path = invocation.evidence_dir / "runner-status.json"
        cycles_path = invocation.evidence_dir / "cycles.csv"
        summary_path = invocation.evidence_dir / "summary.md"
        for kind, path in (("runner_status", status_path), ("cycles", cycles_path), ("summary", summary_path)):
            if path.exists():
                self.store.register_artifact(
                    task_id=invocation.layout.attempt.task_id,
                    attempt_id=invocation.layout.attempt.attempt_id,
                    kind=kind,
                    path=str(path.relative_to(self.artifacts.root)),
                    metadata={"run_id": invocation.run_id, "generation": invocation.generation, "candidate_sha": invocation.candidate_sha},
                )
        if not status_path.exists():
            return 0, (), "runner-status.json is missing"
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return 0, (), f"runner-status.json is unreadable: {exc}"
        if status.get("run_id") != invocation.run_id:
            return int(status.get("completed_cycles", 0)), (), "runner status run_id mismatch"
        completed = int(status.get("completed_cycles", 0))
        if not cycles_path.exists():
            return completed, (), "cycles.csv is missing"
        try:
            with cycles_path.open("r", encoding="utf-8", newline="") as stream:
                rows = tuple(dict(row) for row in csv.DictReader(stream))
        except OSError as exc:
            return completed, (), f"cycles.csv is unreadable: {exc}"
        if not summary_path.exists():
            return completed, rows, "summary.md is missing"
        if status.get("state") != "completed":
            return completed, rows, f"runner state is {status.get('state')!r}, not completed"
        if int(status.get("requested_cycles", -1)) != invocation.requested_cycles:
            return completed, rows, "requested cycle count mismatch"
        if completed != invocation.requested_cycles:
            return completed, rows, f"completed {completed}/{invocation.requested_cycles} cycles"
        if len(rows) != invocation.requested_cycles:
            return completed, rows, f"cycles.csv contains {len(rows)}/{invocation.requested_cycles} records"
        if any(row.get("status") != "ok" for row in rows):
            return completed, rows, "one or more cycles did not succeed"
        expected_cycles = [str(index) for index in range(1, invocation.requested_cycles + 1)]
        if [row.get("cycle") for row in rows] != expected_cycles:
            return completed, rows, "cycle sequence is incomplete or out of order"
        return completed, rows, None

    async def finish(
        self,
        invocation: SimulatedValidationInvocation,
        *,
        stall_timeout_seconds: float | None = None,
        watchdog_poll_interval_seconds: float = 0.05,
    ) -> SimulatedValidationResult:
        if stall_timeout_seconds is None:
            process_result = await invocation.process.wait()
        else:
            process_result = await wait_with_file_progress_watchdog(
                invocation.process,
                progress_paths=(
                    invocation.evidence_dir / "runner-status.json",
                    invocation.evidence_dir / "cycles.csv",
                    invocation.evidence_dir / "summary.md",
                ),
                stall_timeout_seconds=stall_timeout_seconds,
                poll_interval_seconds=watchdog_poll_interval_seconds,
            )
        completed, metrics, evidence_error = self._read_evidence(invocation)
        if process_result.state not in {"SUCCEEDED", "FAILED"}:
            kind = ValidationResultKind.PROCESS_FAILURE
            reason = process_result.state
        elif process_result.returncode != 0:
            kind = ValidationResultKind.VALIDATION_FAILED
            reason = f"validator exit {process_result.returncode}"
        elif evidence_error is not None:
            kind = ValidationResultKind.INCOMPLETE_EVIDENCE
            reason = evidence_error
        else:
            kind = ValidationResultKind.SUCCESS
            reason = None
        normalized = {
            "kind": kind.value,
            "run_id": invocation.run_id,
            "generation": invocation.generation,
            "candidate_sha": invocation.candidate_sha,
            "requested_cycles": invocation.requested_cycles,
            "completed_cycles": completed,
            "reason": reason,
            "process": {"state": process_result.state, "returncode": process_result.returncode, "pid": process_result.pid},
        }
        self.artifacts.finalize_attempt(
            invocation.layout,
            status=kind.value,
            result=normalized,
            exit_status=process_result.returncode,
        )
        return SimulatedValidationResult(
            kind=kind,
            run_id=invocation.run_id,
            attempt_id=invocation.layout.attempt.attempt_id,
            generation=invocation.generation,
            candidate_sha=invocation.candidate_sha,
            process_result=process_result,
            completed_cycles=completed,
            metrics=metrics,
            evidence_dir=invocation.evidence_dir,
            reason=reason,
        )

    async def run(
        self,
        *,
        stall_timeout_seconds: float | None = None,
        watchdog_poll_interval_seconds: float = 0.05,
        **kwargs: Any,
    ) -> SimulatedValidationResult:
        invocation = await self.start(**kwargs)
        return await self.finish(
            invocation,
            stall_timeout_seconds=stall_timeout_seconds,
            watchdog_poll_interval_seconds=watchdog_poll_interval_seconds,
        )
