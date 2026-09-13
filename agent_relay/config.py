from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from .models import ConfigError, GlobalConfig, TaskSpec


def _load_document(path: str | Path) -> Any:
    source = Path(path)
    if not source.is_file():
        raise ConfigError(f"configuration file does not exist: {source}")
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read configuration file {source}: {exc}") from exc
    try:
        if source.suffix.lower() == ".json":
            value = json.loads(text)
        else:
            value = yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot parse configuration file {source}: {exc}") from exc
    if value is None:
        raise ConfigError(f"configuration file is empty: {source}")
    return value


def load_global_config(path: str | Path) -> GlobalConfig:
    return GlobalConfig.from_mapping(_load_document(path))


def load_task_spec(path: str | Path) -> TaskSpec:
    return TaskSpec.from_mapping(_load_document(path))
