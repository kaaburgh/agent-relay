from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .store import AttemptRow, Store, StoreError, utc_now


_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_SENSITIVE_KEY = re.compile(
    r"(?:password|passwd|token|secret|api[_-]?key|authorization|cookie|private[_-]?key|access[_-]?key)",
    re.IGNORECASE,
)
_INLINE_SECRET = re.compile(
    r"(?i)\b(password|passwd|token|secret|api[_-]?key|authorization|cookie|private[_-]?key|access[_-]?key)=([^\s]+)"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")


class ArtifactError(StoreError):
    pass


@dataclass(frozen=True)
class AttemptLayout:
    attempt: AttemptRow
    directory: Path
    metadata_path: Path
    inputs_path: Path
    command_path: Path
    config_path: Path
    stdout_path: Path
    stderr_path: Path
    result_path: Path


def _safe_segment(value: str, label: str) -> str:
    if not _SAFE_SEGMENT.fullmatch(value) or value in {".", ".."}:
        raise ArtifactError(f"unsafe {label}: {value!r}")
    return value


def _redact_string(value: str) -> str:
    value = _INLINE_SECRET.sub(lambda match: f"{match.group(1)}=<redacted>", value)
    return _BEARER.sub("Bearer <redacted>", value)


def redact(value: Any, *, key: str | None = None) -> Any:
    """Return a JSON-compatible copy with obvious credentials removed."""
    if key is not None and _SENSITIVE_KEY.search(key):
        return "<redacted>"
    if isinstance(value, Mapping):
        return {str(item_key): redact(item_value, key=str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, (list, tuple)):
        result: list[Any] = []
        redact_next = False
        for item in value:
            if redact_next:
                result.append("<redacted>")
                redact_next = False
                continue
            if isinstance(item, str):
                option = item.lstrip("-")
                if item.startswith("-") and _SENSITIVE_KEY.fullmatch(option):
                    result.append(item)
                    redact_next = True
                    continue
            result.append(redact(item))
        return result
    if isinstance(value, str):
        return _redact_string(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _redact_string(str(value))


def _write_json_exclusive(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, ensure_ascii=False)
        stream.write("\n")


def _touch_exclusive(path: Path) -> None:
    with path.open("x", encoding="utf-8"):
        pass


class ArtifactManager:
    """Creates durable, non-reused attempt directories and immutable snapshots."""

    def __init__(self, root: str | Path, store: Store) -> None:
        self.root = Path(root)
        self.store = store

    def create_attempt(
        self,
        *,
        task_id: str,
        kind: str,
        inputs: Mapping[str, Any] | None = None,
        command: Any = None,
        config: Mapping[str, Any] | None = None,
        generation: int | None = None,
        candidate_sha: str | None = None,
    ) -> AttemptLayout:
        task_id = _safe_segment(task_id, "task id")
        kind = _safe_segment(kind, "attempt kind")
        if generation is not None and generation <= 0:
            raise ArtifactError("candidate generation must be positive when set")
        if candidate_sha is not None and not candidate_sha.strip():
            raise ArtifactError("candidate SHA must not be empty when set")

        safe_inputs = redact(dict(inputs or {}))
        safe_command = redact(command if command is not None else [])
        safe_config = redact(dict(config or {}))

        attempt = self.store.allocate_attempt(
            task_id=task_id,
            kind=kind,
            generation=generation,
            command=safe_command,
        )
        task_root = self.root / "tasks" / task_id
        attempt_dir = task_root / attempt.artifact_dir
        attempt_dir.parent.mkdir(parents=True, exist_ok=True)
        attempt_dir.mkdir(exist_ok=False)

        layout = AttemptLayout(
            attempt=attempt,
            directory=attempt_dir,
            metadata_path=attempt_dir / "attempt.json",
            inputs_path=attempt_dir / "inputs.json",
            command_path=attempt_dir / "command.json",
            config_path=attempt_dir / "config.json",
            stdout_path=attempt_dir / "stdout.log",
            stderr_path=attempt_dir / "stderr.log",
            result_path=attempt_dir / "result.json",
        )
        metadata = {
            "attempt_id": attempt.attempt_id,
            "attempt_no": attempt.attempt_no,
            "task_id": task_id,
            "kind": kind,
            "generation": generation,
            "candidate_sha": candidate_sha,
            "created_at": attempt.started_at or utc_now(),
        }
        _write_json_exclusive(layout.metadata_path, metadata)
        _write_json_exclusive(layout.inputs_path, safe_inputs)
        _write_json_exclusive(layout.command_path, safe_command)
        _write_json_exclusive(layout.config_path, safe_config)
        _touch_exclusive(layout.stdout_path)
        _touch_exclusive(layout.stderr_path)

        for artifact_kind, path in (
            ("attempt_metadata", layout.metadata_path),
            ("inputs", layout.inputs_path),
            ("command", layout.command_path),
            ("config", layout.config_path),
            ("stdout", layout.stdout_path),
            ("stderr", layout.stderr_path),
        ):
            self.store.register_artifact(
                task_id=task_id,
                attempt_id=attempt.attempt_id,
                kind=artifact_kind,
                path=str(path.relative_to(self.root)),
            )
        return layout

    def finalize_attempt(
        self,
        layout: AttemptLayout,
        *,
        status: str,
        result: Any,
        exit_status: int | None = None,
    ) -> AttemptRow:
        if not status.strip():
            raise ArtifactError("attempt status must not be empty")
        safe_result = redact(result)
        _write_json_exclusive(layout.result_path, safe_result)
        self.store.register_artifact(
            task_id=layout.attempt.task_id,
            attempt_id=layout.attempt.attempt_id,
            kind="result",
            path=str(layout.result_path.relative_to(self.root)),
        )
        return self.store.finish_attempt(
            attempt_id=layout.attempt.attempt_id,
            status=status,
            result=safe_result,
            exit_status=exit_status,
        )
