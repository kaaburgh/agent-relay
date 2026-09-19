from __future__ import annotations

import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from .config import load_global_config
from .evidence import latest_review, latest_validation
from .models import ConfigError, GlobalConfig, ProviderConfig, TaskSpec
from .operator_ownership import process_ownership_state
from .provider_retry import get_provider_wait, resume_provider_if_due
from .resource_leases import active_leases, configure_resource, release_lease
from .store import Store, StoreError, TaskNotFound, TaskRow, utc_now
from .workflow import WorkflowStage, WorkflowStateMachine


class OperatorError(RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimeContext:
    config: GlobalConfig | None
    config_path: Path | None
    state_dir: Path
    database_path: Path


def resolve_context(
    *,
    config_path: str | Path | None = None,
    state_dir: str | Path | None = None,
) -> RuntimeContext:
    source = Path(config_path).expanduser().resolve() if config_path is not None else None
    config = load_global_config(source) if source is not None else None
    if state_dir is not None:
        resolved_state = Path(state_dir).expanduser().resolve()
    elif config is not None:
        configured = config.state_dir.expanduser()
        resolved_state = (
            configured.resolve()
            if configured.is_absolute()
            else (source.parent / configured).resolve()  # type: ignore[union-attr]
        )
    else:
        resolved_state = (Path.cwd() / ".agent-relay").resolve()
    return RuntimeContext(
        config=config,
        config_path=source,
        state_dir=resolved_state,
        database_path=resolved_state / "state.sqlite3",
    )


def task_spec_mapping(task: TaskSpec) -> dict[str, Any]:
    value = asdict(task)
    value["validation"] = [dict(item) for item in value["validation"]]
    value["runtime_resources"] = list(task.runtime_resources)
    value["acceptance_criteria"] = list(task.acceptance_criteria)
    for index, step in enumerate(task.validation):
        value["validation"][index]["argv"] = list(step.argv)
        value["validation"][index]["resources"] = list(step.resources)
    return value


def _safe_task_id(value: str) -> str:
    if not value or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for character in value):
        raise ConfigError("task.task_id may contain only letters, digits, '.', '_' and '-'")
    if value in {".", ".."}:
        raise ConfigError("task.task_id is unsafe")
    return value


def allocate_task_id(task: TaskSpec) -> str:
    if task.task_id is not None:
        return _safe_task_id(task.task_id)
    return f"task-{uuid.uuid4().hex[:12]}"


def initialize_configured_resources(store: Store, config: GlobalConfig | None) -> None:
    if config is None:
        return
    for name, resource in config.resources.items():
        configure_resource(store, name, resource.capacity)


def _validate_task_references(task: TaskSpec, config: GlobalConfig | None) -> None:
    if config is None:
        return
    for step in task.validation:
        if step.runner not in config.runners:
            raise ConfigError(f"task validation runner is not configured: {step.runner}")
        for resource in step.resources:
            if resource not in config.resources:
                raise ConfigError(f"task validation resource is not configured: {resource}")
    for resource in task.runtime_resources:
        if resource not in config.resources:
            raise ConfigError(f"task runtime resource is not configured: {resource}")


def create_task(context: RuntimeContext, task: TaskSpec) -> TaskRow:
    _validate_task_references(task, context.config)
    task_id = allocate_task_id(task)
    context.state_dir.mkdir(parents=True, exist_ok=True)
    snapshot_dir = context.state_dir / "tasks" / task_id
    snapshot_dir.mkdir(parents=True, exist_ok=False)
    snapshot_path = snapshot_dir / "task.yaml"
    mapping = task_spec_mapping(task)
    mapping["task_id"] = task_id
    try:
        with snapshot_path.open("x", encoding="utf-8", newline="\n") as stream:
            yaml.safe_dump(mapping, stream, sort_keys=False, allow_unicode=True)
            stream.flush()
            os.fsync(stream.fileno())
        with Store(context.database_path) as store:
            initialize_configured_resources(store, context.config)
            row = store.create_task(
                task_id=task_id,
                repository=task.repository,
                baseline_ref=task.baseline,
                task_spec=mapping,
            )
    except BaseException:
        try:
            snapshot_path.unlink(missing_ok=True)
            snapshot_dir.rmdir()
        except OSError:
            pass
        raise
    return row


