# Implementation Status

## Current checkpoint

Completed: `R00`, `R01`, `R02`, `R03`, `R04`, `R05`.

Next bounded unit: `R06 — Subprocess supervisor foundation`.

## R01–R03 summary

Task/global configuration and CLI routing exist; SQLite durable state/events are versioned and transactional; workflow policy is explicit and persisted. Exact candidate/validation/review provenance is enforced, malformed review cannot approve, and state transitions use compare-and-set stage/generation/SHA expectations.

## R04 evidence

Immutable/non-reused attempt directories and DB identities exist with redacted inputs/command/config/result snapshots, stdout/stderr paths, candidate provenance metadata and DB-level history protection.

Acceptance: GitHub Actions on `1520b30756fad88eacd2d71cf36c8c22f14c28d5`: PASS, 39 tests on Python 3.12.14. A preceding run exposed and then fixed an `Authorization=Bearer ...` redaction leak.

## R05 evidence

Implemented `agent_relay/git_workspace.py` and real Git integration tests:

- repository/baseline refs are resolved to exact commit SHAs using argv-based `git` subprocesses;
- writer worktree uses a dedicated managed branch rooted at the frozen baseline;
- no `git reset --hard` or `git clean` is used; dirty/untracked content in the user's original checkout is demonstrated to survive managed worktree creation;
- candidate detection requires writer HEAD to be a clean committed descendant of baseline; partial/untracked work fails closed;
- candidate generation, current task candidate pointer and `candidate_commit_detected` semantic event are committed atomically in SQLite;
- recording the same SHA is idempotent and does not duplicate generation/event history;
- rework commit creates generation 2 rather than mutating generation 1;
- reviewer worktrees are unique per generation, detached, and checked out at the exact candidate SHA; generation 1 reviewer remains on generation 1 after generation 2 exists;
- pre-existing managed writer/reviewer paths fail closed instead of being overwritten.

Acceptance command:

```bash
python -m unittest discover -s tests -v
```

GitHub Actions on `61d9cc2d1ece9cc4f833a29f978cec9fa18aa0be`: PASS, 44 tests on Python 3.12.14. The immediately preceding run failed only because the test helper treated the expected exit code 1 from `git symbolic-ref -q HEAD` on a detached reviewer as an exception; the helper was corrected to assert that expected result explicitly.

## Current product state

Task/config parsing, CLI routing, SQLite durable state/events, workflow invariants, immutable attempts/evidence, managed writer/reviewer Git worktrees and candidate generations exist. Subprocess supervision, simulated providers/tools, lease semantics, end-to-end orchestration, restart recovery and real provider/shadPS4 adapters remain unimplemented.

## Durable decisions

- Python 3.12 / Linux-first.
- Deterministic orchestrator; models are bounded workers, never state-machine owners.
- Simulation-first; real providers/runtime follow deterministic integration/recovery behavior.
- SQLite is versioned/fail-closed; event and historical artifact identity are DB-protected.
- Workflow transitions use optimistic compare-and-set rather than last-writer-wins.
- Durable snapshots are redacted before persistence.
- Existing user checkouts are never cleaned/reset automatically; all agent mutations occur in dedicated managed worktrees.
- Reviewer generations use separate detached worktrees instead of reusing/resetting an earlier reviewer checkout.
- Bloodborne-specific behavior stays outside orchestration core.

## Handoff protocol

At every roadmap checkpoint, record completed/in-progress unit, exact commands/results, important files/modules, material decisions, unresolved limitations and exact next `READY` unit. Preserve useful history and never mark an acceptance gate complete without evidence.
