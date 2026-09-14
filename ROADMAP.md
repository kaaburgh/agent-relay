# Roadmap

This roadmap decomposes `docs/spec.md` into durable implementation units. It is not an issue tracker. Each unit is a checkpoint/transaction; unless a single-unit pass is explicitly requested, a successful checkpoint is followed automatically by the next `READY` unit.

Status legend: `DONE`, `READY`, `IN PROGRESS`, `BLOCKED`, `FUTURE`.

Selection rule: continue the single `IN PROGRESS` item if one exists; otherwise take the first `READY` item whose dependencies are `DONE`. Every unit remains subject to the complete product/acceptance contract in `docs/spec.md`; this file must not be used to weaken it. A unit may be `IN PROGRESS` when implementation has landed but its own acceptance gate is not yet demonstrated.

## R00 — Agent-friendly repository scaffold — DONE
Python 3.12 package shell, CI/test shell, durable spec, roadmap, handoff and agent instructions.

## R01 — Task/config domain model and CLI skeleton — DONE
Depends on R00. Typed task/global configuration, YAML/JSON loading/validation, provider/runner/resource shapes, workspace policy, validation-step model, and CLI shells for `task create`, `run`, `status`, `events`, `resume`, `cancel`, `doctor`. Execution commands fail closed until later units. Tests cover valid/invalid parsing and routing.

## R02 — SQLite schema, transactions, and append-only event store — DONE
Depends on R01. Persisted tasks/current workflow state plus durable tables for attempts, generations, provider waits/failures, process metadata, artifacts, reviews, validations, resources/leases and semantic events. Versioned migration is atomic, task+event updates are transactional, event rows are protected by append-only DB triggers, and rollback/reopen/newer-schema behavior is tested.

## R03 — Pure workflow state machine and invariants — DONE
Depends on R02. Deterministic persisted lifecycle states `READY`, `WORK`, `VALIDATE`, `REVIEW`, `REWORK`, `WAITING_PROVIDER`, `BLOCKED`, `FAILED`, `CANCELLED`, `DONE`; illegal transitions fail closed. `REVIEW`/`DONE`/`REWORK` enforce exact frozen candidate generation/SHA provenance, mandatory validation when configured, valid structured review output, and verdict policy. State writes use compare-and-set expectations inside the SQLite write transaction so stale supervisors cannot overwrite newer task/candidate state.

## R04 — Attempt/artifact layout and immutable attempt records — DONE
Depends on R02. SQLite allocates monotonically increasing per-kind attempt numbers under `BEGIN IMMEDIATE`; each attempt gets a non-reused `tasks/<task-id>/attempts/<kind>-NNN/` directory with immutable metadata/input/command/config snapshots, stdout/stderr paths and write-once result. Candidate generation/SHA is captured in metadata where applicable. Recursive credential redaction covers sensitive keys, CLI secret options, inline secrets and Bearer credentials. Attempt identity/history and artifact rows are protected from mutation/deletion by DB triggers; finalized results cannot be overwritten.

## R05 — Managed Git workspace and candidate-generation primitives — DONE
Depends on R01,R04. Git operations use argv subprocesses without destructive reset/clean. Writer worktrees are dedicated branches rooted at a resolved frozen baseline; dirty/untracked files in the user's source checkout are preserved. Candidate detection requires a clean committed descendant of the frozen baseline. Candidate generation + current task pointer + `candidate_commit_detected` event are recorded atomically and idempotently. Every reviewer generation gets a distinct detached worktree checked out at the exact candidate SHA; existing managed paths fail closed instead of being overwritten. Tests use real temporary Git repositories/worktrees and exercise two candidate generations.

## R06 — Subprocess supervisor foundation — DONE
Depends on R04. Managed processes use argv-only `asyncio.create_subprocess_exec`, dedicated sessions/process groups, durable PID/PGID/timestamps/exit state and redacted command capture. Stdout/stderr are real files. Optional per-stage timeout sends SIGTERM then SIGKILL after grace; explicit termination targets the whole process group. Sparse heartbeat updates liveness metadata without semantic-event spam, no short global timeout is imposed, and launch-recording failure kills the newly started process group. Integration tests use real processes, including descendants and SIGTERM-ignore behavior.

## R07 — Simulated writer provider — DONE
Depends on R05,R06. A declarative real-process fake writer supports sleep, file modification, real Git commit, success with/without commit, declared failure, hang, crash, malformed result, provider unavailability/rate limit and partial work before crash. Provider normalization distinguishes malformed output, process failure and provider unavailability. Worker atomically writes an independent `provider-result.json`, so commit/result checkpoints can complete without a provider callback. Integration tests use real temporary Git/worktrees and include standalone worker completion outside the provider adapter.

## R08 — Structured review contract and simulated reviewer — DONE
Depends on R05,R06. Structured review parsing validates verdicts and severities fail-closed; `REQUEST_CHANGES`/missing-evidence verdicts require findings and multiple valid review objects are rejected as ambiguous. Each simulated review is a fresh real subprocess with a unique invocation ID and immutable attempt, bound before launch to the exact detached candidate worktree SHA/generation. Approve/request-changes, malformed output, provider unavailability, crash/failure and hang/timeout are covered. Malformed output never produces an approval.

