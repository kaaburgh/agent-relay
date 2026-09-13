# Implementation Status

## Current checkpoint

Completed: `R00`, `R01`, `R02`, `R03`, `R04`, `R05`, `R06`.

Next bounded unit: `R07 — Simulated writer provider`.

## R01–R05 summary

Task/config parsing and CLI routing exist; SQLite durable state/events are versioned and transactional; workflow policy enforces exact candidate/validation/review provenance; attempt/evidence history is immutable and redacted; managed writer/reviewer worktrees use real Git and preserve user-owned checkout state.

## R06 evidence

Implemented `agent_relay/supervisor.py` with real asyncio subprocess semantics:

- argv-only `asyncio.create_subprocess_exec`; no shell interpretation;
- each managed process starts in its own session/process group (`start_new_session=True`), so process-tree cleanup can target the group and workers are not tied to the orchestrator's process group;
- durable process record captures task/attempt, PID, PGID, redacted argv, start/end timestamps, exit status, state and last liveness;
- stdout/stderr go to preserved attempt files;
- no arbitrary global timeout; optional per-stage timeout is explicit;
- timeout/termination sends SIGTERM to the full process group, waits a configurable grace period, then SIGKILLs the group when required;
- cancellation also cleans the process group and persists terminal process state;
- heartbeat updates `last_liveness_at` without emitting repeated semantic events;
- if the child launches but durable process recording fails, the newly created process group is killed before the error escapes;
- managed process commands are redacted before SQLite persistence.

Real subprocess tests cover successful stdout/stderr capture, durable PID/exit metadata, explicit timeout, deterministic SIGTERM-ignore -> SIGKILL escalation after a child readiness checkpoint, descendant process-group cleanup, sparse heartbeat without event spam, absence of a hidden short timeout, argv literal handling/no shell injection, and command redaction.

Acceptance command:

```bash
python -m unittest discover -s tests -v
```

GitHub Actions on `72fba1348ff5aa4209ae79a4f1c5b0b2344ef7f5`: PASS, 51 tests on Python 3.12.14. The SIGKILL test was deliberately hardened after an earlier green run so it waits for the child to install its SIGTERM-ignore handler before asserting forced escalation, removing a scheduler-dependent race.

## Current product state

Task/config parsing, CLI routing, SQLite durable state/events, workflow invariants, immutable attempts/evidence, managed Git workspaces/candidate generations and real subprocess supervision exist. Simulated writer/reviewer/tool adapters, end-to-end orchestration, leases semantics, restart recovery and real provider/shadPS4 adapters remain unimplemented.

## Durable decisions

- Python 3.12 / Linux-first.
- Deterministic orchestrator; models are bounded workers, never state-machine owners.
- Simulation-first; real providers/runtime follow deterministic integration/recovery behavior.
- SQLite is versioned/fail-closed; event and historical artifact identity are DB-protected.
- Workflow transitions use optimistic compare-and-set rather than last-writer-wins.
- Durable snapshots/managed command records are redacted before persistence.
- Existing user checkouts are never cleaned/reset automatically; all agent mutations occur in dedicated managed worktrees.
- Managed subprocesses use independent process groups with whole-group cleanup and sparse durable liveness metadata.
- Bloodborne-specific behavior stays outside orchestration core.

## Handoff protocol

At every roadmap checkpoint, record completed/in-progress unit, exact commands/results, important files/modules, material decisions, unresolved limitations and exact next `READY` unit. Preserve useful history and never mark an acceptance gate complete without evidence.