def task_status(store: Store, task_id: str) -> dict[str, Any]:
    task = store.get_task(task_id)
    process_rows = store._conn.execute(
        """
        SELECT process_id,attempt_id,pid,process_group_id,state,started_at,ended_at,
               exit_status,last_liveness_at
        FROM processes WHERE task_id=? ORDER BY process_id DESC
        """,
        (task_id,),
    ).fetchall()
    active_processes: list[dict[str, Any]] = []
    for row in process_rows:
        if row["state"] != "RUNNING":
            continue
        ownership = process_ownership_state(store, row)
        active_processes.append(
            {
                "process_id": int(row["process_id"]),
                "attempt_id": row["attempt_id"],
                "pid": int(row["pid"]),
                "process_group_id": row["process_group_id"],
                "state": row["state"],
                "pid_alive": ownership == "LIVE",
                "ownership": ownership,
                "started_at": row["started_at"],
                "last_liveness_at": row["last_liveness_at"],
            }
        )
    events = store.events(task_id)
    last_event = events[-1] if events else None
    wait = get_provider_wait(store, task_id)
    review = latest_review(store, task_id, task.current_generation) if task.current_generation else None
    validation = (
        latest_validation(store, task_id, task.current_generation) if task.current_generation else None
    )
    leases = active_leases(store)
    task_leases = [
        {
            "lease_id": lease.lease_id,
            "resource": lease.resource_name,
            "holder_id": lease.holder_id,
            "attempt_id": lease.attempt_id,
            "acquired_at": lease.acquired_at,
            "heartbeat_at": lease.heartbeat_at,
        }
        for lease in leases
        if lease.task_id == task_id
    ]
    return {
        "task_id": task.task_id,
        "stage": task.stage,
        "stage_attempt": task.stage_attempt,
        "generation": task.current_generation,
        "candidate_sha": task.current_candidate_sha,
        "repository": task.repository,
        "baseline": task.baseline_ref,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "observed_at": utc_now(),
        "active_processes": active_processes,
        "last_event": None
        if last_event is None
        else {
            "sequence": last_event.sequence,
            "type": last_event.event_type,
            "stage": last_event.stage,
            "generation": last_event.generation,
            "created_at": last_event.created_at,
            "payload": dict(last_event.payload),
        },
        "review": None
        if review is None
        else {
            "review_id": review.review_id,
            "generation": review.generation,
            "candidate_sha": review.candidate_sha,
            "run_id": review.run_id,
            "verdict": review.verdict,
            "summary": review.summary,
            "finding_count": len(review.findings),
            "created_at": review.created_at,
        },
        "validation": None
        if validation is None
        else {
            "validation_id": validation.validation_id,
            "generation": validation.generation,
            "candidate_sha": validation.candidate_sha,
            "status": validation.status,
            "created_at": validation.created_at,
        },
        "provider_wait": None
        if wait is None
        else {
            "provider": wait.provider,
            "reason": wait.reason,
            "first_seen": wait.first_seen,
            "last_attempt": wait.last_attempt,
            "next_retry": wait.next_retry,
            "attempt_count": wait.attempt_count,
            "resume_stage": wait.resume_stage,
        },
        "leases": task_leases,
    }


def task_events(store: Store, task_id: str, *, limit: int | None = None) -> list[dict[str, Any]]:
    events = list(store.events(task_id))
    if limit is not None:
        if limit <= 0:
            raise OperatorError("event limit must be positive")
        events = events[-limit:]
    return [
        {
            "sequence": event.sequence,
            "type": event.event_type,
            "stage": event.stage,
            "generation": event.generation,
            "payload": dict(event.payload),
            "created_at": event.created_at,
        }
        for event in events
    ]


def resume_task(store: Store, task_id: str) -> dict[str, Any]:
    task = store.get_task(task_id)
    if task.stage == WorkflowStage.WAITING_PROVIDER.value:
        resumed = resume_provider_if_due(store, task_id=task_id)
        return {
            "action": "provider-retry-started" if resumed else "waiting-provider",
            "resumed": resumed,
            "status": task_status(store, task_id),
        }
    if task.stage in {WorkflowStage.DONE.value, WorkflowStage.FAILED.value, WorkflowStage.CANCELLED.value}:
        return {"action": "terminal", "resumed": False, "status": task_status(store, task_id)}
    active = task_status(store, task_id)["active_processes"]
    if active:
        return {"action": "already-running", "resumed": False, "status": task_status(store, task_id)}
    if task.stage == WorkflowStage.READY.value:
        return {"action": "ready-use-run", "resumed": False, "status": task_status(store, task_id)}
    return {
        "action": "recovery-required",
        "resumed": False,
        "reason": "active-stage ownership is not live; use the provider/tool recovery adapter instead of launching duplicate work",
        "status": task_status(store, task_id),
    }