## R09 — Generic external-tool model and simulated expensive validator — DONE
Depends on R06. A real supervised fake runtime writes incremental `runner-status.json`, `cycles.csv` and `summary.md` with deterministic metrics. The adapter requires matching run ID, completed N/N status, exactly N ordered successful cycle records and a summary before declaring success; exit zero with incomplete evidence fails closed. Cycle failure/crash, hang, nonzero exit, incremental visibility, orphan child and SIGTERM-ignore/SIGKILL cleanup are covered with real subprocesses.

## R10 — First end-to-end happy path — DONE
Depends on R03,R07,R08,R09. Deterministic orchestration now composes a real simulated writer process, frozen candidate generation, real simulated validator evidence, an independent reviewer process/worktree and the workflow state machine through `DONE`. Validation/review outcomes are persisted in SQLite with exact generation/SHA before state transitions consume them; semantic events expose the full happy-path history.

## R11 — REQUEST_CHANGES/rework generations — DONE
Depends on R10. A valid generation-1 `REQUEST_CHANGES` is persisted immutably, its structured findings are copied into the immutable inputs of a fresh rework writer attempt, and the writer makes a distinct generation-2 commit. Generation 2 is independently validated and reviewed from a new detached reviewer worktree/invocation before approval. Historical generation-1 validation/review rows remain queryable and DB-level append-only guards reject UPDATE/DELETE.

## R12 — Provider-unavailable wait/retry — DONE
Depends on R10. Provider unavailability is committed atomically as `WAITING_PROVIDER` plus durable wait metadata and semantic `provider_unavailable`/`retry_scheduled` events. Exponential retry is bounded, not polled by busy-spin, survives SQLite reopen, resumes the exact interrupted active stage only when due, preserves attempt count across repeated failures, and clears current wait metadata only after provider success while preserving event history.

## R13 — Generic persisted resource leases — DONE
Depends on R02,R09. Named resources have durable positive capacity and leases are acquired under `BEGIN IMMEDIATE`, making capacity checks atomic. Same-holder acquire is idempotent while active, release is ownership-checked, heartbeat does not emit event spam, and capacity-1/capacity-N behavior is tested. Independent resources do not serialize each other. Active leases survive database reopen; stale recovery only reclaims attempt-bound leases with stale heartbeat and no durable RUNNING process, tested against a real subprocess. Acquire/release emit semantic resource events.

## R14 — Writer restart recovery — DONE
Depends on R07,R10. A separately running real fake writer survives closure of the first orchestrator Store, commits and writes its provider result independently, and is reconciled by a new Store from process/result/Git evidence into the original attempt and candidate generation. Live ownership prevents duplicate launch, missing-result/dead-process ambiguity fails closed, and repeated reconciliation is idempotent with exactly one writer attempt and one candidate event.

## R15 — External-validation restart recovery — DONE
Depends on R09,R13. A separately running real fake validator can survive Store closure while retaining a persisted resource lease. Reconciliation requires complete run-ID/N-of-N machine evidence before finalizing the original attempt, recording validation and releasing the lease. Live ownership prevents duplicate launch. Dead process with incomplete evidence returns `AMBIGUOUS`, creates no validation record and intentionally retains the lease so an expensive runtime cannot overlap under uncertain ownership.

## R16 — Cancellation, timeout, stall watchdog, cleanup — DONE
Depends on R06,R09. Managed processes distinguish `TIMED_OUT`, `STALLED`, `FAILED` and `CANCELLED` while preserving whole-process-group SIGTERM/SIGKILL cleanup. Stall detection watches low-cost evidence-file progress rather than liveness heartbeats, preserves partial evidence, emits no polling events, and allows long runs that continue to make progress. Stage timeout is an absolute deadline from process launch and remains authoritative while the progress watchdog is active. A stale token must now survive a confirmation poll before destructive stall cleanup, preventing event-loop scheduling around the exact threshold from creating false stalls.

## R17 — Review/error guardrails and correction limit — DONE
Depends on R11,R12,R16. Invalid reviewer output and exit-zero incomplete validation evidence fail closed into durable `BLOCKED` states with explicit semantic events. A generic review guardrail consumes persisted exact-candidate review/validation evidence, permits only configured rework rounds, emits `correction_limit_reached` when the next request exceeds `max_correction_rounds`, and never creates another generation after blocking. A real multi-round orchestration path proves two `REQUEST_CHANGES` generations followed by fresh approval while preserving distinct attempts/run IDs, feedback inputs, validations and immutable review history.

## R18 — Complete deterministic integration suite — DONE
Depends on R14,R15,R17. `tests/test_required_integration_scenarios.py` exposes the twelve product-contract scenarios as twelve named executable acceptance tests. Existing real Git/SQLite/subprocess recovery and guardrail scenarios are deliberately re-executed; provider-unavailable retry uses an actual simulated writer process before and after `WAITING_PROVIDER`, and capacity-1 contention proves two actual fake runtime processes cannot overlap. `docs/integration-scenarios.md` maps every required scenario to its acceptance test.

