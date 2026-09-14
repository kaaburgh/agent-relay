# Implementation Status

## Current checkpoint

Completed: `R00`, `R01`, `R02`, `R03`, `R04`, `R05`, `R06`, `R07`, `R08`, `R09`, `R10`, `R11`, `R12`, `R13`, `R14`, `R15`, `R16`, `R17`.

Next bounded unit: `R18 — Complete deterministic integration suite`.

## R01–R16 summary

Task/config parsing, durable SQLite state/events, workflow invariants, immutable attempts/evidence, managed Git workspaces, real subprocess supervision/watchdog cleanup, simulated providers/tools, happy-path and multi-generation rework orchestration, provider retry, persisted resource leases, and restart recovery for writer/expensive validation are implemented with real temporary Git/SQLite/subprocess semantics.

## R17 evidence

Implemented `agent_relay/guardrails.py`, extended `agent_relay/orchestrator.py`, and added `tests/test_guardrails.py`.

Guardrail guarantees:

- malformed reviewer output from an otherwise successful process is never persisted as approval; orchestration emits `review_invalid_output` and transitions durably from `REVIEW` to `BLOCKED`;
- exit-zero validation with incomplete deterministic evidence is persisted as `INCOMPLETE_EVIDENCE`, emits `validation_incomplete_evidence`, blocks before review starts, and can never satisfy a review/DONE gate;
- review policy consumes only persisted review/validation evidence for the exact current generation/SHA;
- `max_correction_rounds` is interpreted as the number of allowed `REWORK` rounds: with limit 2, the first two `REQUEST_CHANGES` verdicts create fresh generations while the third is preserved as review evidence and transitions to `BLOCKED` with `correction_limit_reached`;
- blocking at the correction limit does not launch another writer or create another candidate generation;
- a real three-generation sequence proves two correction rounds followed by a fresh approval and `DONE`, with distinct reviewer attempts/run IDs, exact-generation validation, findings copied into the next writer inputs, and immutable historical review rows.

Acceptance command:

```bash
python -m unittest discover -s tests -v
```

GitHub Actions on `82f9e8880e929b52b64f4230b4eab4f22a27af84`: PASS, 93 tests in 27.135s on Python 3.12.14.

## Current product state

All primitives needed by the twelve required deterministic scenarios now exist. The next unit makes that contract explicit as a named integration-scenario suite rather than relying on coverage being distributed implicitly across lower-level tests. Any scenario-specific gap found while assembling that matrix must be implemented before R18 is marked complete.

## Durable decisions

- Python 3.12 / Linux-first.
- Deterministic orchestrator; models are bounded workers, never state-machine owners.
- Simulation-first; real providers/runtime follow deterministic integration/recovery behavior.
- SQLite is durable/fail-closed; semantic events and completed validation/review evidence are append-only.
- Existing user checkouts are never cleaned/reset automatically; mutations happen in dedicated managed worktrees.
- Managed subprocesses use independent process groups with whole-group cleanup and sparse durable liveness metadata.
- Wall-clock timeout and evidence-progress stall are separate policies; liveness heartbeat is not proof of useful progress.
- Malformed reviewer output and incomplete deterministic validation evidence block rather than fail open.
- Correction rounds are bounded by task configuration and every rejected generation remains auditable.
- Resource serialization is named/capacity-based and persisted rather than a global in-memory mutex.
- Restart recovery reconciles durable result/process/filesystem evidence before any expensive relaunch.
- Ambiguous expensive-validator ownership retains its lease instead of failing open.
- Bloodborne-specific behavior stays outside orchestration core.

## Handoff protocol

At every roadmap checkpoint, record completed/in-progress unit, exact commands/results, important files/modules, material decisions, unresolved limitations and exact next `READY` unit. Preserve useful history and never mark an acceptance gate complete without evidence.
