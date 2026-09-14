from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .models import GlobalConfig, TaskSpec
from .operator import OperatorError, RuntimeContext, task_status
from .orchestrator import OrchestrationError, SimulationOrchestrator
from .store import Store
from .workflow import WorkflowStage


@dataclass(frozen=True)
class RunResult:
    backend: str
    task_id: str
    stage: str
    candidate_sha: str | None
    generation: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "task_id": self.task_id,
            "stage": self.stage,
            "candidate_sha": self.candidate_sha,
            "generation": self.generation,
        }


def _mapping_list(value: Any, *, path: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list) or not value:
        raise OperatorError(f"{path} must be a non-empty list of mappings")
    result: list[Mapping[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise OperatorError(f"{path}[{index}] must be a mapping")
        result.append(dict(item))
    return result


def _simulation_writer_behavior(config: GlobalConfig, task_id: str) -> Sequence[Mapping[str, Any]]:
    configured = config.writer.options.get("behavior")
    if configured is not None:
        return _mapping_list(configured, path="config.writer.options.behavior")
    marker = f".agent-relay-simulation-{task_id}.txt"
    return [
        {
            "modify_file": {
                "path": marker,
                "content": f"agent-relay simulated candidate for {task_id}\n",
            }
        },
        {"commit": {"message": f"agent-relay simulation candidate for {task_id}"}},
        {"result": {"status": "success", "handoff": "simulation default"}},
    ]


def _simulation_reviewer_behavior(config: GlobalConfig) -> Sequence[Mapping[str, Any]]:
    configured = config.reviewer.options.get("behavior")
    if configured is not None:
        return _mapping_list(configured, path="config.reviewer.options.behavior")
    return [
        {
            "action": "review",
            "value": {
                "verdict": "APPROVE",
                "findings": [],
                "summary": "simulation reviewer approved deterministic candidate",
            },
        }
    ]


def _validate_simulation_contract(task: TaskSpec, config: GlobalConfig) -> tuple[int, Mapping[str, Any] | None]:
    if config.writer.provider != "simulated" or config.reviewer.provider != "simulated":
        raise OperatorError(
            "simulation backend requires writer.provider=simulated and reviewer.provider=simulated"
        )
    if len(task.validation) != 1:
        raise OperatorError("simulation backend currently requires exactly one validation step")
    step = task.validation[0]
    runner = config.runners.get(step.runner)
    if runner is None:
        raise OperatorError(f"simulation validation runner is not configured: {step.runner}")
    if runner.options.get("simulated_validator") is not True:
        raise OperatorError(
            f"runner {step.runner!r} is not explicitly marked options.simulated_validator=true"
        )
    cycles = runner.options.get("requested_cycles", 3)
    if isinstance(cycles, bool) or not isinstance(cycles, int) or cycles <= 0:
        raise OperatorError("simulation runner requested_cycles must be a positive integer")
    behavior = runner.options.get("behavior")
    if behavior is not None and not isinstance(behavior, Mapping):
        raise OperatorError("simulation runner behavior must be a mapping")
    return cycles, dict(behavior) if behavior is not None else None


async def _run_simulation_async(
    *,
    context: RuntimeContext,
    store: Store,
    task_id: str,
) -> RunResult:
    if context.config is None:
        raise OperatorError("run --simulation requires --config")
    row = store.get_task(task_id)
    if row.stage != WorkflowStage.READY.value:
        raise OperatorError(f"simulation run requires READY task, found {row.stage}")
    task = TaskSpec.from_mapping(row.task_json)
    cycles, validation_behavior = _validate_simulation_contract(task, context.config)
    runtime_root = context.state_dir / "runtime" / task_id
    orchestrator = SimulationOrchestrator(store=store, runtime_root=runtime_root)
    try:
        result = await orchestrator.run_happy_path(
            task_id,
            writer_behavior=_simulation_writer_behavior(context.config, task_id),
            validation_cycles=cycles,
            validation_behavior=validation_behavior,
            reviewer_behavior=_simulation_reviewer_behavior(context.config),
        )
    except OrchestrationError as exc:
        raise OperatorError(str(exc)) from exc
    return RunResult(
        backend="simulation",
        task_id=task_id,
        stage=result.task.stage,
        candidate_sha=result.candidate.candidate_sha,
        generation=result.candidate.generation,
    )


def run_task(
    context: RuntimeContext,
    task_id: str,
    *,
    simulation: bool = False,
) -> dict[str, Any]:
    context.state_dir.mkdir(parents=True, exist_ok=True)
    with Store(context.database_path) as store:
        row = store.get_task(task_id)
        if not simulation:
            provider = context.config.writer.provider if context.config is not None else None
            return {
                "action": "backend-not-installed",
                "provider": provider,
                "task_id": task_id,
                "stage": row.stage,
                "reason": "real provider execution is added by R21/R22; use --simulation only with explicit simulated configuration",
            }
        result = asyncio.run(
            _run_simulation_async(context=context, store=store, task_id=task_id)
        )
        payload = result.to_dict()
        payload["status"] = task_status(store, task_id)
        return payload
