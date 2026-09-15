from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping, Any, Sequence

from .artifacts import ArtifactManager, AttemptLayout
from .evidence import ValidationEvidence, record_validation
from .resource_leases import ResourceLease, release_lease
from .runtime_safety import process_ownership_is_live
from .store import AttemptRow, Store, StoreError, utc_now


class ValidationRecoveryError(StoreError):
    pass


@dataclass(frozen=True)
class ValidationRecoveryResult:
    action: Literal["RUNNING", "RECOVERED", "ALREADY_RECOVERED", "AMBIGUOUS"]
    attempt_id: int
    validation: ValidationEvidence | None
    reason: str | None = None


_SUCCESS_STATES = {"completed", "complete", "success", "succeeded", "done", "passed"}
_SUCCESS_CYCLE_STATUSES = {"ok", "success", "succeeded", "passed", "complete", "completed"}
_CYCLE_ID_FIELDS = ("cycle", "cycle_id", "index")


def _layout(artifact_root: Path, attempt: AttemptRow) -> AttemptLayout:
    directory = artifact_root / "tasks" / attempt.task_id / attempt.artifact_dir
    return AttemptLayout(
        attempt=attempt,
        directory=directory,
        metadata_path=directory / "attempt.json",
        inputs_path=directory / "inputs.json",
        command_path=directory / "command.json",
        config_path=directory / "config.json",
        stdout_path=directory / "stdout.log",
        stderr_path=directory / "stderr.log",
        result_path=directory / "result.json",
    )


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


def _load_cycle_records(
    evidence_dir: Path,
) -> tuple[tuple[Mapping[str, Any], ...], Path | None, str | None]:
    csv_path = evidence_dir / "cycles.csv"
    json_path = evidence_dir / "cycles.json"
    if csv_path.exists():
        try:
            with csv_path.open("r", encoding="utf-8", newline="") as stream:
                return tuple(dict(row) for row in csv.DictReader(stream)), csv_path, None
        except OSError as exc:
            return (), csv_path, f"cycles.csv is unreadable: {exc}"
    if not json_path.exists():
        return (), None, "cycles.csv/cycles.json is missing"
    try:
        value = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return (), json_path, f"cycles.json is unreadable: {exc}"
    if isinstance(value, Mapping):
        value = value.get("cycles")
    if not isinstance(value, list) or any(not isinstance(item, Mapping) for item in value):
        return (), json_path, "cycles.json must be a list of objects or an object with a cycles list"
    return tuple(dict(item) for item in value), json_path, None


def _cycle_sequence_error(records: Sequence[Mapping[str, Any]]) -> str | None:
    has_identifier = [any(name in record for name in _CYCLE_ID_FIELDS) for record in records]
    if not any(has_identifier):
        return None
    if not all(has_identifier):
        return "cycle identifiers are partially missing"
    sequence = [_integer_field(record, *_CYCLE_ID_FIELDS) for record in records]
    if any(value is None for value in sequence):
        return "cycle identifiers are invalid"
    if sequence != list(range(1, len(records) + 1)):
        return "cycle sequence is incomplete or out of order"
    return None


def _cycle_success_error(records: Sequence[Mapping[str, Any]]) -> str | None:
    for index, record in enumerate(records, start=1):
        raw = _string_field(record, "status", "result", "outcome")
        if raw is None:
            return f"cycle {index} is missing status/result/outcome"
        if raw.lower() not in _SUCCESS_CYCLE_STATUSES:
            return f"cycle {index} did not prove success: {raw!r}"
    return None


