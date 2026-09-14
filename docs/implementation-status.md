# Implementation Status

## Current checkpoint

Completed: `R00`, `R01`, `R02`, `R03`, `R04`, `R05`, `R06`, `R07`, `R08`, `R09`, `R10`, `R11`, `R12`, `R13`, `R14`, `R15`, `R16`, `R17`, `R18`.

Next bounded unit: `R19 — Chaos/stress and invariant sweeps`.

## R01–R17 summary

Task/config parsing, durable SQLite state/events, workflow invariants, immutable attempts/evidence, managed Git workspaces, real subprocess supervision/watchdog cleanup, simulated providers/tools, bounded multi-generation review/rework, provider retry, persisted resource leases, fail-closed review/validation guardrails, and restart recovery for writer/expensive validation are implemented with real temporary Git/SQLite/subprocess semantics.

## R18 evidence

Implemented `tests/test_required_integration_scenarios.py` and `docs/integration-scenarios.md`.

Deterministic integration-suite guarantees:

- all twelve scenarios required by `docs/spec.md` have explicit `test_sNN_...` acceptance names instead of relying on incidental distributed coverage;
- the suite deliberately re-executes the existing real Git/SQLite/subprocess cases for happy path, review correction, writer recovery, validator recovery, malformed review, incomplete validation, watchdog cleanup, repeated corrections, correction limit, and worker completion while supervisor is absent;
- provider-unavailability scenario now invokes a real simulated writer subprocess that reports unavailable, enters durable `WAITING_PROVIDER`, respects the retry deadline, then invokes a second writer subprocess successfully, clears wait metadata, freezes the candidate and advances the workflow;
- capacity-1 resource scenario now runs a real fake validator process for task A, proves task B cannot acquire the runtime and therefore has no runtime process, releases A, then runs B; persisted process timestamps prove the two fake runtimes did not overlap;
- every acceptance test uses temporary/dedicated state and no real model quota, GPU, shadPS4 or Bloodborne instance.

Acceptance command:

```bash
python -m unittest discover -s tests -v
```

GitHub Actions on `29fa23c5a80574f76515ff494e304cbee2d17f6d`: PASS. The complete test job, including the twelve-scenario acceptance class, succeeded on Python 3.12.

## Current product state

The simulation system now has an explicit deterministic acceptance matrix matching all twelve product-contract scenarios. The next unit is randomized but reproducible chaos/stress: at least 100 short workflows with a recorded seed and invariant checks after every workflow, injecting provider/process/tool/review/restart/delayed-result failures without weakening deterministic gates.

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
- Required integration scenarios are explicitly named and executable; primitive-level coverage alone is not considered sufficient when the contract requires composed behavior.
- Bloodborne-specific behavior stays outside orchestration core.

## Handoff protocol

At every roadmap checkpoint, record completed/in-progress unit, exact commands/results, important files/modules, material decisions, unresolved limitations and exact next `READY` unit. Preserve useful history and never mark an acceptance gate complete without evidence.
