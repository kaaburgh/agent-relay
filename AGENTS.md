# Agent Instructions

## Purpose

Maintain `agent-relay`, a deterministic durable orchestrator for long-running coding/research-agent workflows. The orchestrator owns workflow state; LLMs are bounded workers inside stages. Do not turn the project into an LLM manager, issue-driven system, generic DAG engine, distributed scheduler, or Bloodborne-specific state machine.

## Sources of truth

Read before implementation work:

1. `docs/spec.md` — product/acceptance contract; never silently weaken it.
2. `ROADMAP.md` — ordered durable implementation units and acceptance gates.
3. `docs/implementation-status.md` — current durable handoff, completed work, known failures, and next unit.
4. Existing code/tests/evidence — authoritative for what is actually implemented.

If roadmap wording conflicts with the specification, preserve the specification and repair roadmap/status.

## Durable-unit protocol

A roadmap unit is a durable implementation checkpoint, not necessarily a ChatGPT turn.

Unless the user explicitly requests a single-unit bounded pass:

1. Recover repository state and select the first `READY` roadmap unit whose dependencies are `DONE`.
2. Implement only that unit plus minimum prerequisite repair required for correctness.
3. Run its acceptance gate and relevant regression tests.
4. Update `docs/implementation-status.md` with exact commands/results, decisions, failures, and next unit.
5. Mark the unit `DONE` and the next eligible unit `READY` only when acceptance is demonstrated.
6. Create a durable Git checkpoint/commit when permissions allow.
7. Recover from the new HEAD and continue automatically with the next `READY` unit.

Stop only on Definition of Done, a genuine external blocker, an unsatisfied acceptance gate after reasonable diagnosis, a safety/permission boundary, or objective tool/environment impossibility. A successful unit or commit is not itself a reason to stop.

Never commit knowingly broken work as a completed unit. If blocked, preserve completed work, record the exact failure and recovery action in implementation status, and fail closed.

## Implementation constraints

- Python 3.12, Linux-first.
- Prefer explicit Python, `asyncio`, `sqlite3`, and argv-based subprocess APIs over abstraction-heavy frameworks.
- SQLite is the default durable store unless a bounded unit proves a better simple option.
- State transitions are explicit/auditable; append-only semantic events are mandatory.
- Candidate SHA, validation evidence, and review verdict must belong to the same candidate generation.
- Malformed provider output is failure/blocking input, never implicit approval.
- Reviewer gets a bounded evidence package, not writer private reasoning/history, and must not modify writer worktree.
- Simulation and real subprocess semantics precede real provider integration.
- Codex/Claude CLI syntax belongs only in provider adapters.
- Bloodborne/shadPS4 behavior belongs only in tool adapters/configuration; never copy the external harness FSM into core orchestration.
- Exclusive resources are generic persisted leases/semaphores.
- Recovery inspects durable evidence before deciding to rerun a stage.
- Do not emit repetitive liveness events; persist low-cost heartbeat/progress metadata separately when needed.

## Safety

- Never commit credentials, tokens, cookies, provider auth material, private keys, or local environment files.
- Prefer argv arrays; do not use `shell=True` for managed processes without a documented unavoidable reason.
- Start managed subprocesses in their own process group/session and clean up the complete group.
- Handle SIGINT/SIGTERM deliberately.
- Never `git reset --hard` or `git clean` a user's existing checkout automatically.
- Never discard an unknown dirty worktree.
- Managed writer/reviewer worktrees must be dedicated paths.
- Do not decide/change repository licensing without explicit maintainer direction.

## Tests

Default repository check:

```bash
python -m unittest discover -s tests -v
```

Integration/recovery tests must use real temporary subprocesses, Git repositories/worktrees, and SQLite files when those semantics are under test. Do not mock away the behavior the test is meant to prove.
