# Implementation Status

## Current checkpoint

Completed: `R00`, `R01`, `R02`, `R03`, `R04`, `R05`, `R06`, `R07`.

Next bounded unit: `R08 — Structured review contract and simulated reviewer`.

## R01–R05 summary

Task/config parsing and CLI routing exist; SQLite durable state/events are versioned and transactional; workflow policy enforces exact candidate/validation/review provenance; attempt/evidence history is immutable and redacted; managed writer/reviewer worktrees use real Git and preserve user-owned checkout state.

## R06 evidence

Real asyncio subprocess supervision uses argv-only execution, independent process groups, durable PID/PGID/liveness/exit metadata, real stdout/stderr files, explicit per-stage timeout, SIGTERM→SIGKILL group cleanup and sparse heartbeat. GitHub Actions on `72fba1348ff5aa4209ae79a4f1c5b0b2344ef7f5`: PASS, 51 tests on Python 3.12.14.

## R07 evidence

Implemented `agent_relay/simulated_writer_worker.py` and `agent_relay/simulated_writer.py`.

The simulated writer is a real independent subprocess with declarative JSON behavior supporting:

- configurable sleeps;
- file modification constrained to the managed writer worktree;
- real Git commits;
- successful result with or without a commit;
- declared provider/work failure;
- process crash;
- partial work followed by crash;
- malformed result output;
- provider unavailable and rate-limit signals;
- indefinite hang for watchdog/timeout testing.

Recovery-oriented properties:

- the worker itself atomically writes `provider-result.json` with fsync + rename rather than depending on an in-memory callback;
- a standalone worker test proves it can modify, commit and write a durable handoff after being launched with no provider adapter waiting for completion;
- provider normalization treats a non-zero exit without result as `PROCESS_FAILURE`, exit-zero malformed/missing output as `MALFORMED`, provider-unavailable/rate-limit as `PROVIDER_UNAVAILABLE`, and never invents a candidate commit;
- success without commit is represented explicitly with `candidate_sha=None` rather than silently manufacturing provenance;
- partial work after crash remains visible/dirty for later recovery policy rather than being cleaned automatically;
- normalized provider outcome is persisted into the immutable attempt result.

Acceptance command:

```bash
python -m unittest discover -s tests -v
```

GitHub Actions on `9fab06cabb23acf9e689cb00422ca20e18a41806`: PASS, 59 tests on Python 3.12.14.

## Current product state

Task/config parsing, durable SQLite state/events, workflow invariants, immutable attempts/evidence, managed Git workspaces/candidate generations, real subprocess supervision and a recovery-friendly simulated writer exist. Structured reviewer simulation, expensive-tool simulation, end-to-end orchestration, resource leases, restart recovery and real provider/shadPS4 adapters remain unimplemented.

## Durable decisions

- Python 3.12 / Linux-first.
- Deterministic orchestrator; models are bounded workers, never state-machine owners.
- Simulation-first; real providers/runtime follow deterministic integration/recovery behavior.
- SQLite is versioned/fail-closed; event and historical artifact identity are DB-protected.
- Workflow transitions use optimistic compare-and-set rather than last-writer-wins.
- Durable snapshots/managed command records are redacted before persistence.
- Existing user checkouts are never cleaned/reset automatically; all agent mutations occur in dedicated managed worktrees.
- Managed subprocesses use independent process groups with whole-group cleanup and sparse durable liveness metadata.
- Simulated workers write durable out-of-process result checkpoints suitable for restart/recovery tests.
- Bloodborne-specific behavior stays outside orchestration core.

## Handoff protocol

At every roadmap checkpoint, record completed/in-progress unit, exact commands/results, important files/modules, material decisions, unresolved limitations and exact next `READY` unit. Preserve useful history and never mark an acceptance gate complete without evidence.