## R19 — Chaos/stress and invariant sweeps — DONE
Depends on R18. A reproducible chaos runner creates a fresh tiny real Git repository and SQLite state for every workflow, executes real simulated subprocesses, and injects happy/rework, writer crash, provider wait/retry, validation failure/incomplete evidence, malformed review, delayed result, writer restart/recovery, and tool crash modes. The run guarantees every injection mode occurs, records seed/progress/failure in `chaos-report.json`, and checks provenance, DONE gates, writer ownership, candidate-event consistency and resource-capacity invariants after every workflow. Required demonstration ran 100 workflows with seed `20260914` successfully.

## R20 — Operator CLI and doctor — DONE
Depends on R18. Durable `task create/status/events/resume/cancel/doctor` operate on the SQLite state directory and task snapshots. `status` reports candidate/process/event/review/retry/lease state; `cancel` terminates managed process groups before the durable transition; `resume` refuses unsafe duplicate relaunch. `doctor` checks local prerequisites/auth usability where configured without leaking secrets. `run --simulation` executes the real simulated writer/validator/reviewer pipeline through `DONE`; ordinary `run` remains fail-closed until configured real adapters are installed rather than inventing success.

## R21 — Real Codex writer adapter — DONE
Depends on R18,R20. The authenticated local Codex CLI is isolated behind a writer adapter using current non-interactive `codex exec` JSONL semantics, configurable model/reasoning/sandbox/approval/network settings and prompt delivery over stdin rather than persisted argv. It captures durable command/process metadata, thread/session ID, final agent-message handoff, token usage, stdout/stderr and candidate SHA; rate-limit/unavailability is distinct from process/malformed failures. Success requires a clean committed descendant of the frozen baseline. CLI-compatible fake tests cover argv/stdin shape, success, rate limit, malformed output, timeout and exit-zero/no-commit failure.

## R22 — Real Claude reviewer adapter/package — DONE
Depends on R18,R20. A fresh Claude Code process reviews a dedicated detached worktree at the exact candidate SHA using non-interactive JSON output, a strict JSON schema, `plan` permission mode, read-only tool allowlist, no session persistence and bare startup. The bounded review package contains task/acceptance criteria, frozen baseline/candidate, changed paths plus bounded diff, deterministic evidence/commands and explicitly labeled untrusted writer claims; it exposes no writer private reasoning/history. The adapter captures session/usage/output, validates the structured review again in core, classifies provider unavailability, and rejects any review that changes HEAD or worktree status. CLI-compatible fake tests cover approve, request-changes, malformed/unavailable output, package bounds, wrong SHA/write-tool rejection and malicious reviewer writes.

## R23 — Real shadPS4/Bloodborne tool adapter — DONE
Depends on R18,R20. External-config adapter for the existing harness normalizes run ID, requested/completed cycles, process exit, runner state, cycle data and summary/evidence without copying the harness FSM into core. Dedicated real-subprocess acceptance covers CSV/JSON success, exact provenance/artifacts, incomplete or mismatched evidence, explicit runner/cycle failure, sequence validation, crash/nonzero, timeout/stall cleanup and pre-attempt argv-template validation.

## R24 — Minimal SSH external-tool runner — DONE
Depends on R23. Local/remote execution remains separable through a minimal SSH transport over the existing supervised-process boundary. Remote argv/cwd/environment values are POSIX-quoted into a `sh -s` script sent over SSH stdin; task-controlled command values never pass through a local shell. Dedicated acceptance executes the generated script with hostile shell-looking arguments, verifies stdout/stderr/nonzero/timeout semantics and validates destination/path inputs. No distributed scheduler, implicit artifact transfer, or remote state machine is introduced.

## R25 — Documentation/example configuration — IN PROGRESS
Depends on R20,R21,R22,R23. Final architecture/state-machine/providers/simulation/recovery/shadPS4 docs plus credential-free examples and README five-minute simulations. Current WIP replaces the stale scaffold README, adds all required docs, corrects the simulated CLI example to the real `simulated_validator` contract, adds real-provider/SSH/Bloodborne examples, and introduces a dedicated documentation acceptance test. R25 remains incomplete until that gate and the full regression suite pass.

## R26 — Final correctness/security review and required demonstration — FUTURE
Depends on R19,R21,R22,R23,R25. Adversarial review for races, atomicity, duplicate launches, orphan groups, stale provenance, lease leaks, history loss, unsafe Git/shell and unbounded state/logs; fix findings and actually run/report full suite plus all required demonstrations, including >=100 chaos workflows and exact real-provider/runtime connection steps.

## Non-goals

No Airflow replacement, Kubernetes infrastructure, distributed queue/cluster scheduler, issue-tracker workflow, arbitrary DAG language, web UI, multi-tenant SaaS, LLM-owned orchestration, or Bloodborne-specific core transitions.
