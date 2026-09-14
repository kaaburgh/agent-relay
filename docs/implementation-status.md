# Implementation Status

## Current checkpoint

Completed: `R00`, `R01`, `R02`, `R03`, `R04`, `R05`, `R06`, `R07`, `R08`, `R09`, `R10`, `R11`.

Next bounded unit: `R12 — Provider-unavailable wait/retry`.

## R01–R10 summary

Task/config parsing, durable SQLite state/events, workflow invariants, immutable attempts/evidence, managed Git workspaces, real subprocess supervision, simulated writer/reviewer/validator workers and one complete happy-path orchestration to `DONE` are implemented and tested with real temporary Git/SQLite/processes.

## R11 evidence

Implemented the first correction loop in `agent_relay/orchestrator.py`, added review feedback to simulated writer attempt inputs, added DB-level append-only guards for validation/review evidence, and added `tests/test_orchestrator_rework.py`.

The integration scenario proves:

- generation 1 is implemented, validated, and independently reviewed as `REQUEST_CHANGES`;
- the structured HIGH finding is persisted and copied into the fresh `writer-002/inputs.json` as `review_feedback`;
- rework uses the existing isolated writer branch but requires a new real commit descended from generation 1;
- the new commit is frozen as candidate generation 2 rather than mutating generation 1;
- generation-2 validation and review use only the generation-2 SHA;
- generation 1 validation/review remain queryable and unchanged after generation 2 exists;
- the second review has a fresh invocation ID and a separate generation-2 detached reviewer worktree;
- direct SQL UPDATE/DELETE of historical reviews is rejected by append-only triggers;
- semantic history contains two candidate detections, two validation completions, two review completions, `review_requested_changes`, and final `task_completed`.

Acceptance command:

```bash
python -m unittest discover -s tests -v
```

GitHub Actions on `a929124dabbee2101ffd0553574a9c42ee20acfb`: PASS. The previous 73-test suite plus the R11 integration scenario completed successfully on Python 3.12.

## Current product state

The deterministic simulation now supports a complete happy path and one full `REQUEST_CHANGES -> REWORK -> new generation -> validation -> fresh review -> DONE` cycle with durable provenance. Next is normal provider unavailability: persist wait metadata and retry timing without busy-spin, then resume the exact interrupted workflow/candidate state.

## Durable decisions

- Python 3.12 / Linux-first.
- Deterministic orchestrator; models are bounded workers, never state-machine owners.
- Simulation-first; real providers/runtime follow deterministic integration/recovery behavior.
- SQLite is versioned/fail-closed; semantic events and validation/review evidence are append-only once evidence guards are installed.
- Workflow transitions use optimistic compare-and-set rather than last-writer-wins.
- Existing user checkouts are never cleaned/reset automatically; mutations happen in dedicated managed worktrees.
- Managed subprocesses use independent process groups with whole-group cleanup and sparse durable liveness metadata.
- Reviewer approval is possible only after strict structured-output validation for the exact candidate generation/SHA.
- External validation does not trust exit zero; deterministic evidence completeness is authoritative when available.
- Rework creates a new candidate generation and receives prior structured review findings as explicit immutable inputs.
- Bloodborne-specific behavior stays outside orchestration core.

## Handoff protocol

At every roadmap checkpoint, record completed/in-progress unit, exact commands/results, important files/modules, material decisions, unresolved limitations and exact next `READY` unit. Preserve useful history and never mark an acceptance gate complete without evidence.
