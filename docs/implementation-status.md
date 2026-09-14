# Implementation Status

## Current checkpoint

Completed: `R00`, `R01`, `R02`, `R03`, `R04`, `R05`, `R06`, `R07`, `R08`, `R09`, `R10`.

Next bounded unit: `R11 — REQUEST_CHANGES/rework generations`.

## R01–R09 summary

Task/config parsing and CLI routing exist; SQLite durable state/events are versioned and transactional; workflow policy enforces exact candidate/validation/review provenance; attempt/evidence history is immutable/redacted where implemented; managed writer/reviewer worktrees use real Git; subprocess supervision uses real process groups; simulated writer/reviewer/validator workers are real processes and machine-readable evidence is fail-closed.

## R10 evidence

Implemented `agent_relay/evidence.py`, `agent_relay/orchestrator.py` and `tests/test_orchestrator_happy_path.py`.

The first full deterministic workflow now runs with real temporary Git, SQLite and subprocesses:

- `READY -> WORK` starts a simulated writer in an isolated managed writer worktree;
- the writer makes a real commit and that exact SHA becomes candidate generation 1;
- `WORK -> VALIDATE` runs the real-process simulated expensive validator and persists validation evidence for generation 1/SHA;
- only complete N/N deterministic validator evidence permits `VALIDATE -> REVIEW`;
- the reviewer receives a separate detached worktree at the exact candidate SHA and runs as an independent fresh subprocess;
- valid structured `APPROVE` is persisted as a review record tied to the same generation/SHA;
- `REVIEW -> DONE` consumes exact validation/review provenance through workflow invariants;
- persisted event history is exactly `task_created`, `stage_started`, `candidate_commit_detected`, `validation_started`, `validation_finished`, `review_started`, `review_finished`, `task_completed` for the happy path;
- writer/validation/reviewer each have separate immutable attempts.

Acceptance command:

```bash
python -m unittest discover -s tests -v
```

GitHub Actions on `d1047264f0a02ad69a35a1ea46eeed68607551bc`: PASS, 73 tests on Python 3.12.14.

## Current product state

The simulation stack can now complete one full implementation/validation/independent-review workflow to `DONE` with exact durable candidate provenance. Next is the correction loop: immutable first review, findings supplied to fresh rework, a second candidate generation, revalidation and a fresh reviewer invocation against generation 2.

## Durable decisions

- Python 3.12 / Linux-first.
- Deterministic orchestrator; models are bounded workers, never state-machine owners.
- Simulation-first; real providers/runtime follow deterministic integration/recovery behavior.
- SQLite is versioned/fail-closed; semantic events are append-only.
- Workflow transitions use optimistic compare-and-set rather than last-writer-wins.
- Durable snapshots/managed command records are redacted before persistence.
- Existing user checkouts are never cleaned/reset automatically; all agent mutations occur in dedicated managed worktrees.
- Managed subprocesses use independent process groups with whole-group cleanup and sparse durable liveness metadata.
- Simulated workers write durable out-of-process result/evidence checkpoints suitable for restart/recovery tests.
- Reviewer approval is possible only after strict structured-output validation for the exact candidate generation/SHA.
- External validation does not trust exit zero; deterministic evidence completeness is authoritative when available.
- Workflow transitions consume persisted validation/review evidence provenance, not provider claims alone.
- Bloodborne-specific behavior stays outside orchestration core.

## Handoff protocol

At every roadmap checkpoint, record completed/in-progress unit, exact commands/results, important files/modules, material decisions, unresolved limitations and exact next `READY` unit. Preserve useful history and never mark an acceptance gate complete without evidence.
