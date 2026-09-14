# Implementation Status

## Current checkpoint

Completed: `R00`–`R21`.

Next bounded unit: `R22 — Real Claude reviewer adapter/package`.

## Through R19

Task/config parsing, durable SQLite state/events, workflow invariants, immutable attempts/evidence, managed Git workspaces, real subprocess supervision/watchdog cleanup, simulated providers/tools, bounded review/rework, provider retry, persisted resource leases, restart recovery, fail-closed guardrails, the explicit twelve-scenario deterministic integration matrix, and the seeded 100-workflow chaos/invariant sweep are implemented with real temporary Git/SQLite/subprocess semantics.

## R20 evidence — operator CLI and doctor

Implemented `agent_relay/operator.py`, `agent_relay/execution.py`, and completed `agent_relay/cli.py`; expanded `tests/test_cli.py`.

Operator guarantees:

- `task create` validates and persists both the SQLite task row and a durable human-readable task snapshot;
- `status` reports workflow stage/attempt, current generation/SHA, live managed processes, last semantic event, current review/validation, provider wait/retry metadata and held leases;
- `events` reads append-only semantic history with an optional tail limit;
- `resume` resumes a due `WAITING_PROVIDER` retry but refuses to blindly duplicate ambiguous active-stage ownership;
- `cancel` terminates active managed process groups before committing durable `CANCELLED` state;
- `doctor` checks Python/Git/SQLite writability, configured provider executables/auth probes where configured, local/SSH runner prerequisites, resources and optional repository/baseline readiness without printing credentials;
- `run --simulation` executes the real simulated writer -> validator -> independent reviewer pipeline through `DONE`;
- ordinary `run` is deliberately fail-closed before a real configured execution backend is installed and leaves a `READY` task unmodified.

First R20 acceptance had one test-only validation-order assertion failure (120/121 tests passed); no production change was needed. The assertion was made order-independent. GitHub Actions run `34830427856` on `9cfd4dbf84b8587004e5df4437a9ae18c638730c`: PASS, 121 tests.

## R21 evidence — Codex writer adapter

Implemented `agent_relay/codex_writer.py`; extended `agent_relay/supervisor.py` with bounded subprocess stdin delivery; added `tests/test_codex_writer.py`; refined artifact credential redaction so known numeric token-usage counters remain observable while credential-shaped token keys remain redacted.

Codex adapter guarantees:

- current non-interactive CLI syntax is isolated in the adapter (`codex exec`, JSONL output, model, reasoning effort, sandbox, approval policy, working directory and optional network/ephemeral settings);
- the task prompt is delivered over stdin and is not persisted in process argv/command metadata;
- stdout JSONL parsing requires a `thread.started` session ID and a terminal turn, captures the last completed `agent_message` as handoff, aggregates exposed usage counters and preserves stream errors;
- obvious rate-limit/quota/temporary-unavailability output is normalized separately from generic process failure;
- timeout remains a managed-process failure with process-group cleanup;
- exit zero, valid JSONL and a handoff are still insufficient for success: the writer worktree must contain a clean committed descendant of the exact frozen baseline;
- exit-zero/no-commit and malformed streams fail closed;
- attempts preserve command/PID/timestamps/exit/stdout/stderr/thread ID/handoff/usage/candidate evidence.

The first R21 acceptance exposed two product issues: token usage counters were over-redacted because their field names contain `token`, and valid exit-zero JSONL with no candidate was mistakenly accepted after `detect_candidate()` returned `None`. Both are now regression-covered. GitHub Actions run `34831114522` on `511854de14693700b3cd595fcc8e259014fe7f2d`: PASS, 127 tests in 39.461s on Python 3.12.14.

## Current product state

The durable engine is operator-usable with a real simulation backend, and the first authenticated real-provider boundary (Codex writer) now has a source-verified CLI adapter and deterministic CLI-compatible acceptance coverage. Real end-to-end provider execution remains intentionally incomplete until the independent Claude reviewer adapter is added; the next unit is R22.

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

At every roadmap checkpoint, record completed/in-progress unit, exact commands/results, important files/modules, material decisions, unresolved limitations and exact next `READY` unit. Preserve useful history and never mark an acceptance gate complete without evidence.
