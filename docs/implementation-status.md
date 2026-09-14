# Implementation Status

## Current checkpoint

Completed: `R00`, `R01`, `R02`, `R03`, `R04`, `R05`, `R06`, `R07`, `R08`, `R09`, `R10`, `R11`, `R12`.

Next bounded unit: `R13 — Generic persisted resource leases`.

## R01–R11 summary

Task/config parsing, durable SQLite state/events, workflow invariants, immutable attempts/evidence, managed Git workspaces, real subprocess supervision, simulated writer/reviewer/validator workers, a full happy path and a complete REQUEST_CHANGES rework generation cycle are implemented using real temporary Git/SQLite/subprocesses.

## R12 evidence

Implemented `agent_relay/provider_retry.py` and `tests/test_provider_retry.py`.

Provider-wait guarantees:

- unavailability from an active stage atomically persists `WAITING_PROVIDER`, provider/reason/timestamps/attempt count/next retry, and `provider_unavailable` plus `retry_scheduled` events;
- no polling loop is hidden in the orchestration layer: `resume_provider_if_due` returns without state/event mutation before the persisted due time;
- due retry restores the exact interrupted active stage derived from append-only event evidence and increments the stage attempt;
- wait metadata survives closing/reopening the SQLite database;
- retry count is retained while retry attempts are in flight, so repeated unavailability continues exponential backoff rather than resetting it;
- bounded backoff is tested deterministically as 10s, 20s, 40s, 40s with a 40-second cap;
- current provider-wait metadata is deleted only after explicit provider success, with a final `provider_available` semantic event;
- the implementation uses compare-at-write SQL predicates for stage changes and fails if the task changes underneath the wait/retry commit.

Acceptance command:

```bash
python -m unittest discover -s tests -v
```

GitHub Actions on `dba935606b87f30ed047f4e976a304fa828496e1`: PASS on Python 3.12.

## Current product state

The deterministic simulation supports happy path, review-driven rework generations, and durable bounded provider wait/retry. The next gap is generic persisted resource capacity/leases so expensive runtimes can serialize independently of unrelated workflow activity and recover safely after interruption.

## Durable decisions

- Python 3.12 / Linux-first.
- Deterministic orchestrator; models are bounded workers, never state-machine owners.
- Simulation-first; real providers/runtime follow deterministic integration/recovery behavior.
- SQLite is durable/fail-closed; semantic events and completed validation/review evidence are append-only.
- Workflow transitions use optimistic compare-and-set rather than last-writer-wins.
- Existing user checkouts are never cleaned/reset automatically; mutations happen in dedicated managed worktrees.
- Managed subprocesses use independent process groups with whole-group cleanup and sparse durable liveness metadata.
- Provider retry timing is explicit durable data; no busy-spin or implicit provider retry loop.
- Rework always produces a new candidate generation and receives prior findings as immutable inputs.
- Bloodborne-specific behavior stays outside orchestration core.

## Handoff protocol

At every roadmap checkpoint, record completed/in-progress unit, exact commands/results, important files/modules, material decisions, unresolved limitations and exact next `READY` unit. Preserve useful history and never mark an acceptance gate complete without evidence.
