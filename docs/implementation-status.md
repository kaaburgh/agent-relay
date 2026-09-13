# Implementation Status

## Current checkpoint

Repository bootstrap is complete. The orchestrator itself is not implemented yet.

Completed:

- `R00` agent-friendly repository scaffold;
- Python 3.12 package/CLI shell;
- durable product contract in `docs/spec.md`;
- bounded implementation roadmap in `ROADMAP.md`;
- agent execution rules in `AGENTS.md`;
- smoke test and basic CI configuration.

Next bounded unit: `R01 — Task/config domain model and CLI skeleton`.

## Current product state

No workflow execution, SQLite persistence, providers, subprocess supervision, leases, simulation, recovery, or shadPS4 integration is claimed yet.

This is intentional: future agents should implement one roadmap unit at a time and leave a tested durable checkpoint after each pass.

## Durable decisions

- Python 3.12 / Linux-first.
- Deterministic orchestrator; models are bounded workers, never owners of state machine.
- Simulation-first development. Real Codex/Claude/shadPS4 integration comes only after deterministic integration/recovery behavior is proven.
- SQLite remains the preferred durable store pending a concrete reason to change it.
- Bloodborne-specific behavior stays outside orchestration core.
- No issue tracker is required for task execution or development routing; `ROADMAP.md` is the implementation work queue.
- No repository license has been selected. Do not add one without explicit maintainer direction.

## Handoff protocol

At the end of every roadmap pass, update this file with:

- roadmap unit completed/in progress;
- exact commands/tests run and their result;
- new important files/modules;
- material design decisions;
- unresolved failures/limitations;
- exact next `READY` unit.

Do not erase useful history simply to make this file look clean; condense old completed entries only when the durable Git history and roadmap make them redundant.

## Bootstrap verification

Expected basic command after this bootstrap:

```bash
python -m unittest discover -s tests -v
```

The smoke test only verifies that the package shell/version is importable. It does not exercise orchestrator behavior.
