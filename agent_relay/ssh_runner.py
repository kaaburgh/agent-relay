from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence

from .models import RunnerConfig
from .supervisor import ManagedProcess, SubprocessSupervisor


class SSHRunnerError(ValueError):
    pass


_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_USER = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class SSHTransportPlan:
    transport_argv: tuple[str, ...]
    remote_script: str
    destination: str
    remote_cwd: str | None


def _nonempty_token(value: str, label: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise SSHRunnerError(f"{label} must be a non-empty string without NUL")
    return value


def _destination(config: RunnerConfig) -> str:
    if config.kind != "ssh":
        raise SSHRunnerError("SSHExternalToolRunner requires runner kind 'ssh'")
    host = _nonempty_token(config.host or "", "SSH host")
    if host.startswith("-") or any(character.isspace() for character in host):
        raise SSHRunnerError("SSH host must not start with '-' or contain whitespace")
    if "@" in host and config.user is not None:
        raise SSHRunnerError("SSH host must not contain '@' when user is configured separately")
    if config.user is None:
        return host
    user = _nonempty_token(config.user, "SSH user")
    if not _USER.fullmatch(user):
        raise SSHRunnerError("SSH user contains unsupported characters")
    return f"{user}@{host}"


def _ssh_extra_args(config: RunnerConfig) -> tuple[str, ...]:
    raw = config.options.get("ssh_args", ())
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)) or any(
        not isinstance(item, str) or not item or "\x00" in item for item in raw
    ):
        raise SSHRunnerError("runner.options.ssh_args must be a list of non-empty strings")
    return tuple(raw)


def _resolve_remote_cwd(base_dir: str | None, cwd: str | Path | None) -> str | None:
    base = PurePosixPath(base_dir) if base_dir else None
    if base is not None and not base.is_absolute():
        raise SSHRunnerError("SSH runner base_dir must be an absolute remote POSIX path")

    if cwd is None:
        return str(base) if base is not None else None
    raw = _nonempty_token(str(cwd), "remote cwd")
    path = PurePosixPath(raw)
    if ".." in path.parts:
        raise SSHRunnerError("remote cwd must not contain '..'")
    if base is None:
        return str(path)
    if path.is_absolute():
        try:
            path.relative_to(base)
        except ValueError as exc:
            raise SSHRunnerError("absolute remote cwd must remain under configured base_dir") from exc
        return str(path)
    return str(base / path)


def _quote_remote_argv(argv: Sequence[str]) -> str:
    if not argv or any(not isinstance(item, str) or not item or "\x00" in item for item in argv):
        raise SSHRunnerError("remote argv must contain non-empty strings without NUL")
    return " ".join(shlex.quote(item) for item in argv)


def build_remote_script(
    *,
    argv: Sequence[str],
    cwd: str | None,
    env_additions: Mapping[str, str] | None,
) -> str:
    lines = ["set -eu"]
    if cwd is not None:
        lines.append(f"cd -- {shlex.quote(cwd)}")
    for name, value in sorted((env_additions or {}).items()):
        if not isinstance(name, str) or not _ENV_NAME.fullmatch(name):
            raise SSHRunnerError(f"invalid remote environment variable name: {name!r}")
        if not isinstance(value, str) or "\x00" in value:
            raise SSHRunnerError(f"remote environment value for {name!r} must be a string without NUL")
        lines.append(f"export {name}={shlex.quote(value)}")
    lines.append(f"exec {_quote_remote_argv(argv)}")
    return "\n".join(lines) + "\n"


def build_ssh_transport_plan(
    config: RunnerConfig,
    *,
    argv: Sequence[str],
    cwd: str | Path | None = None,
    env_additions: Mapping[str, str] | None = None,
) -> SSHTransportPlan:
    destination = _destination(config)
    remote_cwd = _resolve_remote_cwd(config.base_dir, cwd)
    executable = _nonempty_token(config.executable or "ssh", "SSH executable")
    extra = _ssh_extra_args(config)
    transport_argv = (
        executable,
        "-T",
        "-o",
        "BatchMode=yes",
        *extra,
        destination,
        "sh",
        "-s",
    )
    script = build_remote_script(argv=argv, cwd=remote_cwd, env_additions=env_additions)
    return SSHTransportPlan(
        transport_argv=transport_argv,
        remote_script=script,
        destination=destination,
        remote_cwd=remote_cwd,
    )


class SSHExternalToolRunner:
    """Minimal SSH transport exposing the same managed-process boundary as local execution.

    The SSH client itself is supervised locally. Remote argv/cwd/environment are sent as a
    quoted POSIX shell script over stdin, so task-controlled values are never interpolated
    into a local shell command line. This transport deliberately does not implement artifact
    transfer or a remote state machine; callers needing local evidence must use a shared path
    or an explicit higher-level transfer mechanism.
    """

    def __init__(
        self,
        *,
        supervisor: SubprocessSupervisor,
        config: RunnerConfig,
    ) -> None:
        if config.kind != "ssh":
            raise SSHRunnerError("SSHExternalToolRunner requires runner kind 'ssh'")
        self.supervisor = supervisor
        self.config = config

    async def start(
        self,
        *,
        task_id: str,
        argv: Sequence[str],
        cwd: str | Path | None,
        stdout_path: str | Path,
        stderr_path: str | Path,
        attempt_id: int | None = None,
        env_additions: Mapping[str, str] | None = None,
        stdin_text: str | None = None,
        timeout_seconds: float | None = None,
        terminate_grace_seconds: float = 5.0,
        heartbeat_interval: float = 30.0,
    ) -> ManagedProcess:
        if stdin_text is not None:
            raise SSHRunnerError("remote command stdin is unavailable because SSH transport stdin carries the launch script")
        plan = build_ssh_transport_plan(
            self.config,
            argv=argv,
            cwd=cwd,
            env_additions=env_additions,
        )
        local_cwd = Path(stdout_path).parent
        local_cwd.mkdir(parents=True, exist_ok=True)
        return await self.supervisor.start(
            task_id=task_id,
            attempt_id=attempt_id,
            argv=plan.transport_argv,
            cwd=local_cwd,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            stdin_text=plan.remote_script,
            timeout_seconds=timeout_seconds,
            terminate_grace_seconds=terminate_grace_seconds,
            heartbeat_interval=heartbeat_interval,
        )
