# Implementation Status

## Current checkpoint

Completed: `R00`, `R01`, `R02`, `R03`, `R04`, `R05`, `R06`, `R07`, `R08`.

Next bounded unit: `R09 — Generic external-tool model and simulated expensive validator`.

## R01–R07 summary

Task/config parsing and CLI routing exist; SQLite durable state/events are versioned and transactional; workflow policy enforces exact candidate/validation/review provenance; attempt/evidence history is immutable and redacted; managed writer/reviewer worktrees use real Git and preserve user-owned checkout state; subprocess supervision uses real process groups; the simulated writer is recovery-friendly and runs out-of-process.

## R08 evidence

Implemented `agent_relay/review.py`, `agent_relay/simulated_reviewer.py`, `agent_relay/simulated_reviewer_worker.py` and `tests/test_simulated_reviewer.py`.

Reviewer guarantees:

- verdict and finding severity values are strictly validated;
- `REQUEST_CHANGES` and `BLOCKED_BY_MISSING_EVIDENCE` require concrete findings;
- noisy output may contain one valid structured review object, but multiple valid objects are rejected as ambiguous;
- malformed output never becomes approval;
- reviewer launch checks the detached worktree HEAD against the requested exact candidate SHA before starting;
- every invocation gets a fresh UUID and immutable reviewer attempt even for the same candidate;
- raw reviewer output is retained as an artifact with invocation ID, candidate SHA and generation;
- provider unavailable, nonzero process failure/crash and timeout/hang remain distinct from malformed structured output;
- approve and request-changes paths are exercised with real subprocesses and real Git worktrees.

Acceptance command:

```bash
python -m unittest discover -s tests -v
```

GitHub Actions on `e107c60c841774e48ea5ac38d0499aba12f07260`: PASS, 66 tests on Python 3.12.14.

## Current product state

Task/config parsing, durable SQLite state/events, workflow invariants, immutable attempts/evidence, managed Git workspaces/candidate generations, real subprocess supervision, simulated writer and independent simulated reviewer exist. Generic expensive-tool simulation, end-to-end orchestration, resource leases, restart recovery and real provider/shadPS4 adapters remain unimplemented.

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
- Bloodborne-specific behavior stays outside orchestration core.

## Handoff protocol

At every roadmap checkpoint, record completed/in-progress unit, exact commands/results, important files/modules, material decisions, unresolved limitations and exact next `READY` unit. Preserve useful history and never mark an acceptance gate complete without evidence.
