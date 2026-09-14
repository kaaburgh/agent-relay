# Agent Instructions

## Purpose

Maintain `agent-relay`, a deterministic durable orchestrator for long-running coding/research-agent workflows. The orchestrator owns workflow state; LLMs are bounded workers inside stages. Do not turn the project into an LLM manager, issue-driven system, generic DAG engine, distributed scheduler, or Bloodborne-specific state machine.

## Sources of truth

Read before implementation work:

1. `docs/spec.md` — product/acceptance contract; never silently weaken it.
2. `ROADMAP.md` — ordered durable implementation units and acceptance gates.
3. `docs/implementation-status.md` — current durable handoff, completed work, known failures, in-progress work, and exact recovery action.
4. Existing code/tests/evidence — authoritative for what is actually implemented.

If roadmap wording conflicts with the specification, preserve the specification and repair roadmap/status. If roadmap/status disagree with code or CI evidence, fail closed: mark the unit `IN PROGRESS`, repair the durable handoff, and do not infer completion from a green regression suite.

## Durable-unit protocol

A roadmap unit is a durable implementation checkpoint, not necessarily a ChatGPT turn.

Unless the user explicitly requests a single-unit bounded pass:

1. Recover repository state and select the current `IN PROGRESS` unit, otherwise the first `READY` roadmap unit whose dependencies are `DONE`.
2. Before landing implementation for a new unit, move that unit from `READY` to `IN PROGRESS` in the durable status. Prefer an atomic multi-file commit/tree for status transitions plus related protocol metadata.
3. Implement only that unit plus minimum prerequisite repair required for correctness.
4. Add or update **unit-specific acceptance tests** that directly exercise the new behavior. A green full regression suite is necessary but is not sufficient evidence for a new unit when no test specifically covers that unit.
5. Run the unit-specific acceptance gate and the relevant/full regression suite.
6. Update `docs/implementation-status.md` with exact commands/results, decisions, failures, partial work, and the exact next recovery action.
7. Mark the unit `DONE` and the next eligible unit `READY` only when its own acceptance is demonstrated. Never mark a unit done merely because unrelated/regression tests are green.
8. Create a durable Git checkpoint/commit when permissions allow.
9. Recover from the new HEAD and continue automatically with the next `READY` unit.

### CI-fact checkpoint rule

Do not wait until the end of a tool window or roadmap unit to persist newly established acceptance facts. Whenever CI or a targeted acceptance run establishes a materially new fact — for example an exact failing test, a newly green fix, a changed blocker, or a newly bounded limitation — update `docs/implementation-status.md` with the tested HEAD, run/test identity, observed result, and exact next action **before** starting another material investigation or production change.

A new CI fact is itself a durable recovery boundary. If the session stops immediately afterward, the repository must already contain enough information for a fresh agent to continue without rereading CI logs merely to discover what failed.

### Pre-fix checkpoint rule

Before applying the next production fix after a red acceptance result, first make the red result durable. At minimum record:

- the exact tested HEAD;
- the exact failing test/check;
- the observed failure mode;
- the production location or invariant implicated, when known;
- the next intended repair and its acceptance command/test.

Do not stack a second material production fix on top of a newly discovered failure while the only record of that failure exists in transient tool output. A WIP/status-only commit is acceptable and preferred to an unrecoverable gap.

### Adversarial-audit repair loop

For final correctness/security work such as `R26`, use this loop for each material finding:

1. add or identify the targeted adversarial test and demonstrate the defect when practical (`red`);
2. durably checkpoint the red result;
3. implement the narrow production fix;
4. run the targeted test to green;
5. durably checkpoint the green result and disposition;
6. only then run the full regression/chaos suite and proceed to the next material finding.

A full-suite run does not replace the targeted red/green evidence. Conversely, a targeted green does not prove the repository is regression-free. Both are required before considering the finding closed.

### Partial-work rule

If implementation must be committed before acceptance is complete, the commit is a WIP checkpoint, not a completed unit. In the same durable checkpoint (preferably one atomic Git tree commit):

- roadmap status is `IN PROGRESS`;
- implementation status names the partial files/behavior already landed;
- missing acceptance is explicit;
- the exact recovery action is recorded.

Do not leave `ROADMAP.md`, `docs/implementation-status.md`, HEAD implementation, and CI evidence describing different units. `tests/test_project_status.py` is a CI guard for this metadata contract and must remain green.

### Closeout checklist

Before ending a tool-execution window, turn, or long work session, run this checklist even if the current unit is incomplete:

- Is every materially new CI/acceptance fact already durable in `docs/implementation-status.md`?
- Is the unit-specific acceptance test present and has it run?
- Has the full/relevant regression suite run after the latest production change?
- Do `ROADMAP.md` and `docs/implementation-status.md` agree on completed, in-progress, and next unit?
- Does HEAD clearly represent either a completed checkpoint or a documented WIP checkpoint?
- Is the exact next recovery action durable in `docs/implementation-status.md`?

If any answer is no and tools still work, repair the durable checkpoint before doing more feature work. If tools become objectively unavailable, report the missing closeout item explicitly; do not claim the unit is complete.

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
