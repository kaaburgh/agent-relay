# Implementation Status

## Current checkpoint

Completed: `R00`, `R01`, `R02`, `R03`, `R04`, `R05`, `R06`, `R07`, `R08`, `R09`, `R10`, `R11`, `R12`, `R13`, `R14`, `R15`, `R16`, `R17`, `R18`, `R19`.

Next bounded unit: `R20 — Operator CLI and doctor`.

## R01–R18 summary

Task/config parsing, durable SQLite state/events, workflow invariants, immutable attempts/evidence, managed Git workspaces, real subprocess supervision/watchdog cleanup, simulated providers/tools, bounded review/rework, provider retry, persisted resource leases, restart recovery, fail-closed guardrails, and the explicit twelve-scenario deterministic integration matrix are implemented with real temporary Git/SQLite/subprocess semantics.

## R19 evidence

Implemented `agent_relay/chaos.py` and `tests/test_chaos.py`.

Chaos/stress guarantees:

- a seeded plan guarantees at least one execution of every supported injection mode, then fills the remaining workflows from the same deterministic RNG;
- each workflow owns a fresh tiny real Git repository and SQLite database rather than sharing in-memory mock state;
- real simulated subprocess modes include happy completion, review/rework, writer crash, provider unavailable/wait/retry, validation failure, incomplete evidence, malformed review, delayed writer result, detached-writer restart recovery, and tool crash;
- `chaos-report.json` is written before execution and atomically updated after every workflow; a failure records seed, workflow index, injection mode, exception type and message;
- after every workflow an invariant sweep verifies contiguous candidate generations, current pointer/history agreement, exact candidate provenance for validation/review rows, REVIEW/REWORK preconditions, DONE validation+approval gates, at most one active writer, resource capacity, and one candidate event per frozen generation;
- seed `20260914` completed exactly 100 workflows and exercised all ten injection modes without invariant failure.

Acceptance command:

```bash
python -m unittest discover -s tests -v
```

GitHub Actions on `718113285e1099ddc92b087e87155a09471da0e4`: PASS, 119 tests in 37.094s on Python 3.12.14. The required 100-workflow chaos test itself completed in about 19.8s inside that run. CI run: `34825130254`.

## Current product state

Simulation/recovery behavior is now covered by deterministic scenario acceptance and reproducible randomized invariant sweeps. The next unit exposes that durable engine to an operator: task create/run/status/events/resume/cancel plus a doctor command that reports prerequisites and configured provider/runtime readiness without printing credentials.

## Durable decisions

- Python 3.12 / Linux-first.
- Deterministic orchestrator; models are bounded workers, never state-machine owners.
- Simulation-first; real providers/runtime follow deterministic integration/recovery behavior.
- SQLite is durable/fail-closed; semantic events and completed validation/review evidence are append-only.
- Existing user checkouts are never cleaned/reset automatically; mutations happen in dedicated managed worktrees.
- Managed subprocesses use independent process groups with whole-group cleanup and sparse durable liveness metadata.
- Malformed reviewer output and incomplete deterministic validation evidence block rather than fail open.
- Correction rounds are bounded and rejected-generation history remains auditable.
- Resource serialization and restart ownership are durable, never process-local assumptions.
- Required deterministic scenarios are explicitly named; chaos adds reproducible variation and never replaces deterministic acceptance.
- Chaos failures must report their seed and exact workflow mode/index so randomized testing remains reproducible.
- Bloodborne-specific behavior stays outside orchestration core.

## Handoff protocol

At every roadmap checkpoint, record completed/in-progress unit, exact commands/results, important files/modules, material decisions, unresolved limitations and exact next `READY` unit. Preserve useful history and never mark an acceptance gate complete without evidence.
