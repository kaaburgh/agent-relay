# Implementation Status

## Current checkpoint

Completed: `R00`, `R01`, `R02`, `R03`, `R04`, `R05`, `R06`, `R07`, `R08`, `R09`, `R10`, `R11`, `R12`, `R13`, `R14`, `R15`.

Next bounded unit: `R16 — Cancellation, timeout, stall watchdog, cleanup`.

## R01–R14 summary

Task/config parsing, durable SQLite state/events, workflow invariants, immutable attempts/evidence, managed Git workspaces, real subprocess supervision, simulated providers/tools, happy-path and rework orchestration, provider retry, persisted resource leases, and writer restart recovery are implemented with real temporary Git/SQLite/subprocess semantics.

## R15 evidence

Implemented `agent_relay/validator_recovery.py` and `tests/test_validator_recovery.py`.

External-validation recovery guarantees:

- a real detached simulated validator can hold a persisted capacity lease and continue running after the first Store is closed;
- a live recorded validator process reconciles as `RUNNING`, preventing duplicate expensive launch;
- after the worker exits while the Store is absent, a new Store validates `runner-status.json`, ordered N/N `cycles.csv`, `summary.md`, run ID, generation, and candidate SHA before accepting completion;
- successful recovery finalizes the original validation attempt, repairs the stale process row, persists exact validation evidence, registers deterministic evidence artifacts, releases the original resource lease, and emits recovery/release semantic evidence;
- dead process plus incomplete/missing evidence reconciles as `AMBIGUOUS`: no validation row is created and the lease remains active, deliberately blocking overlap until an operator or stronger evidence resolves ownership;
- the recovery test uses a real Git candidate generation, real SQLite reopen, a real separate validator subprocess, and the persisted lease table rather than mocks.

Acceptance command:

```bash
python -m unittest discover -s tests -v
```

GitHub Actions on `b54bc3c95ff73d4b9fe74f419b782c0b82786901`: PASS on Python 3.12.

## Current product state

Both writer and expensive-validator restart boundaries now reconcile durable evidence before considering relaunch. Ambiguous validator ownership holds its resource lease fail-closed. Next is explicit cancellation/timeout/stall classification and cleanup, including legitimate long runs and watchdog behavior without event spam.

## Durable decisions

- Python 3.12 / Linux-first.
- Deterministic orchestrator; models are bounded workers, never state-machine owners.
- Simulation-first; real providers/runtime follow deterministic integration/recovery behavior.
- SQLite is durable/fail-closed; semantic events and completed validation/review evidence are append-only.
- Existing user checkouts are never cleaned/reset automatically; mutations happen in dedicated managed worktrees.
- Managed subprocesses use independent process groups with whole-group cleanup and sparse durable liveness metadata.
- Resource serialization is named/capacity-based and persisted rather than a global in-memory mutex.
- Restart recovery reconciles durable result/process/filesystem evidence before any expensive relaunch.
- Ambiguous expensive-validator ownership retains its lease instead of failing open.
- Bloodborne-specific behavior stays outside orchestration core.

## Handoff protocol

At every roadmap checkpoint, record completed/in-progress unit, exact commands/results, important files/modules, material decisions, unresolved limitations and exact next `READY` unit. Preserve useful history and never mark an acceptance gate complete without evidence.
