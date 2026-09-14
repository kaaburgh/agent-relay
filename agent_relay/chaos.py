from __future__ import annotations

import asyncio
import json
import os
import random
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .artifacts import ArtifactManager
from .evidence import latest_review, latest_validation, record_validation
from .git_workspace import GitWorkspaceManager, candidate_generations, record_candidate_generation
from .orchestrator import OrchestrationError, SimulationOrchestrator
from .provider_retry import clear_provider_wait, record_provider_unavailable, resume_provider_if_due
from .simulated_validator import ValidationResultKind
from .simulated_writer import WriterResultKind
from .store import Store, StoreError, utc_now
from .workflow import WorkflowStage, WorkflowStateMachine
from .writer_recovery import reconcile_writer_attempt


CHAOS_MODES = (
    "happy",
    "review_changes",
    "writer_crash",
    "provider_wait",
    "validation_failure",
    "validation_incomplete",
    "malformed_review",
    "delayed_result",
    "writer_restart",
    "tool_crash",
)

APPROVE = {"verdict": "APPROVE", "findings": [], "summary": "chaos approval"}
REQUEST_CHANGES = {
    "verdict": "REQUEST_CHANGES",
    "findings": [
        {
            "severity": "HIGH",
            "title": "chaos correction",
            "problem": "first generation needs correction",
            "failure_scenario": "acceptance marker is stale",
            "required_action": "write a corrected generation",
        }
    ],
    "summary": "one correction required",
}


class ChaosFailure(RuntimeError):
    pass


@dataclass(frozen=True)
class ChaosSummary:
    seed: int
    workflows: int
    mode_counts: Mapping[str, int]
    report_path: Path


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ChaosFailure(f"git failed {args!r}: {completed.stderr.strip()}")
    return completed.stdout.strip()


def _init_repo(root: Path) -> Path:
    repo = root / "source"
    repo.mkdir(parents=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "agent-relay-chaos@example.invalid")
    _git(repo, "config", "user.name", "Agent Relay Chaos")
    (repo / "example.txt").write_text("baseline\n", encoding="utf-8")
    _git(repo, "add", "example.txt")
    _git(repo, "commit", "-m", "baseline")
    return repo


def _writer_behavior(number: int, *, delayed_result: float = 0.0) -> list[dict[str, Any]]:
    behavior: list[dict[str, Any]] = [
        {"modify_file": {"path": "example.txt", "content": f"chaos candidate {number}\n"}},
        {"commit": {"message": f"chaos candidate {number}"}},
    ]
    if delayed_result:
        behavior.append({"sleep": delayed_result})
    behavior.append({"result": {"status": "success"}})
    return behavior