def _complete_evidence(
    evidence_dir: Path,
    *,
    run_id: str,
    requested_cycles: int,
) -> tuple[int, tuple[Mapping[str, Any], ...], Path | None, str | None]:
    status_path = evidence_dir / "runner-status.json"
    summary_path = evidence_dir / "summary.md"
    if not status_path.exists():
        return 0, (), None, "runner-status.json is missing"
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return 0, (), None, f"runner-status.json is unreadable: {exc}"
    if not isinstance(status, Mapping):
        return 0, (), None, "runner-status.json root must be an object"

    completed = _integer_field(status, "completed_cycles", "completedCycles", "cycles_completed")
    if completed is None or completed < 0:
        return 0, (), None, "completed cycle count is missing or invalid"
    if _string_field(status, "run_id", "runId") != run_id:
        return completed, (), None, "runner status run_id mismatch"
    if _integer_field(status, "requested_cycles", "requestedCycles", "cycles_requested") != requested_cycles:
        return completed, (), None, "requested cycle count mismatch"

    rows, cycles_path, cycle_load_error = _load_cycle_records(evidence_dir)
    if cycle_load_error is not None:
        return completed, rows, cycles_path, cycle_load_error
    if not summary_path.exists():
        return completed, rows, cycles_path, "summary.md is missing"

    state = _string_field(status, "state", "status")
    if state is None or state.lower() not in _SUCCESS_STATES:
        return completed, rows, cycles_path, f"runner state is {state!r}, not a success state"
    if completed != requested_cycles:
        return completed, rows, cycles_path, f"completed {completed}/{requested_cycles} cycles"
    if len(rows) != requested_cycles:
        name = cycles_path.name if cycles_path is not None else "cycle evidence"
        return completed, rows, cycles_path, f"{name} contains {len(rows)}/{requested_cycles} records"

    sequence_error = _cycle_sequence_error(rows)
    if sequence_error is not None:
        return completed, rows, cycles_path, sequence_error
    success_error = _cycle_success_error(rows)
    if success_error is not None:
        return completed, rows, cycles_path, success_error
    return completed, rows, cycles_path, None


def _exit_checkpoint(
    path: Path,
    *,
    run_id: str,
) -> tuple[int | None, str | None]:
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
    exit_status = _integer_field(value, "exit_status", "exitStatus", "returncode")
    if exit_status is None:
        return None, "process-exit.json exit status is missing or invalid"
    return exit_status, None


def _register_if_missing(
    store: Store,
    *,
    task_id: str,
    attempt_id: int,
    kind: str,
    path: str,
    metadata: Mapping[str, Any],
) -> None:
    existing = store._conn.execute(
        "SELECT 1 FROM artifacts WHERE task_id=? AND attempt_id=? AND kind=? AND path=?",
        (task_id, attempt_id, kind, path),
    ).fetchone()
    if existing is None:
        store.register_artifact(
            task_id=task_id,
            attempt_id=attempt_id,
            kind=kind,
            path=path,
            metadata=metadata,
        )


