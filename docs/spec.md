# Product and Acceptance Contract

This document preserves the maintainer's original requirements for `agent-relay`. The roadmap may split these requirements into smaller implementation units, but it must not silently weaken or omit them.

## Goal

Build a small durable external orchestrator for long-running coding/research-agent workflows.

It is not issue-tracker-driven and is not an LLM manager. The orchestrator itself is deterministic software; models are workers inside explicit stages.

Initial real-world target:

- OpenAI Codex / Luna Max performs long implementation and experimental work;
- Claude / Opus performs independent adversarial code/evidence review;
- stages may invoke expensive tools such as shadPS4 builds and Bloodborne experiments;
- tasks may run unattended for many hours;
- providers may temporarily be unavailable;
- orchestrator may restart;
- agent processes may fail, hang, or finish without a usable handoff;
- review findings may send work back to the writer multiple times.

The first implementation must be useful for this workflow while keeping Bloodborne-specific state transitions out of orchestration core.

A major deliverable is a realistic simulation environment that exercises orchestration without shadPS4, Bloodborne, GPU, Claude/Codex login, or model quota.

## Core lifecycle

Normal lifecycle:

```text
READY -> WORK -> VALIDATE -> REVIEW -> DONE
                         REVIEW --REQUEST_CHANGES--> REWORK -> VALIDATE -> REVIEW
```

Durable states must also cover at least:

- `WAITING_PROVIDER`
- `BLOCKED`
- `FAILED`
- `CANCELLED`

Use additional internal states only when they materially simplify correctness.

## Scope constraints

Do not build:

- a generic Airflow replacement;
- Kubernetes infrastructure;
- a distributed queue;
- an issue tracker;
- a web application;
- a generic arbitrary DAG language;
- a multi-tenant SaaS;
- an LLM-powered orchestration layer.

Prefer explicit understandable Python to excessive abstraction. Python 3.12 and Linux are target assumptions. Use `asyncio`/subprocesses where useful. SQLite is preferred for durable state unless a clearly better simple option emerges.

## Provider abstractions

### Codex writer

Implement a clean adapter capable of invoking an existing authenticated Codex CLI/session non-interactively. Model/reasoning configuration must live in configuration, not core state logic, e.g. conceptually:

```yaml
writer:
  provider: codex
  model: gpt-5.6-luna
  reasoning_effort: max
```

Exact CLI syntax must be isolated in the adapter. Capture when exposed:

- command/argv;
- PID;
- start/end timestamps;
- exit status;
- stdout/stderr;
- session/run identifier;
- final handoff/result;
- token/usage information;
- obvious rate-limit/provider-unavailable signals.

Never put credentials into repository/config examples.

### Claude reviewer

Implement an adapter for an already-authenticated local Claude CLI. Configuration must allow provider/model selection without leaking syntax into core logic, conceptually:

```yaml
reviewer:
  provider: claude
  model: opus-5
```

Reviewer operates against a dedicated checkout/worktree at the exact candidate SHA whenever practical and is read-only by workflow policy.

Structured result contract:

```json
{
  "verdict": "REQUEST_CHANGES",
  "findings": [
    {
      "severity": "HIGH",
      "title": "...",
      "file": "...",
      "symbol": "...",
      "problem": "...",
      "failure_scenario": "...",
      "required_action": "..."
    }
  ],
  "summary": "..."
}
```

Allowed verdicts:

- `APPROVE`
- `APPROVE_WITH_FOLLOWUPS`
- `REQUEST_CHANGES`
- `BLOCKED_BY_MISSING_EVIDENCE`

Allowed severities:

- `CRITICAL`
- `HIGH`
- `MEDIUM`
- `LOW`

Parsing must be robust. Malformed output must never be silently treated as approval.

## Review independence

Independent review is first-class. Reviewer receives a bounded package such as:

- task specification;
- acceptance criteria;
- baseline SHA;
- candidate SHA;
- git diff;
- relevant source paths;
- test results;
- runtime evidence paths;
- commands used;
- writer claims to verify.

Writer claims are claims, not ground truth. Do not pass private writer reasoning/history. Prefer a separate reviewer worktree at candidate SHA. Reviewer must not modify writer worktree.

`REQUEST_CHANGES` findings go back to a writer/rework stage. Re-review must use a fresh reviewer invocation rather than a persistent reviewer approving its own prior conclusions.

## Local task representation and operator commands

No issue tracker is required. A local task must contain enough information for:

- repository;
- baseline/ref;
- writer instructions;
- validation instructions;
- review instructions;
- acceptance criteria;
- runtime/tool requirements;
- maximum correction rounds.

Support existing repositories/worktrees and automatically managed isolated writer/reviewer worktrees.

