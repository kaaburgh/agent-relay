from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


class ConfigError(ValueError):
    """Raised when a task or global configuration is invalid."""


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigError(f"{path} must be a mapping")
    return value


def _required_str(data: Mapping[str, Any], key: str, path: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{path}.{key} must be a non-empty string")
    return value


def _optional_str(data: Mapping[str, Any], key: str, path: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{path}.{key} must be a non-empty string when set")
    return value


def _string_list(value: Any, path: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    if value is None:
        values: list[Any] = []
    elif isinstance(value, list):
        values = value
    else:
        raise ConfigError(f"{path} must be a list of strings")
    if not allow_empty and not values:
        raise ConfigError(f"{path} must contain at least one item")
    result: list[str] = []
    for index, item in enumerate(values):
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(f"{path}[{index}] must be a non-empty string")
        result.append(item)
    return tuple(result)


@dataclass(frozen=True)
class ProviderConfig:
    provider: str
    executable: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    options: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: Any, path: str) -> "ProviderConfig":
        data = _mapping(value, path)
        provider = _required_str(data, "provider", path)
        options = data.get("options", {})
        if not isinstance(options, Mapping):
            raise ConfigError(f"{path}.options must be a mapping")
        return cls(
            provider=provider,
            executable=_optional_str(data, "executable", path),
            model=_optional_str(data, "model", path),
            reasoning_effort=_optional_str(data, "reasoning_effort", path),
            options=dict(options),
        )


@dataclass(frozen=True)
class RunnerConfig:
    kind: str
    executable: str | None = None
    host: str | None = None
    user: str | None = None
    base_dir: str | None = None
    options: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: Any, path: str) -> "RunnerConfig":
        data = _mapping(value, path)
        kind = _required_str(data, "kind", path)
        if kind not in {"local", "ssh"}:
            raise ConfigError(f"{path}.kind must be 'local' or 'ssh'")
        host = _optional_str(data, "host", path)
        if kind == "ssh" and host is None:
            raise ConfigError(f"{path}.host is required for ssh runners")
        options = data.get("options", {})
        if not isinstance(options, Mapping):
            raise ConfigError(f"{path}.options must be a mapping")
        return cls(
            kind=kind,
            executable=_optional_str(data, "executable", path),
            host=host,
            user=_optional_str(data, "user", path),
            base_dir=_optional_str(data, "base_dir", path),
            options=dict(options),
        )


@dataclass(frozen=True)
class ResourceConfig:
    capacity: int = 1

    @classmethod
    def from_mapping(cls, value: Any, path: str) -> "ResourceConfig":
        data = _mapping(value, path)
        capacity = data.get("capacity", 1)
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
            raise ConfigError(f"{path}.capacity must be a positive integer")
        return cls(capacity=capacity)


@dataclass(frozen=True)
class GlobalConfig:
    writer: ProviderConfig
    reviewer: ProviderConfig
    runners: Mapping[str, RunnerConfig] = field(default_factory=dict)
    resources: Mapping[str, ResourceConfig] = field(default_factory=dict)
    state_dir: Path = Path(".agent-relay")

    @classmethod
    def from_mapping(cls, value: Any) -> "GlobalConfig":
        data = _mapping(value, "config")
        writer = ProviderConfig.from_mapping(data.get("writer"), "config.writer")
        reviewer = ProviderConfig.from_mapping(data.get("reviewer"), "config.reviewer")

        raw_runners = data.get("runners", {})
        if not isinstance(raw_runners, Mapping):
            raise ConfigError("config.runners must be a mapping")
        runners = {
            str(name): RunnerConfig.from_mapping(raw, f"config.runners.{name}")
            for name, raw in raw_runners.items()
        }

        raw_resources = data.get("resources", {})
        if not isinstance(raw_resources, Mapping):
            raise ConfigError("config.resources must be a mapping")
        resources = {
            str(name): ResourceConfig.from_mapping(raw, f"config.resources.{name}")
            for name, raw in raw_resources.items()
        }

        state_dir_value = data.get("state_dir", ".agent-relay")
        if not isinstance(state_dir_value, str) or not state_dir_value.strip():
            raise ConfigError("config.state_dir must be a non-empty string")
        return cls(
            writer=writer,
            reviewer=reviewer,
            runners=runners,
            resources=resources,
            state_dir=Path(state_dir_value),
        )


@dataclass(frozen=True)
class ValidationStep:
    name: str
    runner: str
    argv: tuple[str, ...]
    cwd: str | None = None
    timeout_seconds: float | None = None
    resources: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: Any, path: str) -> "ValidationStep":
        data = _mapping(value, path)
        argv = _string_list(data.get("argv"), f"{path}.argv", allow_empty=False)
        timeout = data.get("timeout_seconds")
        if timeout is not None:
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
                raise ConfigError(f"{path}.timeout_seconds must be a positive number")
            timeout = float(timeout)
        raw_env = data.get("env", {})
        if not isinstance(raw_env, Mapping) or any(
            not isinstance(k, str) or not isinstance(v, str) for k, v in raw_env.items()
        ):
            raise ConfigError(f"{path}.env must be a string-to-string mapping")
        return cls(
            name=_required_str(data, "name", path),
            runner=_required_str(data, "runner", path),
            argv=argv,
            cwd=_optional_str(data, "cwd", path),
            timeout_seconds=timeout,
            resources=_string_list(data.get("resources"), f"{path}.resources"),
            env=dict(raw_env),
        )