def reconcile_validation_attempt(
    store: Store,
    *,
    artifact_root: str | Path,
    task_id: str,
    attempt_id: int,
    generation: int,
    candidate_sha: str,
    run_id: str,
    requested_cycles: int,
    evidence_dir: str | Path,
    lease: ResourceLease,
) -> ValidationRecoveryResult:
    attempt = store.get_attempt(attempt_id)
    if attempt.task_id != task_id or attempt.kind != "validation":
        raise ValidationRecoveryError("attempt is not the requested validation attempt")
    if attempt.generation != generation:
        raise ValidationRecoveryError("validation attempt generation mismatch")
    if lease.task_id != task_id or lease.attempt_id != attempt_id or lease.released_at is not None:
        raise ValidationRecoveryError("active lease ownership does not match validation attempt")

    prior = store._conn.execute(
        "SELECT * FROM validations WHERE task_id=? AND attempt_id=? ORDER BY validation_id DESC LIMIT 1",
        (task_id, attempt_id),
    ).fetchone()
    if prior is not None:
        validation = ValidationEvidence(
            int(prior["validation_id"]), prior["task_id"], int(prior["generation"]),
            int(prior["attempt_id"]), prior["candidate_sha"], prior["status"],
            json.loads(prior["result_json"]), prior["created_at"],
        )
        return ValidationRecoveryResult("ALREADY_RECOVERED", attempt_id, validation)

    process = store._conn.execute(
        "SELECT * FROM processes WHERE attempt_id=? ORDER BY process_id DESC LIMIT 1",
        (attempt_id,),
    ).fetchone()
    if (
        process is not None
        and process["state"] == "RUNNING"
        and process_ownership_is_live(store, process)
    ):
        return ValidationRecoveryResult("RUNNING", attempt_id, None)

    evidence_dir = Path(evidence_dir)
    completed, rows, cycles_path, error = _complete_evidence(
        evidence_dir, run_id=run_id, requested_cycles=requested_cycles
    )
    if error is not None:
        return ValidationRecoveryResult(
            "AMBIGUOUS",
            attempt_id,
            None,
            f"validator process is not live and durable evidence is incomplete: {error}",
        )
    if cycles_path is None:
        return ValidationRecoveryResult(
            "AMBIGUOUS",
            attempt_id,
            None,
            "validator process is not live and cycle evidence path is missing",
        )

    artifact_root = Path(artifact_root)
    current_layout = _layout(artifact_root, store.get_attempt(attempt_id))
    checkpoint_path = current_layout.directory / "process-exit.json"
    exit_status, checkpoint_error = _exit_checkpoint(checkpoint_path, run_id=run_id)
    if checkpoint_error is not None:
        return ValidationRecoveryResult(
            "AMBIGUOUS",
            attempt_id,
            None,
            "validator process is not live but successful process exit is unproven: "
            + checkpoint_error,
        )
    if exit_status != 0:
        return ValidationRecoveryResult(
            "AMBIGUOUS",
            attempt_id,
            None,
            f"validator terminal checkpoint proves nonzero exit {exit_status}; cannot recover success",
        )

    if process is not None:
        process_state = process["state"]
        process_exit_status = process["exit_status"]
        if process_state in {"CAPTURE_FAILED", "TIMED_OUT", "STALLED", "CANCELLED", "TERMINATED"}:
            return ValidationRecoveryResult(
                "AMBIGUOUS",
                attempt_id,
                None,
                f"durable process state is {process_state!r}, not a successful recoverable state",
            )
        if process_state == "SUCCEEDED" and process_exit_status not in {0, None}:
            return ValidationRecoveryResult(
                "AMBIGUOUS", attempt_id, None, "durable successful process has nonzero exit status"
            )
        if process_state == "FAILED" and process_exit_status in {0, None}:
            return ValidationRecoveryResult(
                "AMBIGUOUS",
                attempt_id,
                None,
                "durable FAILED wrapper has no nonzero wrapper exit to explain its failure",
            )
        if process_state not in {"RUNNING", "SUCCEEDED", "FAILED"}:
            return ValidationRecoveryResult(
                "AMBIGUOUS",
                attempt_id,
                None,
                f"durable process state is {process_state!r}, not a known recoverable wrapper state",
            )
        # FAILED with a nonzero wrapper exit is recoverable when the child-owned checkpoint
        # already proves that the harness itself completed successfully. The wrapper failure
        # remains immutable process metadata and is not rewritten below.

    metadata = {"run_id": run_id, "generation": generation, "candidate_sha": candidate_sha}
    for kind, path in (
        ("runner_status", evidence_dir / "runner-status.json"),
        ("cycles", cycles_path),
        ("summary", evidence_dir / "summary.md"),
        ("process_exit", checkpoint_path),
    ):
        _register_if_missing(
            store,
            task_id=task_id,
            attempt_id=attempt_id,
            kind=kind,
            path=str(path.relative_to(artifact_root)),
            metadata=metadata,
        )

    now = utc_now()
    if process is not None and process["state"] == "RUNNING":
        with store._transaction():
            store._conn.execute(
                """
                UPDATE processes
                SET state='SUCCEEDED', ended_at=?, exit_status=0, last_liveness_at=?
                WHERE process_id=? AND state='RUNNING'
                """,
                (now, now, process["process_id"]),
            )

    current_attempt = store.get_attempt(attempt_id)
    if current_attempt.ended_at is None:
        ArtifactManager(artifact_root, store).finalize_attempt(
            _layout(artifact_root, current_attempt),
            status="SUCCESS",
            result={
                "kind": "SUCCESS",
                "run_id": run_id,
                "generation": generation,
                "candidate_sha": candidate_sha,
                "requested_cycles": requested_cycles,
                "completed_cycles": completed,
                "recovered": True,
            },
            exit_status=0,
        )
    elif current_attempt.status != "SUCCESS":
        return ValidationRecoveryResult(
            "AMBIGUOUS",
            attempt_id,
            None,
            f"validation attempt is already terminal as {current_attempt.status!r}",
        )

    validation = record_validation(
        store,
        task_id=task_id,
        generation=generation,
        candidate_sha=candidate_sha,
        attempt_id=attempt_id,
        status="SUCCESS",
        result={
            "run_id": run_id,
            "requested_cycles": requested_cycles,
            "completed_cycles": completed,
            "metrics": list(rows),
            "recovered": True,
        },
    )
    release_lease(
        store,
        lease_id=lease.lease_id,
        holder_id=lease.holder_id,
        recovered=True,
    )
    store.append_event(
        task_id=task_id,
        event_type="validation_recovered",
        stage=store.get_task(task_id).stage,
        generation=generation,
        payload={"attempt_id": attempt_id, "run_id": run_id, "candidate_sha": candidate_sha},
    )
    return ValidationRecoveryResult("RECOVERED", attempt_id, validation)
