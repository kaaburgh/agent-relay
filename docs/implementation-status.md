# Implementation Status

## Current checkpoint

Completed: `R00`–`R23`.

In progress: `R24`.

Next bounded unit: `R24` — Minimal SSH external-tool runner.

Acceptance pending: dedicated R24 acceptance for safe SSH transport, quoting, timeout/nonzero behavior and explicit limitations around remote artifact ownership/lifecycle.

## R24 WIP — minimal SSH external-tool transport

The implementation unit is intentionally transport-only. It must not introduce a remote workflow state machine, distributed queue/scheduler, rsync protocol, or Bloodborne-specific behavior.

Current WIP design:

- `agent_relay/ssh_runner.py` exposes `SSHExternalToolRunner` on the existing `SubprocessSupervisor`/`ManagedProcess` boundary;
- the local process is `ssh` executed with argv-only subprocess semantics, `-T`, and `BatchMode=yes`;
- runner config supplies host/user/base_dir and optional trusted `ssh_args`;
- remote argv, cwd and environment additions are emitted as a POSIX `sh -s` launch script over SSH stdin rather than interpolated into a local shell command line;
- every remote argv/env/cwd value is shell-quoted; environment names and destination fields are validated; configured `base_dir` constrains remote cwd and rejects traversal/out-of-base absolute paths;
- remote command stdin is deliberately unsupported because transport stdin carries the launch script;
- stdout/stderr/exit/timeout remain ordinary durable managed-process semantics locally;
- artifact transfer is deliberately not implemented: higher-level adapters must use paths visible to the orchestrator (for example a shared filesystem) or a future explicit transfer mechanism. The SSH transport must not pretend remote evidence exists locally;
- local SSH process-group cleanup is supervised; no stronger claim about arbitrary daemonized remote descendants is made by this unit.

Dedicated WIP acceptance is in `tests/test_ssh_runner.py` and uses a real fake SSH executable/process without requiring a network host. It exercises local argv shape, remote script quoting, hostile shell-looking task values, stdout/stderr, nonzero exit, timeout cleanup, path/destination/environment validation and stdin ownership.

Exact recovery action:

1. Run the dedicated R24 tests plus the full suite on the WIP implementation.
2. Fix product/test issues without weakening quoting or fail-closed validation.
3. If green, record exact CI evidence, mark R24 `DONE`, and advance R25 to `READY`.
4. R25 documentation must explicitly describe the remote-evidence/shared-filesystem limitation rather than implying transparent artifact transfer.

## R23 evidence — real shadPS4/Bloodborne tool adapter

Implemented and hardened `agent_relay/shadps4_validator.py`; added dedicated real-subprocess acceptance in `tests/test_shadps4_validator.py`.

The first dedicated R23 gate, GitHub Actions run `34834967782`, failed one of 143 tests and exposed a production classification bug: the generic `cycle ` prefix classified `cycle evidence contains 2/3 records` as deterministic validation failure instead of incomplete evidence. The fix narrows explicit validation-failure classification, validates argv before attempt allocation, and validates cycle ordering when identifiers are present.

Final R23 acceptance: GitHub Actions run `34835209968` on `e4b882864cc1b84c3ccbca4c0f5ce5cb026b9d27`: PASS, 143 tests in 129.519s on Python 3.12.14. Dedicated coverage includes CSV/JSON success, exact provenance/artifacts, incomplete/mismatched/out-of-order evidence, explicit runner/cycle failure, crash, nonzero, timeout, stall and pre-attempt placeholder validation.

## Protocol repair after the R23 partial-stop incident

Development protocol is machine-checked:

- a new unit moves to `IN PROGRESS` before/with partial implementation landing;
- full regression does not prove a unit complete without unit-specific acceptance coverage;
- WIP checkpoints record missing acceptance and exact recovery action;
- roadmap/status transitions prefer atomic Git tree commits;
- every tool/session closeout runs an acceptance/status/recovery checklist;
- `tests/test_project_status.py` enforces roadmap/handoff agreement in CI.

## Durable decisions

- Python 3.12 / Linux-first.
- Deterministic orchestrator; models are bounded workers, never state-machine owners.
- Simulation-first; real providers/runtime follow deterministic integration/recovery behavior.
- SQLite is durable/fail-closed; semantic events and completed validation/review evidence are append-only.
- Existing user checkouts are never cleaned/reset automatically; mutations happen in dedicated managed worktrees.
- Managed subprocesses use independent process groups with whole-group cleanup and sparse durable liveness metadata.
- Prefer argv execution locally; SSH remote shell requirements are isolated in the transport and receive only shell-quoted values over stdin.
- Remote transport is an adapter, never a second orchestrator.
- Remote artifact transfer is not implicit.
- A provider/tool successful exit never substitutes for deterministic evidence gates.
- Bloodborne-specific behavior stays outside orchestration core.

## Handoff protocol

At every roadmap checkpoint, record completed/in-progress unit, exact commands/results, important files/modules, material decisions, unresolved limitations, missing acceptance, and exact next recovery action. Preserve useful history and never mark an acceptance gate complete without direct evidence.