def _write_report(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(dict(value), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def assert_store_invariants(store: Store, task_id: str) -> None:
    task = store.get_task(task_id)
    generations = candidate_generations(store, task_id)
    expected_generations = list(range(1, len(generations) + 1))
    if [item.generation for item in generations] != expected_generations:
        raise ChaosFailure("candidate generations are not contiguous")

    if task.current_generation == 0:
        if task.current_candidate_sha is not None or generations:
            raise ChaosFailure("generation-zero task has a candidate pointer/history")
    else:
        if len(generations) != task.current_generation:
            raise ChaosFailure("task generation pointer disagrees with candidate history")
        current = generations[-1]
        if current.candidate_sha != task.current_candidate_sha:
            raise ChaosFailure("task candidate SHA disagrees with current generation")

    candidate_by_generation = {item.generation: item.candidate_sha for item in generations}
    for table in ("validations", "reviews"):
        rows = store._conn.execute(
            f"SELECT generation,candidate_sha FROM {table} WHERE task_id=?", (task_id,)
        ).fetchall()
        for row in rows:
            if candidate_by_generation.get(int(row["generation"])) != row["candidate_sha"]:
                raise ChaosFailure(f"{table} evidence is detached from frozen candidate provenance")

    if task.stage == WorkflowStage.REVIEW.value and not task.current_candidate_sha:
        raise ChaosFailure("REVIEW exists without frozen candidate")

    if task.stage == WorkflowStage.REWORK.value:
        review = latest_review(store, task_id, task.current_generation)
        if review is None or review.verdict != "REQUEST_CHANGES":
            raise ChaosFailure("REWORK exists without current REQUEST_CHANGES evidence")

    if task.stage == WorkflowStage.DONE.value:
        validation = latest_validation(store, task_id, task.current_generation)
        review = latest_review(store, task_id, task.current_generation)
        if validation is None or validation.status != "SUCCESS":
            raise ChaosFailure("DONE lacks successful current-generation validation")
        if validation.candidate_sha != task.current_candidate_sha:
            raise ChaosFailure("DONE validation is for stale candidate")
        if review is None or review.verdict not in {"APPROVE", "APPROVE_WITH_FOLLOWUPS"}:
            raise ChaosFailure("DONE lacks valid current-generation approval")
        if review.candidate_sha != task.current_candidate_sha:
            raise ChaosFailure("DONE review is for stale candidate")

    active_writers = store._conn.execute(
        "SELECT COUNT(*) FROM attempts WHERE task_id=? AND kind='writer' AND ended_at IS NULL",
        (task_id,),
    ).fetchone()[0]
    if int(active_writers) > 1:
        raise ChaosFailure("more than one writer attempt is active for one task")

    capacities = store._conn.execute("SELECT resource_name,capacity FROM resources").fetchall()
    for resource in capacities:
        active = store._conn.execute(
            "SELECT COUNT(*) FROM leases WHERE resource_name=? AND released_at IS NULL",
            (resource["resource_name"],),
        ).fetchone()[0]
        if int(active) > int(resource["capacity"]):
            raise ChaosFailure("active resource leases exceed configured capacity")

    candidate_events = sum(
        1 for event in store.events(task_id) if event.event_type == "candidate_commit_detected"
    )
    if candidate_events != len(generations):
        raise ChaosFailure("candidate event history disagrees with candidate generations")


def _new_task(store: Store, repo: Path, task_id: str) -> None:
    store.create_task(
        task_id=task_id,
        repository=str(repo),
        baseline_ref="main",
        task_spec={
            "repository": str(repo),
            "baseline": "main",
            "max_correction_rounds": 2,
        },
    )


async def _manual_candidate(
    orchestrator: SimulationOrchestrator,
    *,
    task_id: str,
    repo: Path,
    behavior: Sequence[Mapping[str, Any]],
):
    orchestrator.workflow.transition(task_id, WorkflowStage.WORK)
    workspaces = orchestrator.git.create_writer_worktree(
        task_id=task_id, repository=repo, baseline_ref="main"
    )
    result = await orchestrator.writer.run(
        task_id=task_id,
        writer_worktree=workspaces.writer,
        baseline_sha=workspaces.baseline_sha,
        behavior=behavior,
    )
    if result.kind != WriterResultKind.SUCCESS or not result.candidate_sha:
        raise ChaosFailure(f"candidate setup failed: {result.kind}: {result.reason}")
    candidate = record_candidate_generation(
        orchestrator.store,
        task_id=task_id,
        candidate_sha=result.candidate_sha,
        writer_attempt_id=result.attempt_id,
        expected_previous_generation=0,
    )
    return workspaces, candidate


async def _run_writer_restart(
    *,
    root: Path,
    db: Path,
    store: Store,
    repo: Path,
    task_id: str,
) -> Store:
    workflow = WorkflowStateMachine(store)
    workflow.transition(task_id, WorkflowStage.WORK)
    git = GitWorkspaceManager(root / "managed")
    workspaces = git.create_writer_worktree(task_id=task_id, repository=repo, baseline_ref="main")
    artifacts = ArtifactManager(root / "artifacts", store)
    behavior = [
        {"sleep": 0.01},
        {"modify_file": {"path": "example.txt", "content": "restart candidate\n"}},
        {"commit": {"message": "restart candidate"}},
        {"result": {"status": "success"}},
    ]
    layout = artifacts.create_attempt(
        task_id=task_id,
        kind="writer",
        inputs={"behavior": behavior, "baseline_sha": workspaces.baseline_sha},
        command=["chaos-detached-writer"],
    )
    script = layout.directory / "writer-behavior.json"
    provider_result = layout.directory / "provider-result.json"
    script.write_text(json.dumps(behavior), encoding="utf-8")
    out = layout.stdout_path.open("ab", buffering=0)
    err = layout.stderr_path.open("ab", buffering=0)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "agent_relay.simulated_writer_worker",
            "--script",
            str(script),
            "--worktree",
            str(workspaces.writer),
            "--result",
            str(provider_result),
        ],
        cwd=workspaces.writer,
        stdout=out,
        stderr=err,
        start_new_session=True,
    )
    out.close()
    err.close()
    launched = utc_now()
    with store._transaction():
        store._conn.execute(
            """
            INSERT INTO processes(
                task_id,attempt_id,pid,process_group_id,state,command_json,
                started_at,last_liveness_at
            ) VALUES (?,?,?,?, 'RUNNING', ?, ?, ?)
            """,
            (
                task_id,
                layout.attempt.attempt_id,
                process.pid,
                os.getpgid(process.pid),
                json.dumps(["chaos-detached-writer"]),
                launched,
                launched,
            ),
        )

    live = reconcile_writer_attempt(
        store,
        artifact_root=artifacts.root,
        git=git,
        task_id=task_id,
        writer_worktree=workspaces.writer,
        baseline_sha=workspaces.baseline_sha,
        attempt_id=layout.attempt.attempt_id,
        expected_previous_generation=0,
    )
    if live.action != "RUNNING":
        raise ChaosFailure(f"restart precondition was not live: {live.action}")

    store.close()
    await asyncio.to_thread(process.wait, 10)
    if process.returncode != 0:
        raise ChaosFailure(f"detached writer exit {process.returncode}")
    reopened = Store(db)
    recovered = reconcile_writer_attempt(
        reopened,
        artifact_root=artifacts.root,
        git=git,
        task_id=task_id,
        writer_worktree=workspaces.writer,
        baseline_sha=workspaces.baseline_sha,
        attempt_id=layout.attempt.attempt_id,
        expected_previous_generation=0,
    )
    if recovered.action != "RECOVERED":
        reopened.close()
        raise ChaosFailure(f"writer restart did not recover: {recovered.action}")
    WorkflowStateMachine(reopened).transition(task_id, WorkflowStage.VALIDATE)
    WorkflowStateMachine(reopened).transition(task_id, WorkflowStage.FAILED)
    return reopened


