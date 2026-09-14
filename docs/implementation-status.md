# Implementation Status

## Current checkpoint

Completed: `R00`–`R24`.

In progress: `R25`.

Next bounded unit: `R25` — Documentation/example configuration.

Acceptance pending: R25 documentation/examples have been prepared but must pass their dedicated documentation contract plus the full regression suite before R25 may be marked `DONE`.

## R25 WIP — documentation and credential-free examples

This checkpoint replaces the stale scaffold-era README and adds the documentation set required by `docs/spec.md`:

- `README.md` with current implementation status, safety boundaries, operator CLI notes and three executable five-minute simulation paths (happy path, `REQUEST_CHANGES`, restart recovery);
- `docs/architecture.md`;
- `docs/state-machine.md`;
- `docs/providers.md`;
- `docs/simulation.md`;
- `docs/recovery.md`;
- `docs/shadps4-example.md`.

Example configuration is also updated/added:

- `examples/config.simulated.yaml` now matches the real CLI simulation contract by using one local runner with `options.simulated_validator: true`; the old example did not satisfy `execution.py` and therefore could not back the documented `run --simulation` workflow;
- `examples/config.real.example.yaml` demonstrates credential-free Codex writer, Claude reviewer, local/SSH runners and capacity-1 `bloodborne-runtime` configuration;
- `examples/task.simulated.example.yaml` documents the one-validator CLI simulation shape;
- `examples/task.shadps4.example.yaml` documents the external Bloodborne harness placeholders and runtime resource without hard-coding one user's installation as a repository default.

`tests/test_documentation.py` is the R25-specific acceptance gate. It checks the required doc set, stale README removal, local README links, existence of the exact quick-start unittest targets, config/task schema validity, credential-free examples, capacity-1 runtime example and explicit SSH/shared-storage/artifact-transfer limitations.

Exact R25 recovery action:

1. Run `python -m unittest tests.test_documentation -v` through CI/current HEAD.
2. Run the full `python -m unittest discover -s tests -v` suite after the latest documentation/example changes.
3. Fix documentation/example/test defects without weakening the contract or inventing unsupported real-backend behavior.
4. If green, atomically mark R25 `DONE`, set R26 `READY`, record exact CI evidence, and continue automatically into the final adversarial correctness/security review.

## R24 acceptance — minimal SSH external-tool transport

R24 is complete. `agent_relay/ssh_runner.py` exposes `SSHExternalToolRunner` on the existing `SubprocessSupervisor`/`ManagedProcess` boundary. The local `ssh` client is argv-launched with `-T` and `BatchMode=yes`; remote argv/cwd/environment additions are POSIX-quoted and sent to constant `sh -s` over stdin, keeping task-controlled command values out of a local shell command line.

The transport remains intentionally minimal: no remote workflow state machine, distributed queue/scheduler, implicit SCP/rsync/artifact transfer or claim that arbitrary daemonized remote descendants are killed when the SSH connection dies. Remote evidence must be visible to the local orchestrator through shared storage or an explicit higher-level transfer step.

Initial R24 CI run `34835972840` on `d14d4cbe46304da8e1f583b47423cdf80baabdca`: PASS, 149 tests. Before accepting the unit, the test was strengthened to execute the generated launch script through a real `/bin/sh -s`, proving shell-looking arguments/environment values containing whitespace, `;`, `$()` and quotes remain literal rather than executing injected commands. The first test also emitted a Python invalid-escape `SyntaxWarning`, which was removed.

Final R24 acceptance: GitHub Actions run `34836178935` on `a0b4e69abf7f8a7d0ef3f9376cd22f5a3bf008f9`: PASS, 150 tests in 64.759s on Python 3.12.14, including the strengthened end-to-end SSH quoting test and the seeded 100-workflow chaos regression.

## R23 acceptance — real shadPS4/Bloodborne tool adapter

`agent_relay/shadps4_validator.py` wraps the existing harness as an external evidence boundary. Bloodborne menu/death/reload behavior remains outside orchestration core.

The first dedicated R23 gate, GitHub Actions run `34834967782`, exposed a production classification bug: `cycle evidence contains 2/3 records` was incorrectly treated as deterministic validation failure. The fix narrowed explicit validation-failure classification, moved argv validation before attempt allocation, and validated cycle ordering when identifiers are present.

Final R23 acceptance: GitHub Actions run `34835209968` on `e4b882864cc1b84c3ccbca4c0f5ce5cb026b9d27`: PASS, 143 tests. Dedicated cases cover CSV/JSON success, exact provenance/artifacts, incomplete/mismatched/out-of-order evidence, explicit runner/cycle failure, crash/nonzero, timeout/stall and pre-attempt placeholder validation.

## Development protocol guard

Development status is machine-checked:

- a partially landed unit is `IN PROGRESS` rather than implicitly complete;
- a full regression suite does not replace unit-specific acceptance evidence;
- WIP checkpoints name missing acceptance and exact recovery action;
- roadmap/status transitions prefer atomic Git tree commits;
- closeout checks roadmap/status/HEAD/acceptance alignment;
- `tests/test_project_status.py` makes roadmap/handoff drift a CI failure.

## Durable decisions

- Python 3.12 / Linux-first.
- Deterministic orchestrator; models are bounded workers, never state-machine owners.
- Simulation-first; real providers/runtime follow deterministic integration/recovery behavior.
- SQLite is durable/fail-closed; semantic events and completed validation/review evidence are append-only.
- Existing user checkouts are never cleaned/reset automatically; mutations happen in dedicated managed worktrees.
- Managed subprocesses use independent process groups with whole-group cleanup and sparse durable liveness metadata.
- Prompts may be delivered over stdin so task instructions are not copied into process argv metadata.
- Credential redaction must distinguish secret token material from numeric token-usage telemetry.
- Provider/tool process success never substitutes for deterministic Git/evidence acceptance gates.
- Malformed reviewer output and incomplete deterministic validation evidence block rather than fail open.
- Correction rounds are bounded and rejected-generation history remains auditable.
- Resource serialization and restart ownership are durable, never process-local assumptions.
- Required deterministic scenarios are explicitly named; chaos adds reproducible variation and never replaces deterministic acceptance.
- Bloodborne-specific behavior stays outside orchestration core.
- SSH is transport only; it has no implicit artifact transfer or remote orchestrator semantics.

## Handoff protocol

At every roadmap checkpoint, record completed/in-progress unit, exact commands/results, important files/modules, material decisions, unresolved limitations, missing acceptance, and exact next recovery action. Preserve useful history and never mark an acceptance gate complete without direct evidence.
