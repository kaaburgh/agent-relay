# Implementation Status

## Current checkpoint

Completed: `R00`–`R23`.

In progress: none.

Next bounded unit: `R24` — Minimal SSH external-tool runner.

## R23 evidence — real shadPS4/Bloodborne tool adapter

Implemented and hardened `agent_relay/shadps4_validator.py`; added dedicated real-subprocess acceptance in `tests/test_shadps4_validator.py`.

Adapter guarantees:

- Bloodborne/shadPS4 gameplay/death/reload behavior remains owned by the external harness; orchestration core knows only the process/evidence boundary;
- only the explicit `{run_id}`, `{requested_cycles}`, and `{evidence_dir}` argv placeholders are accepted, and template errors fail before allocating a durable attempt;
- the external process is supervised through the normal process-group/timeout machinery and may additionally use the evidence-progress stall watchdog;
- `runner-status.json`, `cycles.csv` or `cycles.json`, and `summary.md` are normalized and registered as durable artifacts tied to exact generation/candidate/run ID;
- success requires matching run ID, matching requested count, completed N/N, exactly N cycle records, a complete success runner state, no explicit failed cycle, and a valid ordered 1..N sequence when cycle identifiers are present;
- exit zero with missing/incomplete/mismatched/out-of-order evidence is `INCOMPLETE_EVIDENCE`, never success;
- explicit failed runner state or failed cycle is `VALIDATION_FAILED`;
- abrupt process crash, timeout and watchdog stall remain `PROCESS_FAILURE` unless complete deterministic failure evidence proves a validation failure;
- normalized result/attempt provenance preserves run ID, generation, candidate SHA, process outcome, paths and cycle evidence.

The first dedicated R23 gate, GitHub Actions run `34834967782`, failed one of 143 tests. It exposed a production classification bug: the generic check `evidence_error.startswith("cycle ")` incorrectly treated the incomplete-evidence message `cycle evidence contains 2/3 records` as a deterministic validation failure. The fix narrows validation-failure classification to explicit runner failure or an actual cycle `reports failure status`, validates argv before attempt allocation, and validates cycle sequence when identifiers are present.

Final R23 acceptance: GitHub Actions run `34835209968` on `e4b882864cc1b84c3ccbca4c0f5ce5cb026b9d27`: PASS, 143 tests in 129.519s on Python 3.12.14. All seven dedicated R23 test methods passed together with the full regression/100-workflow chaos suite.

## Protocol repair after the R23 partial-stop incident

Development protocol was hardened before R23 acceptance:

- a new unit moves to `IN PROGRESS` before/with partial implementation landing;
- a full regression suite does not prove a unit complete without unit-specific acceptance coverage;
- partial/WIP commits must durably record missing acceptance and exact recovery action;
- roadmap/status transitions should use one atomic Git tree commit when possible;
- every tool/session closeout runs an explicit acceptance/status/recovery checklist;
- `tests/test_project_status.py` machine-checks agreement between roadmap and implementation status so metadata drift makes CI red.

The new guard immediately caught a formatting inconsistency in the first repaired handoff, proving the metadata contract is active; the corrected protocol CI then passed before feature work continued.

## Through R22

Task/config parsing, durable SQLite state/events, workflow invariants, immutable attempts/evidence, managed Git workspaces, real subprocess supervision/watchdog cleanup, simulated providers/tools, bounded review/rework, provider retry, persisted resource leases, restart recovery, fail-closed guardrails, the explicit twelve-scenario deterministic integration matrix, the seeded 100-workflow chaos/invariant sweep, operator CLI/doctor, the real Codex writer adapter, and the independent Claude reviewer adapter are implemented and acceptance-tested.

R20 acceptance: GitHub Actions run `34830427856` on `9cfd4dbf84b8587004e5df4437a9ae18c638730c`: PASS, 121 tests.

R21 acceptance: GitHub Actions run `34831114522` on `511854de14693700b3cd595fcc8e259014fe7f2d`: PASS, 127 tests.

R22 checkpoint: `0e6885a06c809617a62393b60329b1cc5f07ccb7`; GitHub Actions run `34832267148`: PASS.

## Current product state

The durable engine now has real provider boundaries for Codex writer and Claude reviewer plus a fail-closed external shadPS4/Bloodborne validation adapter. Local external execution remains the proven path. The next bounded unit is a deliberately minimal SSH transport for external tools; it must remain separable from orchestration semantics and must not grow into a distributed scheduler.

Exact R24 recovery action:

1. Read the SSH-related task/runner configuration and doctor behavior plus the external-tool supervision boundary.
2. Define the smallest argv-safe SSH transport contract that preserves stdout/stderr/exit/timeout semantics without `shell=True` locally and without embedding orchestration state in the remote side.
3. Add dedicated R24 tests using a fake `ssh` executable/process rather than requiring a network host; prove argv shape, host/user/base-dir handling, remote argument quoting, timeout/nonzero behavior and no accidental local shell interpretation.
4. Run R24-specific tests and the full regression suite before marking R24 done.

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
- Remote transport, when present, must remain a transport adapter rather than a second orchestrator.

## Handoff protocol

At every roadmap checkpoint, record completed/in-progress unit, exact commands/results, important files/modules, material decisions, unresolved limitations, missing acceptance, and exact next recovery action. Preserve useful history and never mark an acceptance gate complete without direct evidence.
