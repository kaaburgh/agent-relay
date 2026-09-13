from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .config import load_global_config, load_task_spec
from .models import ConfigError


_NOT_IMPLEMENTED = "not implemented until durable storage/orchestration roadmap units are complete"


def _task_create(args: argparse.Namespace) -> int:
    task = load_task_spec(args.task_file)
    payload = {
        "status": "valid",
        "repository": task.repository,
        "baseline": task.baseline,
        "validation_steps": len(task.validation),
        "max_correction_rounds": task.max_correction_rounds,
    }
    print(json.dumps(payload, sort_keys=True))
    return 0


def _execution_shell(args: argparse.Namespace) -> int:
    print(f"{args.command}: {_NOT_IMPLEMENTED}", file=sys.stderr)
    return 2


def _doctor(args: argparse.Namespace) -> int:
    if args.config is None:
        print(f"doctor: {_NOT_IMPLEMENTED}", file=sys.stderr)
        return 2
    config = load_global_config(args.config)
    payload = {
        "status": "config-valid",
        "writer_provider": config.writer.provider,
        "reviewer_provider": config.reviewer.provider,
        "runner_count": len(config.runners),
        "resource_count": len(config.resources),
    }
    print(json.dumps(payload, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-relay",
        description="Durable deterministic orchestration for long-running coding and research agents.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    task_parser = subparsers.add_parser("task", help="task specification commands")
    task_subparsers = task_parser.add_subparsers(dest="task_command", required=True)
    create_parser = task_subparsers.add_parser("create", help="validate a task specification")
    create_parser.add_argument("task_file", type=Path)
    create_parser.set_defaults(handler=_task_create)

    for name in ("run", "status", "events", "resume", "cancel"):
        command_parser = subparsers.add_parser(name)
        command_parser.add_argument("task_id")
        command_parser.set_defaults(handler=_execution_shell)

    doctor_parser = subparsers.add_parser("doctor")
    doctor_parser.add_argument("--config", type=Path)
    doctor_parser.set_defaults(handler=_doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0
    try:
        return int(handler(args))
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