def _process_group_alive(group: int) -> bool:
    try:
        os.killpg(group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True

    proc = Path("/proc")
    if not proc.is_dir():
        return True
    observed_member = False
    try:
        entries = tuple(proc.iterdir())
    except OSError:
        return True
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "stat").read_text(encoding="utf-8")
            close_paren = raw.rfind(")")
            if close_paren < 0:
                continue
            fields = raw[close_paren + 2 :].split()
            if len(fields) < 3 or int(fields[2]) != group:
                continue
            observed_member = True
            if fields[0] != "Z":
                return True
        except (OSError, ValueError):
            continue
    return not observed_member


def _terminate_owned_process_group(
    store: Store,
    row: Any,
    grace_seconds: float,
) -> tuple[bool, bool]:
    ownership = process_ownership_state(store, row)
    if ownership == "DEAD":
        return False, False
    if ownership != "LIVE":
        raise OperatorError(
            f"refusing to signal process {row['process_id']}: durable ownership is {ownership}"
        )

    group = int(row["process_group_id"] or row["pid"])
    if not _process_group_alive(group):
        return False, False
    # Re-check immediately before the destructive signal to close the PID-reuse TOCTOU gap.
    ownership = process_ownership_state(store, row)
    if ownership == "DEAD":
        return False, False
    if ownership != "LIVE":
        raise OperatorError(
            f"refusing to signal process {row['process_id']}: ownership changed to {ownership}"
        )

    sent_term = False
    sent_kill = False
    try:
        os.killpg(group, signal.SIGTERM)
        sent_term = True
    except ProcessLookupError:
        return False, False
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        if not _process_group_alive(group):
            return sent_term, sent_kill
        time.sleep(min(0.02, max(0.0, deadline - time.monotonic())))

    if _process_group_alive(group):
        ownership = process_ownership_state(store, row)
        if ownership == "LIVE":
            try:
                os.killpg(group, signal.SIGKILL)
                sent_kill = True
            except ProcessLookupError:
                return sent_term, sent_kill
        else:
            raise OperatorError(
                f"cannot prove process group {group} is safe to release for process "
                f"{row['process_id']}: ownership is {ownership}"
            )

    kill_deadline = time.monotonic() + grace_seconds
    while time.monotonic() < kill_deadline:
        if not _process_group_alive(group):
            return sent_term, sent_kill
        time.sleep(min(0.02, max(0.0, kill_deadline - time.monotonic())))
    if _process_group_alive(group):
        ownership = process_ownership_state(store, row)
        raise OperatorError(
            f"process group {group} for process {row['process_id']} remains alive after SIGKILL; "
            f"ownership is {ownership}; refusing cancellation closeout"
        )
    return sent_term, sent_kill


def _cancelled_process_is_remote_transport(row: Any) -> tuple[bool, str]:
    try:
        command = json.loads(row["command_json"])
    except (TypeError, json.JSONDecodeError):
        return True, "process-command-unavailable"
    if not isinstance(command, list) or not command or any(not isinstance(item, str) for item in command):
        return True, "process-command-unavailable"

    executable = command[0]
    for index in range(len(command) - 2, -1, -1):
        if command[index] == "--" and index + 1 < len(command):
            executable = command[index + 1]
            break
    ssh_transport_shape = (
        "-T" in command
        and "BatchMode=yes" in command
        and len(command) >= 2
        and command[-2:] == ["sh", "-s"]
    )
    if Path(executable).name == "ssh" or ssh_transport_shape:
        return True, "remote-ssh-transport"
    return False, "local-process-terminated"


