# Implementation Status

## Current checkpoint

Completed: `R00`, `R01`, `R02`, `R03`, `R04`.

Next bounded unit: `R05 — Managed Git workspace and candidate-generation primitives`.

## R01 evidence

Typed task/global config models, YAML/JSON loading, provider/runner/resource/workspace/validation shapes, credential-free examples and CLI routing exist. Execution commands fail closed until durable execution exists.

Acceptance: `python3 -m unittest discover -s tests -v` — PASS, 13 tests.

## R02 evidence

`agent_relay/store.py` provides versioned SQLite durable state for tasks, attempts, generations, processes, artifacts, provider waits, validations, reviews, resources/leases and append-only semantic events. Task/state+event writes are transactional; event mutation/deletion is DB-prohibited; failed migrations roll back; newer schemas fail closed.

Acceptance: PASS, 21 tests. GitHub Actions final R02 checkpoint `b71073b4da0adfe30c5aedbc9d777ae0825d1910`: PASS.

## R03 evidence

`agent_relay/workflow.py` implements the explicit lifecycle and exact candidate/validation/review provenance checks. Malformed review cannot approve; stale evidence cannot approve newer candidates; `REWORK` requires current-candidate `REQUEST_CHANGES`; terminal states cannot regress. Transition commits use compare-and-set expectations for stage + generation/SHA under the SQLite write transaction.

Acceptance: local PASS, 32 tests. Incremental CI briefly failed when workflow code was published before its matching Store CAS API; commit `df22fd091a048a7f323afe07db4531da59bea8bb` closed the mismatch and CI passed.

## R04 evidence

Implemented durable attempt/artifact handling:

- SQLite schema version 2 adds DB triggers protecting attempt identity/history and artifact rows from overwrite/delete;
- per-task/per-kind attempt numbers are allocated under `BEGIN IMMEDIATE`, preventing concurrent retries from reusing the same number;
- attempt directories are non-reused and created with `exist_ok=False` as `tasks/<task-id>/attempts/<kind>-NNN/`;
- each attempt preserves `attempt.json`, `inputs.json`, `command.json`, `config.json`, `stdout.log`, `stderr.log`, and a write-once `result.json`;
- candidate generation/SHA are snapshotted in attempt metadata where applicable;
- recursive redaction removes obvious sensitive mapping keys, separate CLI secret arguments, inline `key=value` secrets and Bearer credentials before snapshots/DB command/result storage;
- path components are validated before filesystem use;
- finalized attempt results cannot be replaced through either ArtifactManager or Store lifecycle APIs.

Acceptance command:

```bash
python -m unittest discover -s tests -v
```

GitHub Actions on `1520b30756fad88eacd2d71cf36c8c22f14c28d5`: PASS, 39 tests on Python 3.12.14. An earlier run correctly failed because `Authorization=Bearer deadbeef` leaked the trailing token; `1520b307...` fixed the redaction order and the regression test now passes.

## Current product state

Task/config parsing, CLI routing, SQLite durable state/events, persisted workflow invariants, and immutable attempt/evidence layout exist. Managed Git worktrees, subprocess supervision, providers/tools, leases semantics, simulation, restart recovery and real shadPS4 integration are not yet claimed.

## Durable decisions

- Python 3.12 / Linux-first.
- Deterministic orchestrator; models are bounded workers, never state-machine owners.
- Simulation-first; real Codex/Claude/shadPS4 integration follows deterministic integration/recovery behavior.
- SQLite migrations are versioned/fail-closed; event and historical artifact identity are protected at DB layer.
- Workflow transitions use optimistic compare-and-set rather than last-writer-wins.
- Durable snapshots are redacted before persistence; configuration models contain no credential fields.
- `local` and `ssh` runner kinds are reserved without embedding remote-cluster semantics.
- Bloodborne-specific behavior stays outside orchestration core.

## Handoff protocol

At every roadmap checkpoint, record completed/in-progress unit, exact commands/results, important files/modules, material decisions, unresolved limitations and exact next `READY` unit. Preserve useful history and never mark an acceptance gate complete without evidence.