async def _execute_mode(
    *,
    mode: str,
    root: Path,
    db: Path,
    store: Store,
    repo: Path,
    task_id: str,
    rng: random.Random,
) -> Store:
    orchestrator = SimulationOrchestrator(store=store, runtime_root=root / "runtime")

    if mode == "happy":
        result = await orchestrator.run_happy_path(
            task_id,
            writer_behavior=_writer_behavior(1),
            validation_cycles=1,
            reviewer_behavior=[{"action": "review", "value": APPROVE}],
        )
        if result.task.stage != WorkflowStage.DONE.value:
            raise ChaosFailure("happy mode did not reach DONE")
        return store

    if mode == "review_changes":
        result = await orchestrator.run_correction_sequence(
            task_id,
            writer_behaviors=[_writer_behavior(1), _writer_behavior(2)],
            reviewer_behaviors=[
                [{"action": "review", "value": REQUEST_CHANGES}],
                [{"action": "review", "value": APPROVE}],
            ],
            validation_cycles=1,
        )
        if result.task.stage != WorkflowStage.DONE.value or len(result.candidates) != 2:
            raise ChaosFailure("review_changes mode did not preserve two generations and finish")
        return store

    if mode == "writer_crash":
        orchestrator.workflow.transition(task_id, WorkflowStage.WORK)
        workspaces = orchestrator.git.create_writer_worktree(
            task_id=task_id, repository=repo, baseline_ref="main"
        )
        result = await orchestrator.writer.run(
            task_id=task_id,
            writer_worktree=workspaces.writer,
            baseline_sha=workspaces.baseline_sha,
            behavior=[{"crash": 23}],
        )
        if result.kind != WriterResultKind.PROCESS_FAILURE or result.candidate_sha is not None:
            raise ChaosFailure("writer crash was not classified fail-closed")
        orchestrator.workflow.transition(task_id, WorkflowStage.FAILED)
        return store

    if mode == "provider_wait":
        orchestrator.workflow.transition(task_id, WorkflowStage.WORK)
        workspaces = orchestrator.git.create_writer_worktree(
            task_id=task_id, repository=repo, baseline_ref="main"
        )
        unavailable = await orchestrator.writer.run(
            task_id=task_id,
            writer_worktree=workspaces.writer,
            baseline_sha=workspaces.baseline_sha,
            behavior=[{"provider_unavailable": "chaos quota"}],
        )
        if unavailable.kind != WriterResultKind.PROVIDER_UNAVAILABLE:
            raise ChaosFailure("provider_wait injection did not report provider unavailability")
        t0 = datetime(2026, 9, 14, 8, 0, tzinfo=timezone.utc) + timedelta(
            seconds=rng.randint(0, 1000)
        )
        record_provider_unavailable(
            store,
            task_id=task_id,
            provider="simulated-writer",
            reason=unavailable.reason or "chaos unavailable",
            now=t0,
            base_delay_seconds=1,
            max_delay_seconds=4,
        )
        if resume_provider_if_due(store, task_id=task_id, now=t0 + timedelta(milliseconds=500)):
            raise ChaosFailure("provider retry ignored next_retry")
        if not resume_provider_if_due(store, task_id=task_id, now=t0 + timedelta(seconds=1)):
            raise ChaosFailure("provider retry did not resume when due")
        success = await orchestrator.writer.run(
            task_id=task_id,
            writer_worktree=workspaces.writer,
            baseline_sha=workspaces.baseline_sha,
            behavior=_writer_behavior(1),
        )
        if success.kind != WriterResultKind.SUCCESS or not success.candidate_sha:
            raise ChaosFailure("provider did not recover on scheduled retry")
        clear_provider_wait(
            store,
            task_id=task_id,
            provider="simulated-writer",
            now=t0 + timedelta(seconds=2),
        )
        record_candidate_generation(
            store,
            task_id=task_id,
            candidate_sha=success.candidate_sha,
            writer_attempt_id=success.attempt_id,
            expected_previous_generation=0,
        )
        orchestrator.workflow.transition(task_id, WorkflowStage.VALIDATE)
        orchestrator.workflow.transition(task_id, WorkflowStage.FAILED)
        return store

    if mode in {"validation_failure", "tool_crash"}:
        workspaces, candidate = await _manual_candidate(
            orchestrator,
            task_id=task_id,
            repo=repo,
            behavior=_writer_behavior(1),
        )
        orchestrator.workflow.transition(task_id, WorkflowStage.VALIDATE)
        behavior = {"fail_cycle": 1} if mode == "validation_failure" else {"crash_cycle": 1}
        validation_result = await orchestrator.validator.run(
            task_id=task_id,
            cwd=workspaces.writer,
            generation=candidate.generation,
            candidate_sha=candidate.candidate_sha,
            requested_cycles=1,
            behavior=behavior,
        )
        if validation_result.kind == ValidationResultKind.SUCCESS:
            raise ChaosFailure(f"{mode} unexpectedly passed")
        record_validation(
            store,
            task_id=task_id,
            generation=candidate.generation,
            candidate_sha=candidate.candidate_sha,
            attempt_id=validation_result.attempt_id,
            status=validation_result.kind.value,
            result={"run_id": validation_result.run_id, "reason": validation_result.reason},
        )
        orchestrator.workflow.transition(task_id, WorkflowStage.FAILED)
        return store

    if mode == "validation_incomplete":
        try:
            await orchestrator.run_happy_path(
                task_id,
                writer_behavior=_writer_behavior(1),
                validation_cycles=2,
                validation_behavior={"incomplete_cycles": 1},
                reviewer_behavior=[{"action": "review", "value": APPROVE}],
            )
        except OrchestrationError:
            pass
        if store.get_task(task_id).stage != WorkflowStage.BLOCKED.value:
            raise ChaosFailure("incomplete validation did not block")
        return store

    if mode == "malformed_review":
        try:
            await orchestrator.run_happy_path(
                task_id,
                writer_behavior=_writer_behavior(1),
                validation_cycles=1,
                reviewer_behavior=[{"action": "raw", "text": "chaos malformed approval"}],
            )
        except OrchestrationError:
            pass
        if store.get_task(task_id).stage != WorkflowStage.BLOCKED.value:
            raise ChaosFailure("malformed review did not block")
        return store

    if mode == "delayed_result":
        delay = rng.uniform(0.005, 0.02)
        _workspaces, _candidate = await _manual_candidate(
            orchestrator,
            task_id=task_id,
            repo=repo,
            behavior=_writer_behavior(1, delayed_result=delay),
        )
        orchestrator.workflow.transition(task_id, WorkflowStage.VALIDATE)
        orchestrator.workflow.transition(task_id, WorkflowStage.FAILED)
        return store

    if mode == "writer_restart":
        return await _run_writer_restart(
            root=root,
            db=db,
            store=store,
            repo=repo,
            task_id=task_id,
        )

    raise ChaosFailure(f"unknown chaos mode: {mode}")


