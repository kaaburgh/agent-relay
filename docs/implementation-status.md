# Implementation Status

## Current checkpoint

Completed: `R00`–`R22`.

In progress: `R23`.

Next bounded unit: `R23 — Real shadPS4/Bloodborne tool adapter`.

Acceptance pending: dedicated R23 acceptance coverage for the external shadPS4/Bloodborne adapter. The production adapter is present at HEAD, and the full pre-existing regression suite is green, but R23 is **not** complete until its own success/failure/evidence cases are directly exercised.

## Durable recovery point

Current WIP implementation commit: `5e6365c6030b5c3873231546275e8e4510b983f9` (`R23: add external shadPS4 Bloodborne validation adapter`).

The commit adds `agent_relay/shadps4_validator.py` with an external-config boundary around the existing harness. It renders only allowed placeholders, supervises the external process, optionally applies the evidence-progress watchdog, reads `runner-status.json`, `cycles.csv`/`cycles.json`, and `summary.md`, validates run ID/requested/completed cycles and cycle statuses, and classifies success/failure/incomplete evidence/process failure. Bloodborne menu/death/reload behavior remains outside core orchestration.

GitHub Actions run `34832377976` on `5e6365c6030b5c3873231546275e8e4510b983f9`: PASS, 134 tests in 41.974s on Python 3.12.14. This proves no observed regression in the existing suite; it is deliberately **not** accepted as R23 completion evidence because there was no R23-specific test module in that run.

Exact recovery action:

1. Review `agent_relay/shadps4_validator.py` against R23/spec acceptance requirements.
2. Add `tests/test_shadps4_validator.py` using a real fake external harness subprocess.
3. Cover at minimum: successful N/N evidence, exit-zero incomplete evidence, runner/cycle failure, run-ID/request-count mismatch, CSV and JSON cycle evidence, process crash/nonzero, timeout/stall cleanup, placeholder validation, and durable artifact/attempt provenance.
4. Run the R23-specific tests, then `python -m unittest discover -s tests -v`.
5. Only after both are green, atomically mark R23 `DONE`, set R24 `READY`, update this file with exact evidence, and continue automatically.

## Protocol repair after the R23 partial-stop incident

The previous handoff could drift because HEAD contained R23 production code, `ROADMAP.md` still said R23 `READY`, `docs/implementation-status.md` was stale at R21/R22, and the general regression CI remained green. That state is no longer considered acceptable.

Protocol changes now required by `AGENTS.md`:

- a new unit moves to `IN PROGRESS` before/with partial implementation landing;
- a full regression suite does not prove a unit complete without unit-specific acceptance coverage;
- partial/WIP commits must durably record missing acceptance and exact recovery action;
- roadmap/status transitions should use one atomic Git tree commit when possible;
- every tool/session closeout runs an explicit acceptance/status/recovery checklist;
- `tests/test_project_status.py` machine-checks agreement between roadmap and implementation status so metadata drift makes CI red.

## Through R22

Task/config parsing, durable SQLite state/events, workflow invariants, immutable attempts/evidence, managed Git workspaces, real subprocess supervision/watchdog cleanup, simulated providers/tools, bounded review/rework, provider retry, persisted resource leases, restart recovery, fail-closed guardrails, the explicit twelve-scenario deterministic integration matrix, the seeded 100-workflow chaos/invariant sweep, operator CLI/doctor, the real Codex writer adapter, and the independent Claude reviewer adapter are implemented and acceptance-tested.

R20 acceptance: GitHub Actions run `34830427856` on `9cfd4dbf84b8587004e5df4437a9ae18c638730c`: PASS, 121 tests.

R21 acceptance: GitHub Actions run `34831114522` on `511854de14693700b3cd595fcc8e259014fe7f2d`: PASS, 127 tests.

R22 checkpoint: `0e6885a06c809617a62393b60329b1cc5f07ccb7`; its GitHub Actions run `34832267148` completed successfully before R23 implementation landed.

## Durable decisions

- Python 3.12 / Linux-first.
- Deterministic orchestrator; models are bounded workers, never state-machine owners.
- Simulation-first; real providers/runtime follow deterministic integration/recovery behavior.
- SQLite is durable/fail-closed; semantic events and completed validation/review evidence are append-only.
- Existing user checkouts are never cleaned/reset automatically; mutations happen in dedicated managed worktrees.
- Managed subprocesses use independent process groups with whole-group cleanup and sparse durable liveness metadata.
- Prompts may be delivered over stdin so large/sensitive task instructions are not copied into process argv metadata.
- Credential redaction must distinguish secret token material from numeric token-usage telemetry.
- A provider's successful exit/handoff never substitutes for deterministic Git/evidence acceptance gates.
- Malformed reviewer output and incomplete deterministic validation evidence block rather than fail open.
- Correction rounds are bounded and rejected-generation history remains auditable.
- Resource serialization and restart ownership are durable, never process-local assumptions.
- Required deterministic scenarios are explicitly named; chaos adds reproducible variation and never replaces deterministic acceptance.
- Bloodborne-specific behavior stays outside orchestration core.

## Handoff protocol

At every roadmap checkpoint, record completed/in-progress unit, exact commands/results, important files/modules, material decisions, unresolved limitations, missing acceptance, and exact next recovery action. Preserve useful history and never mark an acceptance gate complete without direct evidence.
