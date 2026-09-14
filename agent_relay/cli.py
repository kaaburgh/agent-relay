from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .config import load_task_spec
from .execution import run_task
from .models import ConfigError
from .operator import (
    OperatorError,
    cancel_task,
    create_task,
    doctor,
    resolve_context,
    resume_task,
    task_events,
    task_status,
)
from .store import Store, StoreError


def _emit(value: Any) -> None:
    print(json.dumps(value, sort_keys=True))


def _context(args: argparse.Namespace):
    return resolve_context(
        config_path=getattr(args, "config", None),
        state_dir=getattr(args, "state_dir", None),
    )


def _task_create(args: argparse.Namespace) -> int:
    context = _context(args)
    task = load_task_spec(args.task_file)
    row = create_task(context, task)
    _emit(
        {
            "task_id": row.task_id,
            "stage": row.stage,
            "repository": row.repository,
            "baseline": row.baseline_ref,
            "state_dir": str(context.state_dir),
            "database": str(context.database_path),
        }
    )
    return 0


def _run(args: argparse.Namespace) -> int:
    payload = run_task(_context(args), args.task_id, simulation=args.simulation)
    _emit(payload)
    return 0 if payload.get("action") != "backend-not-installed" else 2


def _status(args: argparse.Namespace) -> int:
    context = _context(args)
    with Store(context.database_path) as store:
        _emit(task_status(store, args.task_id))
    return 0


def _events(args: argparse.Namespace) -> int:
    context = _context(args)
    with Store(context.database_path) as store:
        _emit({"task_id": args.task_id, "events": task_events(store, args.task_id, limit=args.limit)})
    return 0


def _resume(args: argparse.Namespace) -> int:
    context = _context(args)
    with Store(context.database_path) as store:
        _emit(resume_task(store, args.task_id))
    return 0


def _cancel(args: argparse.Namespace) -> int:
    context = _context(args)
    with Store(context.database_path) as store:
        _emit(cancel_task(store, args.task_id, grace_seconds=args.grace_seconds))
    return 0


def _doctor(args: argparse.Namespace) -> int:
    context = _context(args)
    task = load_task_spec(args.task) if args.task is not None else None
    payload = doctor(context, task=task)
    _emit(payload)
    return 0 if payload["ok"] else 2


def _add_runtime_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path, help="global configuration YAML/JSON")
    parser.add_argument(
        "--state-dir",
        type=Path,
        help="override durable state directory (otherwise config state_dir or .agent-relay)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-relay",
        description="Durable deterministic orchestration for long-running coding and research agents.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    task_parser = subparsers.add_parser("task", help="task specification commands")
    task_subparsers = task_parser.add_subparsers(dest="task_command", required=True)
    create_parser = task_subparsers.add_parser("create", help="persist a validated task specification")
    create_parser.add_argument("task_file", type=Path)
    _add_runtime_options(create_parser)
    create_parser.set_defaults(handler=_task_create)

    run_parser = subparsers.add_parser("run", help="run a persisted task")
    run_parser.add_argument("task_id")
    run_parser.add_argument(
        "--simulation",
        action="store_true",
        help="use the explicit built-in simulated writer/validator/reviewer backend",
    )
    _add_runtime_options(run_parser)
    run_parser.set_defaults(handler=_run)

    status_parser = subparsers.add_parser("status", help="show durable task status")
    status_parser.add_argument("task_id")
    _add_runtime_options(status_parser)
    status_parser.set_defaults(handler=_status)

    events_parser = subparsers.add_parser("events", help="show append-only semantic events")
    events_parser.add_argument("task_id")
    events_parser.add_argument("--limit", type=int)
    _add_runtime_options(events_parser)
    events_parser.set_defaults(handler=_events)

    resume_parser = subparsers.add_parser("resume", help="resume when durable recovery policy permits")
    resume_parser.add_argument("task_id")
    _add_runtime_options(resume_parser)
    resume_parser.set_defaults(handler=_resume)

    cancel_parser = subparsers.add_parser("cancel", help="cancel task and active managed process groups")
    cancel_parser.add_argument("task_id")
    cancel_parser.add_argument("--grace-seconds", type=float, default=5.0)
    _add_runtime_options(cancel_parser)
    cancel_parser.set_defaults(handler=_cancel)

    doctor_parser = subparsers.add_parser("doctor", help="check local prerequisites and configured providers/runners")
    _add_runtime_options(doctor_parser)
    doctor_parser.add_argument("--task", type=Path, help="optionally validate a task and repository/baseline")
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
    except (ConfigError, OperatorError, StoreError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