def cancel_task(store: Store, task_id: str, *, grace_seconds: float = 5.0) -> dict[str, Any]:
    if grace_seconds <= 0:
        raise OperatorError("cancel grace_seconds must be positive")
    task = store.get_task(task_id)
    if task.stage in {WorkflowStage.DONE.value, WorkflowStage.FAILED.value, WorkflowStage.CANCELLED.value}:
        return {"action": "already-terminal", "status": task_status(store, task_id)}

    rows = store._conn.execute(
        "SELECT * FROM processes WHERE task_id=? AND state='RUNNING' ORDER BY process_id",
        (task_id,),
    ).fetchall()

    # Fail before sending any signal if a numeric PID/PGID now belongs to another process or
    # if this is a legacy row for which durable identity was never captured.
    for row in rows:
        ownership = process_ownership_state(store, row)
        if ownership in {"MISMATCH", "UNVERIFIED"}:
            raise OperatorError(
                f"cannot safely cancel process {row['process_id']}: ownership is {ownership}"
            )

    terminated: list[dict[str, Any]] = []
    cancelled_processes_by_attempt: dict[int, Any] = {}
    for row in rows:
        sent_term, sent_kill = _terminate_owned_process_group(store, row, grace_seconds)
        now = utc_now()
        with store._transaction():
            store._conn.execute(
                """
                UPDATE processes
                SET state='CANCELLED',ended_at=?,last_liveness_at=?
                WHERE process_id=? AND state='RUNNING'
                """,
                (now, now, row["process_id"]),
            )
        attempt_id = row["attempt_id"]
        if attempt_id is not None:
            attempt_key = int(attempt_id)
            cancelled_processes_by_attempt[attempt_key] = row
            attempt = store.get_attempt(attempt_key)
            if attempt.ended_at is None:
                store.finish_attempt(
                    attempt_id=attempt_key,
                    status="CANCELLED",
                    result={"reason": "operator cancellation"},
                    exit_status=None,
                )
        terminated.append(
            {
                "process_id": int(row["process_id"]),
                "pid": int(row["pid"]),
                "sigterm_sent": sent_term,
                "sigkill_sent": sent_kill,
            }
        )

    released_leases: list[dict[str, Any]] = []
    retained_leases: list[dict[str, Any]] = []
    for lease in active_leases(store):
        if lease.task_id != task_id:
            continue
        process_row = (
            cancelled_processes_by_attempt.get(int(lease.attempt_id))
            if lease.attempt_id is not None
            else None
        )
        if process_row is None:
            retained_leases.append(
                {
                    "lease_id": lease.lease_id,
                    "resource": lease.resource_name,
                    "attempt_id": lease.attempt_id,
                    "reason": "no-cancelled-process-proof",
                }
            )
            continue
        remote, reason = _cancelled_process_is_remote_transport(process_row)
        if remote:
            retained_leases.append(
                {
                    "lease_id": lease.lease_id,
                    "resource": lease.resource_name,
                    "attempt_id": lease.attempt_id,
                    "reason": reason,
                }
            )
            continue
        released = release_lease(
            store,
            lease_id=lease.lease_id,
            holder_id=lease.holder_id,
        )
        released_leases.append(
            {
                "lease_id": released.lease_id,
                "resource": released.resource_name,
                "attempt_id": released.attempt_id,
                "reason": reason,
            }
        )

    final = WorkflowStateMachine(store).transition(
        task_id,
        WorkflowStage.CANCELLED,
        event_type="task_cancelled",
        payload={
            "operator": True,
            "terminated_processes": len(terminated),
            "released_leases": len(released_leases),
            "retained_leases": len(retained_leases),
        },
    )
    return {
        "action": "cancelled",
        "terminated_processes": terminated,
        "released_leases": released_leases,
        "retained_leases": retained_leases,
        "stage": final.stage,
        "status": task_status(store, task_id),
    }


def _which(executable: str | None, fallback: str) -> str | None:
    candidate = executable or fallback
    if os.path.sep in candidate:
        path = Path(candidate).expanduser()
        return str(path.resolve()) if path.is_file() and os.access(path, os.X_OK) else None
    return shutil.which(candidate)


def _run_probe(argv: Sequence[str], *, timeout: float = 5.0) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "reason": type(exc).__name__}
    return {
        "ok": completed.returncode == 0,
        "exit_status": completed.returncode,
        "output": (completed.stdout or completed.stderr).strip()[:500],
    }


