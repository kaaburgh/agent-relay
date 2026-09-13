# Implementation Status

## Current checkpoint

Completed: `R00`, `R01`, `R02`.

Next bounded unit: `R03 — Pure workflow state machine and invariants`.

## R01 evidence

Implemented typed task/global config models, YAML/JSON loading, provider/runner/resource/workspace/validation shapes, credential-free examples and CLI routing for `task create`, `run`, `status`, `events`, `resume`, `cancel`, `doctor`. Execution commands fail closed until durable execution exists. The durable-unit protocol now treats a successful checkpoint as a continuation point rather than an automatic end of the overall agent run.

Acceptance command:

```bash
python3 -m unittest discover -s tests -v
```

R01 result: PASS, 13 tests.

## R02 evidence

Implemented `agent_relay/store.py` with schema version 1 and durable tables for:

- tasks/current stage and candidate pointer;
- attempts and candidate generations;
- process metadata and artifacts;
- provider waits;
- validations and reviews;
- resource capacities and lease records;
- append-only semantic events.

Important properties:

- schema creation and `PRAGMA user_version` run in one explicit SQLite migration transaction;
- newer unknown schema versions fail closed;
- `task_created` is committed atomically with task creation;
- state update + semantic event append are committed atomically under `BEGIN IMMEDIATE`;
- the current candidate/generation read for an update occurs inside the same write transaction;
- event UPDATE/DELETE is prohibited with SQLite triggers, not merely by API convention;
- reopen/reload preserves state and ordered event history;
- a deliberately failed migration proves partial schema and version changes roll back.

Acceptance command:

```bash
python3 -m unittest discover -s tests -v
```

R02 result: PASS, 21 tests.

Environment note: local verification ran on Python 3.13.5 with PyYAML 6.0.3; repository/CI target remains Python 3.12 and CI installs declared dependencies.

## Current product state

Task/config parsing, CLI routing, SQLite durable state schema, transactional task/event updates and immutable event history exist. Workflow state-machine policy, attempt artifact manager, Git workspaces, providers, subprocess supervision, leases semantics, simulation, recovery and shadPS4 integration are not yet claimed.

## Durable decisions

- Python 3.12 / Linux-first.
- Deterministic orchestrator; models are bounded workers, never state-machine owners.
- Simulation-first; real Codex/Claude/shadPS4 integration follows deterministic integration/recovery behavior.
- SQLite is the durable store; migrations are versioned/fail-closed.
- Event history is append-only at the database layer.
- YAML/JSON configuration models intentionally contain no secret/token fields.
- `local` and `ssh` runner kinds are reserved in config so remote validation can be added without changing task shape.
- Bloodborne-specific behavior stays outside orchestration core.

## Handoff protocol

At every roadmap checkpoint, record completed/in-progress unit, exact commands/results, important files/modules, material decisions, unresolved limitations and exact next `READY` unit. Preserve useful history and never mark an acceptance gate complete without evidence.
