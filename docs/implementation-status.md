# Implementation Status

## Current checkpoint

Completed: `R00`, `R01`, `R02`, `R03`.

Next bounded unit: `R04 — Attempt/artifact layout and immutable attempt records`.

## R01 evidence

Implemented typed task/global config models, YAML/JSON loading, provider/runner/resource/workspace/validation shapes, credential-free examples and CLI routing for `task create`, `run`, `status`, `events`, `resume`, `cancel`, `doctor`. Execution commands fail closed until durable execution exists. The durable-unit protocol now treats a successful checkpoint as a continuation point rather than an automatic end of the overall agent run.

Acceptance command:

```bash
python3 -m unittest discover -s tests -v
```

R01 result: PASS, 13 tests.

## R02 evidence

Implemented `agent_relay/store.py` with schema version 1 and durable tables for tasks/current state, attempts, candidate generations, processes, artifacts, provider waits, validations, reviews, resources/leases and append-only semantic events.

Important properties:

- schema creation and `PRAGMA user_version` run in one explicit SQLite migration transaction;
- newer unknown schema versions fail closed;
- task creation and state update + semantic event append are atomic under `BEGIN IMMEDIATE`;
- event UPDATE/DELETE is prohibited with SQLite triggers;
- reopen/reload preserves state and ordered event history;
- deliberately failed migration proves partial schema/version changes roll back.

Acceptance command:

```bash
python3 -m unittest discover -s tests -v
```

R02 result: PASS, 21 tests. GitHub Actions for final R02 checkpoint `b71073b4da0adfe30c5aedbc9d777ae0825d1910`: PASS.

## R03 evidence

Implemented `agent_relay/workflow.py` with the explicit generic lifecycle:

`READY`, `WORK`, `VALIDATE`, `REVIEW`, `REWORK`, `WAITING_PROVIDER`, `BLOCKED`, `FAILED`, `CANCELLED`, `DONE`.

Enforced/tested invariants include:

- illegal transitions do not mutate state/history;
- terminal `DONE`/`FAILED`/`CANCELLED` states do not regress;
- `REVIEW` requires a frozen candidate generation/SHA and, when configured, successful validation for that exact candidate;
- `DONE` requires valid structured reviewer output with `APPROVE`/`APPROVE_WITH_FOLLOWUPS` for the current generation/SHA plus matching successful validation when mandatory;
- malformed reviewer output cannot approve;
- stale validation or stale review evidence cannot approve a newer candidate;
- `REWORK` requires a valid `REQUEST_CHANGES` result for the current candidate;
- persisted transitions survive SQLite reopen;
- state transition writes use compare-and-set expectations for persisted stage + candidate generation/SHA inside the `BEGIN IMMEDIATE` transaction, preventing a stale supervisor snapshot from overwriting newer durable state.

Acceptance command:

```bash
python3 -m unittest discover -s tests -v
```

R03 local result: PASS, 32 tests. During incremental GitHub commits, CI on `75af937...` failed because workflow code/tests were published before the matching Store CAS interface; commit `df22fd091a048a7f323afe07db4531da59bea8bb` closed that interface mismatch and GitHub Actions passed. The stale-snapshot regression test was then added before declaring the checkpoint complete.

## Current product state

Task/config parsing, CLI routing, SQLite durable state/event history and deterministic persisted workflow policy exist. Attempt artifact management, Git workspaces, provider/tool subprocesses, leases semantics, simulations, restart recovery and shadPS4 integration are not yet claimed.

## Durable decisions

- Python 3.12 / Linux-first.
- Deterministic orchestrator; models are bounded workers, never state-machine owners.
- Simulation-first; real Codex/Claude/shadPS4 integration follows deterministic integration/recovery behavior.
- SQLite is the durable store; migrations are versioned/fail-closed and event history is append-only at DB layer.
- Workflow transitions are optimistic compare-and-set writes against stage + candidate provenance, rather than blind last-writer-wins updates.
- YAML/JSON configuration models intentionally contain no secret/token fields.
- `local` and `ssh` runner kinds are reserved in config so remote validation can be added without changing task shape.
- Bloodborne-specific behavior stays outside orchestration core.

## Handoff protocol

At every roadmap checkpoint, record completed/in-progress unit, exact commands/results, important files/modules, material decisions, unresolved limitations and exact next `READY` unit. Preserve useful history and never mark an acceptance gate complete without evidence.
