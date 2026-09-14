# Architecture

## Boundary

`agent-relay` is deterministic orchestration software. It owns durable workflow state, candidate/evidence provenance, retries, recovery decisions and resource ownership. Codex, Claude and external tools are workers behind explicit adapters; none owns the state machine.

The core intentionally avoids an issue tracker, arbitrary DAG engine, distributed queue, web UI, or Bloodborne-specific workflow states.

## Main components

- `models.py` / `config.py`: typed task, provider, runner and resource configuration.
- `store.py`: SQLite schema, transactions, current task state and append-only semantic events.
- `workflow.py`: legal lifecycle transitions and exact candidate/validation/review invariants.
- `artifacts.py`: immutable attempt directories, snapshots, stdout/stderr/results and redaction.
- `git_workspace.py`: isolated writer branches/worktrees, detached reviewer worktrees and candidate generations.
- `supervisor.py` / `watchdog.py`: argv-based process launch, PID/PGID durability, timeout/stall/cancel and whole-local-process-group cleanup.
- `resource_leases.py`: persisted named-capacity leases for scarce runtimes.
- `provider_retry.py`: durable `WAITING_PROVIDER` metadata and bounded retry.
- `orchestrator.py`: deterministic composition of simulated workers and evidence gates.
- provider adapters: `codex_writer.py`, `claude_reviewer.py`.
- external-tool adapters: `simulated_validator.py`, `shadps4_validator.py`.
- remote transport: `ssh_runner.py`.
- operator surfaces: `operator.py`, `execution.py`, `cli.py`.

## Durable data model

SQLite stores tasks, attempts, candidate generations, process metadata, validations, reviews, resources/leases, provider waits, artifacts and semantic events. Critical state transitions use transactions and compare-and-set expectations so stale supervisors cannot overwrite newer state.

Events are append-only and describe meaningful changes; heartbeat/liveness updates do not flood semantic history. Attempt identity and completed validation/review/artifact history are protected against accidental mutation.

## Candidate/evidence provenance

A committed writer result becomes an explicit candidate generation `(generation, SHA)`. Mandatory validation and independent review are recorded against that exact generation/SHA. `DONE` cannot consume validation or review from another generation.

Reviewer work happens in a separate detached worktree at the frozen candidate SHA. Writer claims are included only as untrusted claims in the bounded review package.

## Process model

Managed local processes use argv execution, independent sessions/process groups, real stdout/stderr files and durable process metadata. A stage may have a wall-clock timeout and, for evidence-producing tools, a separate progress-stall watchdog. Timeout, stall, cancellation, provider unavailability and ordinary process failure are distinct outcomes.

## Remote transport

`SSHExternalToolRunner` preserves the same local `ManagedProcess` boundary. It launches `ssh` as an argv process and sends a POSIX-quoted remote launch script to constant `sh -s` over stdin. It is transport only: no remote scheduler, no remote SQLite state machine, and no implicit artifact transfer.

Because evidence parsing remains local, a remote external tool must write evidence to a path visible to the orchestrator (for example shared storage) or be paired with an explicit transfer layer outside the current SSH transport.

## Recovery philosophy

Recovery reconciles durable evidence before rerunning work. Candidate commits, provider result files, validation evidence, process state and leases are inspected to decide whether a stage completed, is still owned by a live process, or is ambiguous. Ambiguous expensive-runtime ownership fails closed and retains its lease rather than risking duplicate execution.

See [recovery.md](recovery.md) and [state-machine.md](state-machine.md) for details.
