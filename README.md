# agent-relay

`agent-relay` is a small durable deterministic orchestrator for long-running coding and research-agent workflows. Models and external tools are bounded workers; SQLite-backed software owns workflow state, retries, evidence provenance, recovery, and resource leases.

The initial real-world target is a shadPS4/Bloodborne research workflow with a Codex writer, an independent Claude reviewer, and expensive external validation. Bloodborne gameplay/death/reload state stays in the external harness, not in orchestration core.

## What is implemented

- durable SQLite task/state/attempt/event history;
- managed Git writer/reviewer worktrees and candidate generations;
- real subprocess supervision, timeout/stall/cancellation and process-group cleanup;
- deterministic simulated writer/reviewer/expensive validator;
- provider wait/retry, restart recovery, and persisted resource leases;
- operator CLI and `doctor`;
- authenticated Codex writer adapter and independent read-only Claude reviewer adapter;
- external shadPS4/Bloodborne evidence adapter;
- minimal SSH transport for external tools;
- twelve deterministic integration scenarios plus a seeded 100-workflow chaos sweep.

The top-level ordinary `agent-relay run` real-provider composition remains intentionally fail-closed; `run --simulation` is the operator-ready end-to-end backend. The real provider/tool adapters are available as explicit components and are finalized/integrated under the remaining correctness work rather than faking an end-to-end success.

## Install

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
agent-relay --version
```

No provider credentials, GPU, shadPS4, or Bloodborne installation is required for simulations.

## Five-minute simulation quick start

The repository ships self-contained real-process simulations. These commands create temporary Git repositories/SQLite state and do not use Codex, Claude, SSH, or a GPU.

Happy path — writer commits, validator produces N/N evidence, reviewer approves, task reaches `DONE`:

```bash
python -m unittest \
  tests.test_orchestrator_happy_path.HappyPathIntegrationTests.test_writer_validator_independent_reviewer_reaches_done_with_exact_provenance -v
```

`REQUEST_CHANGES` path — generation 1 is rejected, feedback enters fresh rework, generation 2 is revalidated and freshly reviewed:

```bash
python -m unittest \
  tests.test_orchestrator_rework.ReworkIntegrationTests.test_request_changes_flows_to_fresh_rework_generation_and_fresh_review -v
```

Restart recovery — a separately running writer finishes while the first Store/orchestrator is absent and the reopened orchestrator reuses the durable checkpoint without duplicating work:

```bash
python -m unittest \
  tests.test_writer_recovery.WriterRecoveryTests.test_separate_worker_finishes_after_store_closes_and_new_store_recovers_once -v
```

Run all tests, including the 100-workflow seeded chaos sweep:

```bash
python -m unittest discover -s tests -v
```

## Operator CLI simulation

`examples/config.simulated.yaml` is an explicit simulation configuration. A persisted task still needs a real local Git repository because candidate/worktree semantics are intentionally real. Given a task YAML whose `repository` points to such a repository:

```bash
agent-relay doctor --config examples/config.simulated.yaml --task /path/to/task.yaml
agent-relay task create /path/to/task.yaml --config examples/config.simulated.yaml
# Use the task_id printed above:
agent-relay run TASK_ID --simulation --config examples/config.simulated.yaml
agent-relay status TASK_ID --config examples/config.simulated.yaml
agent-relay events TASK_ID --config examples/config.simulated.yaml
```

`run` without `--simulation` currently fails closed instead of pretending the real Codex/Claude/tool pipeline is installed end-to-end.

## Documentation

- [Architecture](docs/architecture.md)
- [State machine and invariants](docs/state-machine.md)
- [Providers and SSH transport](docs/providers.md)
- [Simulation](docs/simulation.md)
- [Recovery](docs/recovery.md)
- [shadPS4/Bloodborne adapter](docs/shadps4-example.md)
- [Required integration scenarios](docs/integration-scenarios.md)
- [Product/acceptance contract](docs/spec.md)
- [Roadmap](ROADMAP.md)
- [Current durable handoff](docs/implementation-status.md)

## Safety model

The project avoids `shell=True` for local managed processes, uses dedicated process groups, never automatically hard-resets/cleans an existing user checkout, keeps reviewer worktrees separate, redacts credential-shaped data from artifacts, and fails closed on malformed provider output or incomplete deterministic evidence.

SSH is deliberately a minimal transport. Remote argv/cwd/environment values are POSIX-quoted and sent to `sh -s` over SSH stdin; task-controlled values are not interpolated into a local shell. SSH configuration such as `ssh_args` is trusted operator configuration. The transport does **not** transparently copy remote evidence: use a path visible to both sides (for example a shared filesystem) or add an explicit transfer layer. Killing the local SSH process also does not promise to kill arbitrary daemonized remote descendants.

## Development protocol

Read `AGENTS.md`, `docs/spec.md`, `ROADMAP.md`, and `docs/implementation-status.md` before implementation work. Roadmap units have unit-specific acceptance gates. Partial work is durably marked `IN PROGRESS`; `tests/test_project_status.py` rejects roadmap/handoff drift.

## License

No license has been selected. Do not add or change a license without explicit maintainer direction.