CLI should provide equivalents of:

```text
agent-relay task create task.yaml
agent-relay run <task-id>
agent-relay status <task-id>
agent-relay events <task-id>
agent-relay resume <task-id>
agent-relay cancel <task-id>
agent-relay doctor
```

Exact UX may improve on these names.

`status` should quickly answer: task, current stage, candidate SHA, active process/provider, time in stage, last event, review status, next retry, held resources.

`doctor` should check: git, Python, configured Codex executable, configured Claude executable, repository paths, provider auth usability where detectable, runtime tool paths, and SQLite writability.

## Durable state and audit history

Persist enough information that killing/restarting the orchestrator at almost any point does not lose workflow state. At minimum persist:

- task ID;
- workflow stage;
- stage attempt number;
- repository;
- baseline SHA;
- current candidate SHA;
- writer run IDs;
- review run IDs;
- commands;
- process state / last known PID;
- artifact paths;
- review verdict/findings;
- timestamps;
- provider failures;
- retry timestamps;
- validation outcomes;
- event history.

Use transactions where needed. Every transition must be explicit/auditable. Maintain append-only event history in addition to current task state.

Representative semantic events:

- `task_created`
- `stage_started`
- `provider_process_started`
- `provider_process_finished`
- `candidate_commit_detected`
- `validation_started`
- `validation_finished`
- `review_started`
- `review_finished`
- `review_requested_changes`
- `provider_unavailable`
- `retry_scheduled`
- `orchestrator_recovered`
- `task_completed`

Do not flood the event log with repetitive "still running" polling events.

## Restart recovery

Recovery after normal shutdown, SIGTERM, process crash, or machine restart is critical.

Do not automatically rerun a successful expensive stage just because the orchestrator missed its final callback. On recovery inspect durable evidence such as:

- candidate commit;
- stage result/artifact file;
- validation output;
- provider result;
- process existence.

Make completion idempotent where practical. Document states that cannot be recovered automatically.

## Provider unavailability

Provider unavailability is normal, not corruption. Use durable `WAITING_PROVIDER` metadata containing provider, reason, first-seen, last-attempt, next-retry, and attempt-count. Support bounded/exponential retry. Provider-specific detection belongs in adapters. Do not spin continuously.

## Timeouts and stalls

Distinguish:

- stage timeout;
- process crash;
- provider unavailable;
- apparent stall;
- successful long-running operation.

Long Luna tasks may legitimately run for hours. No short arbitrary global timeout. Support per-stage configuration and low-cost liveness/watchdog logic.

## Exclusive runtime resources

Real workflow may have scarce mutable resources such as one GPU, Bloodborne instance, DISPLAY, or known save/profile.

Implement a generic persisted lease/semaphore concept. Example named resource:

```yaml
resources:
  - bloodborne-runtime
```

A capacity-1 resource must prevent two tasks/stages from invoking the real runtime simultaneously. Core must know only resource name/capacity, not Bloodborne semantics. Lease ownership must persist or be safely reconstructed after restart.

## External tool / validation abstraction

Not all work is an LLM run. Explicit external-tool stages must capture:

- argv;
- cwd;
- environment additions;
- timeout;
- exit status;
- stdout/stderr;
- artifacts;
- structured result if available.

Same abstraction should support unit tests, builds, perf experiments, shadPS4/Bloodborne runs, and custom analysis scripts.

When deterministic machine-readable results exist, models do not decide whether validation passed.

## Real shadPS4/Bloodborne adapter

Implement a first real tool adapter outside orchestration core. Known existing runner shape:

```text
/home/ubuntu/bb-shadPS4-correctness-instrumentation/tools/run_bb_death_reload_benchmark.py
```

Do not assume that path exists on the development machine; paths belong in external configuration.

Adapter must understand at least:

- run ID;
- requested cycles;
- runner exit status;
- `runner-status.json` when present;
- `cycles.csv`/JSON when present;
- `summary.md`/evidence path;
- completed cycle count.

Produce a small normalized result. Do not reimplement the Bloodborne menu/death FSM; external harness remains authoritative.

## Simulation system — major deliverable

The complete orchestrator must be testable quickly/deterministically without model quota, Claude/Codex login, GPU, shadPS4, or Bloodborne.

### Simulated writer

Declaratively configurable fake provider/process must support:

- sleep for N seconds;
- produce candidate commit;
- produce no commit;
- success;
- failure;
- hang;
- crash;
- malformed result;
- provider rate limit;
- temporary unavailability;
- partial work then crash;
- success while orchestrator restarts/is absent.

Where practical use tiny real temporary Git repositories so worktree/commit behavior is real.

### Simulated reviewer

