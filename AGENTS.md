# Agent Instructions

## Purpose

Maintain `agent-relay`, a deterministic durable orchestrator for long-running coding/research-agent workflows.

The orchestrator owns workflow state. LLMs are bounded workers inside stages. Do not turn the project into an LLM manager, issue-driven system, generic DAG engine, distributed scheduler, or Bloodborne-specific state machine.

## Sources of truth

Read these before implementation work:

1. `docs/spec.md` — product/acceptance contract; do not silently weaken it.
2. `ROADMAP.md` — ordered bounded implementation units and their acceptance gates.
3. `docs/implementation-status.md` — current durable handoff, completed work, known failures, and next unit.
4. Existing code/tests/evidence — authoritative for what is actually implemented.

If roadmap wording conflicts with the specification, preserve the specification and repair the roadmap/status as part of the current bounded pass.

## Bounded-work protocol

Unless the user explicitly selects another unit:

1. Recover repository state: read the files above and inspect the current branch/status/history.
2. Select only the first `READY` roadmap item whose dependencies are complete.
3. Mark it `IN PROGRESS` in `docs/implementation-status.md` before broad implementation work when practical.
4. Implement only that unit plus the minimum prerequisite repair needed to make it correct.
5. Run the unit's stated checks and relevant existing regression tests.
6. Record commands/results and material design decisions in `docs/implementation-status.md`.
7. Mark the roadmap item complete only when its acceptance gate is demonstrably satisfied.
8. Create a durable Git checkpoint/commit when repository permissions allow it.
9. Stop. Do not opportunistically begin the next roadmap unit in the same pass unless explicitly requested.

A pass that only designs future work is not complete when the roadmap unit calls for code/tests.

## Implementation constraints

- Python 3.12, Linux-first.
- Prefer explicit Python, `asyncio`, `sqlite3`, and argv-based subprocess APIs over abstraction-heavy frameworks.
- SQLite is the default durable store unless a bounded roadmap unit establishes a concrete reason to change it.
- State transitions must be explicit and auditable; append-only semantic events are mandatory.
- Candidate SHA, validation evidence, and review verdict must belong to the same candidate generation.
- Malformed provider output is failure/blocking input, never implicit approval.
- Reviewer receives a bounded evidence package, not writer private reasoning/history, and must not modify the writer worktree.
- Simulation and real subprocess semantics come before real provider integration.
- Real Codex/Claude CLI syntax belongs only in provider adapters.
- Bloodborne/shadPS4 behavior belongs only in tool adapters/configuration; never copy the external harness FSM into core orchestration.
- Exclusive resources are generic persisted leases/semaphores.
- Recovery must inspect durable evidence before deciding to rerun a stage.
- Do not emit repetitive liveness events; persist low-cost heartbeat/progress metadata separately when needed.

## Safety

- Never commit credentials, tokens, cookies, provider auth material, private keys, or local environment files.
- Prefer argv arrays; do not use `shell=True` for managed processes without a documented unavoidable reason.
- Start managed subprocesses in their own process group/session and clean up the complete group.
- Handle SIGINT/SIGTERM deliberately.
- Never `git reset --hard` or `git clean` a user's existing checkout automatically.
- Never discard an unknown dirty worktree.
- Managed writer/reviewer worktrees must be dedicated paths.
- Do not decide or change repository licensing without explicit maintainer direction.

## Tests

Default repository check:

```bash
python -m unittest discover -s tests -v
```

Integration/recovery tests must use real temporary subprocesses, temporary Git repositories/worktrees, and SQLite files where those semantics are under test. Do not mock away the behavior the test is meant to prove.
