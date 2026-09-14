from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping, Any

from .artifacts import ArtifactManager, AttemptLayout
from .evidence import ValidationEvidence, record_validation
from .resource_leases import ResourceLease, release_lease
from .store import AttemptRow, Store, StoreError, utc_now


class ValidationRecoveryError(StoreError):
    pass


@dataclass(frozen=True)
class ValidationRecoveryResult:
    action: Literal["RUNNING", "RECOVERED", "ALREADY_RECOVERED", "AMBIGUOUS"]
    attempt_id: int
    validation: ValidationEvidence | None
    reason: str | None = None


def _pid_is_live(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    stat = Path(f"/proc/{pid}/stat")
    if stat.exists():
        try:
            return stat.read_text().split()[2] != "Z"
        except (FileNotFoundError, IndexError):
            return False
    return True


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


def _complete_evidence(
    evidence_dir: Path,
    *,
    run_id: str,
    requested_cycles: int,
) -> tuple[int, tuple[Mapping[str, Any], ...], str | None]:
    status_path = evidence_dir / "runner-status.json"
    cycles_path = evidence_dir / "cycles.csv"
    summary_path = evidence_dir / "summary.md"
    if not status_path.exists():
        return 0, (), "runner-status.json is missing"
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return 0, (), f"runner-status.json is unreadable: {exc}"
    completed = int(status.get("completed_cycles", 0))
    if status.get("run_id") != run_id:
        return completed, (), "runner status run_id mismatch"
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
    if int(status.get("requested_cycles", -1)) != requested_cycles:
        return completed, rows, "requested cycle count mismatch"
    if completed != requested_cycles:
        return completed, rows, f"completed {completed}/{requested_cycles} cycles"
    if len(rows) != requested_cycles:
        return completed, rows, f"cycles.csv contains {len(rows)}/{requested_cycles} records"
    if [row.get("cycle") for row in rows] != [str(index) for index in range(1, requested_cycles + 1)]:
        return completed, rows, "cycle sequence is incomplete or out of order"
    if any(row.get("status") != "ok" for row in rows):
        return completed, rows, "one or more cycles did not succeed"
    return completed, rows, None


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
    if process is not None and process["state"] == "RUNNING" and _pid_is_live(int(process["pid"])):
        return ValidationRecoveryResult("RUNNING", attempt_id, None)

    evidence_dir = Path(evidence_dir)
    completed, rows, error = _complete_evidence(
        evidence_dir, run_id=run_id, requested_cycles=requested_cycles
    )
    if error is not None:
        return ValidationRecoveryResult(
            "AMBIGUOUS",
            attempt_id,
            None,
            f"validator process is not live and durable evidence is incomplete: {error}",
        )

    artifact_root = Path(artifact_root)
    metadata = {"run_id": run_id, "generation": generation, "candidate_sha": candidate_sha}
    for kind, path in (
        ("runner_status", evidence_dir / "runner-status.json"),
        ("cycles", evidence_dir / "cycles.csv"),
        ("summary", evidence_dir / "summary.md"),
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
                "UPDATE processes SET state='SUCCEEDED', ended_at=?, exit_status=0, last_liveness_at=? WHERE process_id=? AND state='RUNNING'",
                (now, now, process["process_id"]),
            )

    if attempt.status == "CREATED":
        ArtifactManager(artifact_root, store).finalize_attempt(
            _layout(artifact_root, attempt),
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