Support:

- `APPROVE`;
- `REQUEST_CHANGES`;
- `APPROVE_WITH_FOLLOWUPS`;
- `BLOCKED_BY_MISSING_EVIDENCE`;
- malformed output;
- process failure;
- hang;
- provider unavailable.

Support staged responses by review attempt, e.g. first request changes then approve.

### Simulated expensive shadPS4/Bloodborne-style tool

Must realistically exercise process supervision, not just return a boolean. It must support:

- configurable duration;
- resource lease consumption;
- incremental progress artifacts;
- `runner-status.json`;
- `cycles.csv`;
- `summary.md`;
- N/N successful cycles;
- fail at cycle K;
- hang at cycle K;
- crash;
- success with incomplete evidence;
- nonzero exit;
- leave child process behind;
- respond to SIGTERM;
- ignore SIGTERM and require SIGKILL.

Example output shape:

```text
evidence/<run-id>/
    runner-status.json
    cycles.csv
    summary.md
```

Can simulate metrics such as `stack_vma_count`, `vm_size_delta`, `image_live_count`, `gpu_memory` so acceptance criteria can be exercised. Goal is orchestration realism, not shadPS4 emulation.

### Resource contention

Start two simulated tasks using capacity-1 `bloodborne-runtime` and prove simulated runtime validators do not overlap. Other non-runtime stages may proceed concurrently when architecture permits.

## Required deterministic integration scenarios

Automated integration tests must cover at least:

1. Happy path: writer -> commit -> validator pass -> reviewer `APPROVE` -> `DONE`.
2. Review correction: initial review `REQUEST_CHANGES` with HIGH finding -> rework -> validation -> fresh reviewer `APPROVE` -> `DONE`; review attempt IDs differ.
3. Orchestrator dies while writer works: terminate orchestrator, writer finishes/candidate exists, restart, recover without unnecessary writer rerun, continue.
4. Orchestrator dies during external validation: restart safely, no duplicate expensive validator.
5. Provider unavailable: `WAITING_PROVIDER` -> scheduled retry -> availability restored -> workflow resumes.
6. Reviewer malformed output: successful process exit with invalid schema must not approve; explicit retry/fail/block policy.
7. Validation incomplete evidence: exit zero but requested 3 cycles yields only 2 valid records; must not pass.
8. Hanging tool: watchdog detects configured stall/timeout, cleans process correctly, preserves evidence.
9. Resource lease: two tasks request capacity-1 runtime; prove only one fake runtime process runs at once.
10. Repeated review problems: at least two rework/review rounds before approval; full history remains.
11. Correction limit: reviewer always requests changes; task becomes `BLOCKED` after configured maximum rather than looping.
12. Process succeeds while supervisor unavailable: worker starts, orchestrator disappears, worker completes and writes commit/evidence, orchestrator returns and recognizes checkpoint rather than rerunning writer.

## Chaos/stress testing

After deterministic scenarios, implement a small reproducible chaos runner with random injections such as:

- process crash;
- orchestrator restart;
- temporary provider failure;
- review `REQUEST_CHANGES`;
- tool failure;
- delayed callback/result file.

Use deterministic random seed and print/record it on failure. Required demonstration: at least 100 short workflows in seconds/minutes.

Verify invariants after each run.

## State-machine invariants

At minimum enforce/test:

1. At most one writer owns a task at once.
2. Reviewer never writes to writer workspace.
3. `REVIEW` cannot begin without a frozen candidate SHA.
4. `DONE` cannot be reached without configured review gate succeeding.
5. If validation is mandatory, `DONE` requires validation for the same candidate SHA/generation reviewed.
6. `REQUEST_CHANGES` creates a new candidate generation.
7. Re-review applies to the new generation.
8. Historical reviews remain immutable/auditable.
9. Stage retry never silently overwrites prior attempt artifacts.
10. Exclusive resource never exceeds configured active holders/capacity.
11. Restart recovery cannot regress a completed durable stage without explicit operator action.
12. Malformed provider output cannot be interpreted as success.

Additional chaos invariants include no impossible stage history, no simultaneous task writers, no approval without valid reviewer output, no `DONE` without required successful validation, no silently lost candidate commit, and no retry duplicating a committed successful stage.

## Candidate generations

Committed rework creates explicit generations, e.g.:

```text
candidate 1 / SHA aaa / validation V1 / review R1 -> REQUEST_CHANGES
candidate 2 / SHA bbb / validation V2 / review R2 -> APPROVE
```

Never approve candidate `bbb` using validation from `aaa`.

## Artifact layout

Use an understandable layout conceptually like:

