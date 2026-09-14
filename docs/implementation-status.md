# Implementation Status

## Current checkpoint

Completed: `R00`, `R01`, `R02`, `R03`, `R04`, `R05`, `R06`, `R07`, `R08`, `R09`, `R10`, `R11`, `R12`, `R13`.

Next bounded unit: `R14 — Writer restart recovery`.

## R01–R12 summary

Task/config parsing, durable SQLite state/events, workflow invariants, immutable attempts/evidence, managed Git workspaces, real subprocess supervision, simulated writer/reviewer/validator workers, happy-path and rework orchestration, and durable provider wait/retry are implemented with real temporary Git/SQLite/subprocess semantics.

## R13 evidence

Implemented `agent_relay/resource_leases.py` and `tests/test_resource_leases.py`.

Resource lease guarantees:

- named resources have explicit positive capacity stored in SQLite;
- acquisition runs under `BEGIN IMMEDIATE`, so capacity read/check/insert is one serialized transaction;
- capacity 1 rejects a second holder until release; capacity 2 permits exactly two active holders;
- separate resource names can be held concurrently, so unrelated workflow stages are not globally serialized;
- holder IDs are durable single-use lease identities and an active same-owner acquire is idempotent;
- release verifies ownership and emits `resource_released`; acquire emits `resource_acquired`; heartbeat updates only liveness and creates no semantic-event spam;
- an active lease survives database close/reopen and still consumes capacity;
- stale recovery only considers attempt-bound leases whose heartbeat is stale and for which there is no durable `RUNNING` process row;
- safe recovery is tested with a real sleeping subprocess: an artificially stale lease is not reclaimed while its process is running, then is reclaimed after the real process is terminated.

Acceptance command:

```bash
python -m unittest discover -s tests -v
```

GitHub Actions on `ff6886fc2f89cc0150cf342ffbb21e204edbe81d`: PASS on Python 3.12.

## Current product state

Core simulation now has explicit persisted concurrency control suitable for expensive validators/runtimes, in addition to provider retry and multi-generation review/rework. Next is restart recovery for the writer: durable commit/result/process evidence must allow a new orchestrator instance to advance without launching duplicate expensive writer work.

## Durable decisions

- Python 3.12 / Linux-first.
- Deterministic orchestrator; models are bounded workers, never state-machine owners.
- Simulation-first; real providers/runtime follow deterministic integration/recovery behavior.
- SQLite is durable/fail-closed; semantic events and completed validation/review evidence are append-only.
- Existing user checkouts are never cleaned/reset automatically; mutations happen in dedicated managed worktrees.
- Managed subprocesses use independent process groups with whole-group cleanup and sparse durable liveness metadata.
- Provider retry timing is explicit durable data; no busy-spin or implicit retry loop.
- Resource serialization is named/capacity-based and persisted rather than a global in-memory mutex.
- Stale lease recovery fails safe when durable process evidence still says RUNNING.
- Bloodborne-specific behavior stays outside orchestration core.

## Handoff protocol

At every roadmap checkpoint, record completed/in-progress unit, exact commands/results, important files/modules, material decisions, unresolved limitations and exact next `READY` unit. Preserve useful history and never mark an acceptance gate complete without evidence.
