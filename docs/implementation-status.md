# Implementation Status

## Current checkpoint

Completed: `R00`, `R01`, `R02`, `R03`, `R04`, `R05`, `R06`, `R07`, `R08`, `R09`, `R10`, `R11`, `R12`, `R13`, `R14`.

Next bounded unit: `R15 — External-validation restart recovery`.

## R01–R13 summary

Task/config parsing, durable SQLite state/events, workflow invariants, immutable attempts/evidence, managed Git workspaces, real subprocess supervision, simulated writer/reviewer/validator workers, happy-path and rework orchestration, durable provider retry, and generic persisted resource leases are implemented using real temporary Git/SQLite/subprocess semantics.

## R14 evidence

Implemented `agent_relay/writer_recovery.py` and `tests/test_writer_recovery.py`.

Writer recovery guarantees:

- a real separately running simulated writer process may outlive the first orchestrator/SQLite `Store` instance;
- while the recorded PID is still live and no durable result exists, reconciliation returns `RUNNING` and never launches a duplicate writer;
- after the first Store is closed, the worker independently modifies the real managed worktree, commits the candidate, writes `provider-result.json`, and exits;
- a newly opened Store reconciles provider result + real Git HEAD + durable process/attempt evidence into the original writer attempt and candidate generation;
- recovery finalizes the original attempt, repairs the stale durable process row to `SUCCEEDED`, records the exact candidate SHA/generation, and emits `writer_recovered`;
- repeated reconciliation is idempotent: it returns `ALREADY_RECOVERED` without adding attempts, candidate generations, semantic events, or a second expensive worker launch;
- missing result plus no live recorded process is treated as ambiguous ownership and fails closed rather than guessing that a relaunch is safe.

Acceptance command:

```bash
python -m unittest discover -s tests -v
```

GitHub Actions on `535caec5292128739084270f6c9e86161fa4f4e4`: PASS on Python 3.12.

## Current product state

The writer side now has a real restart boundary: durable filesystem/Git/SQLite evidence is sufficient to recover a completed worker without duplicate launch. The next gap is the analogous expensive-validator recovery path, including persisted resource-lease ownership and fail-closed behavior for ambiguous/incomplete evidence.

## Durable decisions

- Python 3.12 / Linux-first.
- Deterministic orchestrator; models are bounded workers, never state-machine owners.
- Simulation-first; real providers/runtime follow deterministic integration/recovery behavior.
- SQLite is durable/fail-closed; semantic events and completed validation/review evidence are append-only.
- Existing user checkouts are never cleaned/reset automatically; mutations happen in dedicated managed worktrees.
- Managed subprocesses use independent process groups with whole-group cleanup and sparse durable liveness metadata.
- Provider retry timing is explicit durable data; no busy-spin or implicit retry loop.
- Resource serialization is named/capacity-based and persisted rather than a global in-memory mutex.
- Restart recovery reconciles durable evidence before considering any expensive relaunch.
- Bloodborne-specific behavior stays outside orchestration core.

## Handoff protocol

At every roadmap checkpoint, record completed/in-progress unit, exact commands/results, important files/modules, material decisions, unresolved limitations and exact next `READY` unit. Preserve useful history and never mark an acceptance gate complete without evidence.