```text
tasks/<task-id>/
    task.yaml
    state/
    events.jsonl
    attempts/
        writer-001/
        validation-001/
        review-001/
        writer-002/
        validation-002/
        review-002/
```

Exact layout may differ. Each attempt preserves inputs, command/config, stdout/stderr, result, timestamps, and candidate SHA where applicable. Never dump provider secrets.

## Configuration

Use human-readable configuration for provider/runtime setup. Keep task-specific experimental prompts out of global config. Secrets come from environment/authenticated CLIs, not committed config.

Provide examples for:

- Codex Luna writer;
- Claude Opus reviewer;
- local simulated providers;
- remote/real shadPS4 runner;
- runtime resource capacity 1.

Do not depend on one user's absolute paths by default.

## Remote execution

Permanent orchestrator and GPU/shadPS4 node may differ. Tool abstraction must keep local and remote execution cleanly separable. A minimal SSH command runner is desirable if it does not destabilize core work; otherwise preserve a clean extension point and document the limitation. Do not build a distributed cluster scheduler.

## Logging and observability

Use structured logs and semantic events. Avoid repetitive polling chatter. Long-running process liveness may update internal timestamps without flooding event history. Operator-visible output should emphasize state transitions/problems.

## Security and safety

- no credential/token logging;
- reviewer read-only by policy and preferably filesystem-safe where practical;
- validate repository paths;
- careful shell quoting;
- prefer argv arrays over `shell=True`;
- kill complete managed process groups;
- handle SIGINT/SIGTERM;
- never automatically hard-reset/clean a user's existing checkout;
- never discard unknown dirty worktree;
- use dedicated managed worktrees/directories.

## Documentation deliverables

Write:

- `README.md`
- `docs/architecture.md`
- `docs/state-machine.md`
- `docs/providers.md`
- `docs/simulation.md`
- `docs/recovery.md`
- `docs/shadps4-example.md`

README must contain a five-minute simulation quick start. A developer must be able to clone and run happy path, `REQUEST_CHANGES` path, and restart-recovery path without real providers, shadPS4, GPU, or credentials.

`docs/shadps4-example.md` must explain plugging the existing Bloodborne harness into external-tool adapter without copying its internal FSM into agent-relay.

## Testing expectations

Use normal unit tests for small components and real subprocess/integration tests for orchestration behavior. Important concurrency/recovery behavior must execute for real using temporary subprocesses, Git repos/worktrees, and SQLite. Tests should remain reasonably fast; simulation stages should be sub-second/few-second.

## Required implementation order

Simulation completion must not be blocked by real provider integration. Preferred order is:

1. repository/package/CLI;
2. persistent task/event store;
3. explicit state machine;
4. simulated writer/reviewer/tool;
5. happy path;
6. rework generations;
7. restart recovery;
8. leases;
9. failure/provider-unavailable handling;
10. chaos suite;
11. real Codex adapter;
12. real Claude adapter;
13. shadPS4 adapter/config;
14. complete tests/stress;
15. final architecture/correctness review.

`ROADMAP.md` refines this into smaller durable units.

## Final self-review

Before declaring success, inspect and fix meaningful findings around:

- race conditions;
- non-atomic transitions;
- duplicate stage launches after restart;
- orphan processes;
- incorrect candidate/review association;
- lease leaks;
- stale approval/validation;
- loss of historical evidence;
- unsafe Git operations;
- shell injection;
- unbounded logs/state.

## Required final demonstration

Actually run and report commands/results for:

1. full automated test suite;
2. happy-path simulation;
3. `REQUEST_CHANGES -> rework -> APPROVE`;
4. orchestrator restart while writer active;
5. restart after writer completed while orchestrator absent;
6. provider unavailable -> retry -> success;
7. two tasks contending for one simulated capacity-1 runtime;
8. malformed reviewer result;
9. repeated `REQUEST_CHANGES` until correction limit;
10. randomized chaos/stress with at least 100 workflows.

Do not merely claim support; run them.

## Definition of done

Final handoff must report:

1. repository structure;
2. architecture summary;
3. state-machine summary;
4. persistent storage schema;
5. provider interfaces;
6. real Codex adapter status;
7. real Claude adapter status;
8. simulated provider/tool capabilities;
9. resource/lease design;
10. restart-recovery behavior;
11. commands/results for required demonstrations;
12. chaos workflow count and seed(s);
13. known limitations;
14. exact next steps for authenticated Codex/Luna, authenticated Claude/Opus, and real remote shadPS4/Bloodborne runner.

The key acceptance criterion is not that the code looks plausible. The simulation environment must demonstrate that the orchestrator survives the same classes of failures observed in long-running shadPS4 research while preserving mandatory independent review and correct candidate/evidence provenance.
