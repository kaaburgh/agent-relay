# Implementation Status

## Current checkpoint

Completed: `R00`, `R01`, `R02`, `R03`, `R04`, `R05`, `R06`, `R07`, `R08`, `R09`.

Next bounded unit: `R10 — First end-to-end happy path`.

## R01–R08 summary

Task/config parsing and CLI routing exist; SQLite durable state/events are versioned and transactional; workflow policy enforces exact candidate/validation/review provenance; attempt/evidence history is immutable and redacted; managed writer/reviewer worktrees use real Git and preserve user-owned checkout state; subprocess supervision uses real process groups; simulated writer/reviewer providers are real out-of-process workers and malformed reviewer output cannot approve.

## R09 evidence

Implemented `agent_relay/simulated_validator_worker.py`, `agent_relay/simulated_validator.py` and `tests/test_simulated_validator.py`.

Validator guarantees:

- fake runtime is a real supervised subprocess and emits incremental `runner-status.json`, `cycles.csv` and `summary.md` under a per-run evidence directory;
- deterministic cycle metrics are emitted for orchestration/acceptance testing;
- success requires matching run ID, runner state `completed`, requested/completed N/N, exactly N ordered successful cycle records, and a summary file;
- exit code zero with omitted summary or missing cycle records becomes `INCOMPLETE_EVIDENCE`, never success;
- fail-at-cycle and crash paths cannot pass;
- runner status is demonstrably observable while the process is still running;
- simulated validator can spawn a descendant, ignore SIGTERM and hang; supervisor timeout escalates to SIGKILL for the whole group and the descendant is proven no longer live;
- validation attempt/result and discovered evidence paths remain durable artifacts tied to generation/candidate SHA.

Acceptance command:

```bash
python -m unittest discover -s tests -v
```

GitHub Actions on `f8799dec8d717d3a79ae3f045cf0a146dd53b189`: PASS, 72 tests on Python 3.12.14.

## Current product state

Simulation primitives now exist for writer, independent structured reviewer and an expensive external validator with real process/Git/evidence behavior. The next gap is orchestration that composes these pieces into a durable end-to-end state-machine run; rework/retry/leases/recovery and real adapters follow later roadmap units.

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
- Reviewer approval is possible only after strict structured-output validation for the exact candidate generation/SHA.
- External validation does not trust exit zero; deterministic evidence completeness is authoritative when available.
- Bloodborne-specific behavior stays outside orchestration core.

## Handoff protocol

At every roadmap checkpoint, record completed/in-progress unit, exact commands/results, important files/modules, material decisions, unresolved limitations and exact next `READY` unit. Preserve useful history and never mark an acceptance gate complete without evidence.
