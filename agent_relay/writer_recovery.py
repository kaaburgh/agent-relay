from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .artifacts import ArtifactManager, AttemptLayout
from .git_workspace import CandidateGeneration, GitWorkspaceManager, record_candidate_generation
from .store import AttemptRow, Store, StoreError, utc_now


class WriterRecoveryError(StoreError):
    pass


@dataclass(frozen=True)
class WriterRecoveryResult:
    action: Literal["RUNNING", "RECOVERED", "ALREADY_RECOVERED"]
    attempt_id: int
    candidate: CandidateGeneration | None


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


def reconcile_writer_attempt(
    store: Store,
    *,
    artifact_root: str | Path,
    git: GitWorkspaceManager,
    task_id: str,
    writer_worktree: str | Path,
    baseline_sha: str,
    attempt_id: int,
    expected_previous_generation: int,
) -> WriterRecoveryResult:
    attempt = store.get_attempt(attempt_id)
    if attempt.task_id != task_id or attempt.kind != "writer":
        raise WriterRecoveryError("attempt is not the requested task writer attempt")
    layout = _layout(Path(artifact_root), attempt)
    provider_result_path = layout.directory / "provider-result.json"
    process = store._conn.execute(
        "SELECT * FROM processes WHERE attempt_id=? ORDER BY process_id DESC LIMIT 1",
        (attempt_id,),
    ).fetchone()

    if not provider_result_path.exists():
        if process is not None and process["state"] == "RUNNING" and _pid_is_live(int(process["pid"])):
            return WriterRecoveryResult("RUNNING", attempt_id, None)
        raise WriterRecoveryError(
            "writer ownership is ambiguous: no durable provider result and no live recorded process"
        )

    try:
        payload = json.loads(provider_result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WriterRecoveryError(f"writer provider result is unreadable: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("status") != "success":
        raise WriterRecoveryError("writer provider result does not prove successful completion")

    if process is not None and process["state"] == "RUNNING" and _pid_is_live(int(process["pid"])):
        return WriterRecoveryResult("RUNNING", attempt_id, None)

    try:
        candidate_sha = git.detect_candidate(writer_worktree, baseline_sha)
    except Exception as exc:
        raise WriterRecoveryError(f"writer result exists but candidate Git evidence is invalid: {exc}") from exc
    if not candidate_sha:
        raise WriterRecoveryError("writer result exists but no committed candidate is present")

    existing = store._conn.execute(
        "SELECT generation,candidate_sha,writer_attempt_id,created_at FROM candidate_generations WHERE task_id=? AND candidate_sha=?",
        (task_id, candidate_sha),
    ).fetchone()
    if existing is not None:
        candidate = CandidateGeneration(
            task_id=task_id,
            generation=int(existing["generation"]),
            candidate_sha=existing["candidate_sha"],
            writer_attempt_id=existing["writer_attempt_id"],
            created_at=existing["created_at"],
        )
        return WriterRecoveryResult("ALREADY_RECOVERED", attempt_id, candidate)

    recovered_now = utc_now()
    with store._transaction():
        if process is not None and process["state"] == "RUNNING":
            store._conn.execute(
                "UPDATE processes SET state='SUCCEEDED', ended_at=?, exit_status=0, last_liveness_at=? WHERE process_id=? AND state='RUNNING'",
                (recovered_now, recovered_now, process["process_id"]),
            )
        row = store._conn.execute(
            "SELECT status FROM attempts WHERE attempt_id=?", (attempt_id,)
        ).fetchone()
        if row is None:
            raise WriterRecoveryError("writer attempt disappeared during recovery")

    if attempt.status == "CREATED":
        manager = ArtifactManager(artifact_root, store)
        manager.finalize_attempt(
            layout,
            status="SUCCESS",
            result={
                "kind": "SUCCESS",
                "candidate_sha": candidate_sha,
                "reason": "recovered from durable worker result and Git commit",
                "provider_payload": payload,
                "recovered": True,
            },
            exit_status=0,
        )

    candidate = record_candidate_generation(
        store,
        task_id=task_id,
        candidate_sha=candidate_sha,
        writer_attempt_id=attempt_id,
        expected_previous_generation=expected_previous_generation,
    )
    store.append_event(
        task_id=task_id,
        event_type="writer_recovered",
        stage=store.get_task(task_id).stage,
        generation=candidate.generation,
        payload={"attempt_id": attempt_id, "candidate_sha": candidate_sha},
    )
    return WriterRecoveryResult("RECOVERED", attempt_id, candidate)