def _provider_check(name: str, provider: ProviderConfig) -> dict[str, Any]:
    if provider.provider == "simulated":
        return {"role": name, "provider": provider.provider, "executable": "built-in", "ok": True, "auth": "not-required"}
    executable = _which(provider.executable, provider.provider)
    result: dict[str, Any] = {
        "role": name,
        "provider": provider.provider,
        "executable": executable,
        "ok": executable is not None,
    }
    if executable is None:
        result["reason"] = "executable-not-found"
        result["auth"] = "unknown"
        return result
    raw_auth = provider.options.get("auth_check_argv")
    if raw_auth is None:
        result["auth"] = "not-configured"
        result["warning"] = "provider auth check argv is not configured"
        return result
    if not isinstance(raw_auth, list) or any(not isinstance(item, str) or not item for item in raw_auth):
        result["ok"] = False
        result["auth"] = "invalid-check-config"
        return result
    probe = _run_probe([executable, *raw_auth])
    result["auth"] = "usable" if probe["ok"] else "unusable"
    result["auth_probe"] = {key: value for key, value in probe.items() if key != "output"}
    result["ok"] = bool(result["ok"] and probe["ok"])
    return result


def doctor(
    context: RuntimeContext,
    *,
    task: TaskSpec | None = None,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    python_ok = sys.version_info >= (3, 12)
    checks.append(
        {
            "name": "python",
            "ok": python_ok,
            "version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        }
    )
    git = shutil.which("git")
    checks.append({"name": "git", "ok": git is not None, "executable": git})

    try:
        context.state_dir.mkdir(parents=True, exist_ok=True)
        probe = context.state_dir / f".doctor-{uuid.uuid4().hex}.tmp"
        probe.write_text("ok\n", encoding="utf-8")
        probe.unlink()
        with Store(context.database_path) as store:
            initialize_configured_resources(store, context.config)
        state_ok = True
        state_reason = None
    except (OSError, sqlite3.Error, StoreError) as exc:
        state_ok = False
        state_reason = type(exc).__name__
    checks.append(
        {
            "name": "state",
            "ok": state_ok,
            "state_dir": str(context.state_dir),
            "database": str(context.database_path),
            "reason": state_reason,
        }
    )

    providers: list[dict[str, Any]] = []
    runners: list[dict[str, Any]] = []
    if context.config is not None:
        providers = [
            _provider_check("writer", context.config.writer),
            _provider_check("reviewer", context.config.reviewer),
        ]
        for name, runner in context.config.runners.items():
            executable_name = runner.executable or ("ssh" if runner.kind == "ssh" else "python")
            executable = _which(executable_name, executable_name)
            runners.append(
                {
                    "name": name,
                    "kind": runner.kind,
                    "ok": executable is not None,
                    "executable": executable,
                    "host_configured": runner.host is not None if runner.kind == "ssh" else None,
                }
            )

    task_checks: list[dict[str, Any]] = []
    if task is not None:
        try:
            _validate_task_references(task, context.config)
            references_ok = True
            references_reason = None
        except ConfigError as exc:
            references_ok = False
            references_reason = str(exc)
        task_checks.append({"name": "config-references", "ok": references_ok, "reason": references_reason})
        repository = Path(task.repository).expanduser()
        repository_ok = repository.is_dir() and git is not None
        baseline_ok = False
        if repository_ok:
            probe = _run_probe([git, "-C", str(repository), "rev-parse", "--verify", f"{task.baseline}^{{commit}}"])
            baseline_ok = bool(probe["ok"])
        task_checks.extend(
            [
                {"name": "repository", "ok": repository_ok, "path": str(repository)},
                {"name": "baseline", "ok": baseline_ok, "ref": task.baseline},
            ]
        )
        if task.workspace.mode == "existing":
            writer_path = Path(task.workspace.writer_path or "").expanduser()
            task_checks.append(
                {"name": "existing-writer-workspace", "ok": writer_path.is_dir(), "path": str(writer_path)}
            )

    required = checks + runners + task_checks
    provider_required = [item for item in providers if item.get("auth") != "not-configured"]
    ok = all(bool(item.get("ok")) for item in required + provider_required)
    warnings = sum(1 for item in providers if item.get("warning"))
    return {
        "status": "ok" if ok else "failed",
        "ok": ok,
        "warnings": warnings,
        "checks": checks,
        "providers": providers,
        "runners": runners,
        "task_checks": task_checks,
    }
