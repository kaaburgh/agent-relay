# agent-relay

`agent-relay` is a small durable external orchestrator for long-running coding and research-agent workflows.

The orchestrator is deterministic software. Models are workers inside explicit workflow stages; no LLM owns the workflow state machine.

The initial real-world target is a shadPS4/Bloodborne research workflow with a Codex/Luna writer, an independent Claude/Opus reviewer, and expensive external validation runs. Bloodborne-specific behavior must stay outside the orchestration core.

## Status

The repository is scaffolded for Python 3.12 and organized for bounded agent work. Product requirements are preserved in [`docs/spec.md`](docs/spec.md), implementation is decomposed in [`ROADMAP.md`](ROADMAP.md), and the durable handoff is [`docs/implementation-status.md`](docs/implementation-status.md).

Implementation of the orchestrator itself has not started yet beyond the repository/package shell.

## Development

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python -m unittest discover -s tests -v
agent-relay --version
```

No Codex, Claude, shadPS4, GPU, or provider credentials should be required for the simulation-first development path.

## Agent workflow

Before changing code, read in this order:

1. `AGENTS.md`
2. `docs/spec.md`
3. `ROADMAP.md`
4. `docs/implementation-status.md`

Work one bounded roadmap item at a time. A completed item must leave a durable repository checkpoint with tests/evidence and updated status. Do not start the next item in the same pass unless explicitly requested.

## Core principles

- deterministic orchestrator; models are workers, not managers;
- SQLite-backed durable state and append-only event history;
- explicit candidate generations and evidence provenance;
- independent read-only review of frozen candidate SHAs;
- simulation before real providers and real GPU/runtime integration;
- restart recovery must prefer durable evidence over blind reruns;
- exclusive external resources use generic persisted leases;
- argv-based subprocess execution, process-group cleanup, no credential logging;
- no issue tracker, web app, arbitrary DAG language, distributed queue, or Bloodborne-specific core transitions.

## License

No license has been selected yet. Do not add or change a license without an explicit maintainer decision.