async def run_chaos(
    root: str | Path,
    *,
    workflows: int = 100,
    seed: int = 20260914,
) -> ChaosSummary:
    if workflows < len(CHAOS_MODES):
        raise ValueError(f"workflows must be at least {len(CHAOS_MODES)} to cover every injection mode")
    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)
    report_path = root_path / "chaos-report.json"
    rng = random.Random(seed)
    plan = list(CHAOS_MODES)
    while len(plan) < workflows:
        plan.append(rng.choice(CHAOS_MODES))
    rng.shuffle(plan)
    counts: Counter[str] = Counter()
    entries: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "seed": seed,
        "requested_workflows": workflows,
        "completed_workflows": 0,
        "mode_counts": {},
        "runs": entries,
    }
    _write_report(report_path, report)

    for index, mode in enumerate(plan):
        run_root = root_path / f"workflow-{index:03d}"
        run_root.mkdir()
        repo = _init_repo(run_root)
        db = run_root / "state.sqlite3"
        store = Store(db)
        task_id = f"chaos-{index:03d}"
        _new_task(store, repo, task_id)
        try:
            store = await _execute_mode(
                mode=mode,
                root=run_root,
                db=db,
                store=store,
                repo=repo,
                task_id=task_id,
                rng=rng,
            )
            assert_store_invariants(store, task_id)
            task = store.get_task(task_id)
            counts[mode] += 1
            entries.append(
                {
                    "index": index,
                    "mode": mode,
                    "stage": task.stage,
                    "generation": task.current_generation,
                    "events": len(store.events(task_id)),
                }
            )
            report["completed_workflows"] = index + 1
            report["mode_counts"] = dict(sorted(counts.items()))
            _write_report(report_path, report)
        except BaseException as exc:
            report["failure"] = {
                "index": index,
                "mode": mode,
                "type": type(exc).__name__,
                "message": str(exc),
            }
            report["mode_counts"] = dict(sorted(counts.items()))
            _write_report(report_path, report)
            raise ChaosFailure(
                f"chaos failure seed={seed} workflow={index} mode={mode}: {type(exc).__name__}: {exc}"
            ) from exc
        finally:
            try:
                store.close()
            except Exception:
                pass

    missing = set(CHAOS_MODES) - set(counts)
    if missing:
        raise ChaosFailure(f"chaos plan failed to exercise modes: {sorted(missing)}")
    report["completed"] = True
    _write_report(report_path, report)
    return ChaosSummary(
        seed=seed,
        workflows=workflows,
        mode_counts=dict(sorted(counts.items())),
        report_path=report_path,
    )