@dataclass(frozen=True)
class WorkspacePolicy:
    mode: str = "managed"
    writer_path: str | None = None

    @classmethod
    def from_mapping(cls, value: Any, path: str = "task.workspace") -> "WorkspacePolicy":
        if value is None:
            return cls()
        data = _mapping(value, path)
        mode = data.get("mode", "managed")
        if mode not in {"managed", "existing"}:
            raise ConfigError(f"{path}.mode must be 'managed' or 'existing'")
        writer_path = _optional_str(data, "writer_path", path)
        if mode == "existing" and writer_path is None:
            raise ConfigError(f"{path}.writer_path is required when mode is existing")
        return cls(mode=mode, writer_path=writer_path)


@dataclass(frozen=True)
class TaskSpec:
    repository: str
    baseline: str
    writer_instructions: str
    validation: tuple[ValidationStep, ...]
    review_instructions: str
    acceptance_criteria: tuple[str, ...]
    runtime_resources: tuple[str, ...] = ()
    max_correction_rounds: int = 3
    workspace: WorkspacePolicy = field(default_factory=WorkspacePolicy)
    task_id: str | None = None

    @classmethod
    def from_mapping(cls, value: Any) -> "TaskSpec":
        data = _mapping(value, "task")
        raw_validation = data.get("validation")
        if not isinstance(raw_validation, list) or not raw_validation:
            raise ConfigError("task.validation must contain at least one validation step")
        max_rounds = data.get("max_correction_rounds", 3)
        if isinstance(max_rounds, bool) or not isinstance(max_rounds, int) or max_rounds < 0:
            raise ConfigError("task.max_correction_rounds must be a non-negative integer")
        return cls(
            task_id=_optional_str(data, "task_id", "task"),
            repository=_required_str(data, "repository", "task"),
            baseline=_required_str(data, "baseline", "task"),
            writer_instructions=_required_str(data, "writer_instructions", "task"),
            validation=tuple(
                ValidationStep.from_mapping(item, f"task.validation[{index}]")
                for index, item in enumerate(raw_validation)
            ),
            review_instructions=_required_str(data, "review_instructions", "task"),
            acceptance_criteria=_string_list(
                data.get("acceptance_criteria"), "task.acceptance_criteria", allow_empty=False
            ),
            runtime_resources=_string_list(data.get("runtime_resources"), "task.runtime_resources"),
            max_correction_rounds=max_rounds,
            workspace=WorkspacePolicy.from_mapping(data.get("workspace")),
        )
