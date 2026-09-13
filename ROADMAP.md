# Roadmap

This roadmap decomposes `docs/spec.md` into durable implementation units. It is not an issue tracker. Each unit is a checkpoint/transaction; unless a single-unit pass is explicitly requested, a successful checkpoint is followed automatically by the next `READY` unit.

Status legend: `DONE`, `READY`, `BLOCKED`, `FUTURE`.

Selection rule: take the first `READY` item whose dependencies are `DONE`. Every unit remains subject to the complete product/acceptance contract in `docs/spec.md`; this file must not be used to weaken it.

## R00 — Agent-friendly repository scaffold — DONE
Python 3.12 package shell, CI/test shell, durable spec, roadmap, handoff and agent instructions.

## R01 — Task/config domain model and CLI skeleton — DONE
Depends on R00. Typed task/global configuration, YAML/JSON loading/validation, provider/runner/resource shapes, workspace policy, validation-step model, and CLI shells for `task create`, `run`, `status`, `events`, `resume`, `cancel`, `doctor`. Execution commands fail closed until later units. Tests cover valid/invalid parsing and routing.

## R02 — SQLite schema, transactions, and append-only event store — DONE
Depends on R01. Persisted tasks/current workflow state plus durable tables for attempts, generations, provider waits/failures, process metadata, artifacts, reviews, validations, resources/leases and semantic events. Versioned migration is atomic, task+event updates are transactional, event rows are protected by append-only DB triggers, and rollback/reopen/newer-schema behavior is tested.

## R03 — Pure workflow state machine and invariants — DONE
Depends on R02. Deterministic persisted lifecycle states `READY`, `WORK`, `VALIDATE`, `REVIEW`, `REWORK`, `WAITING_PROVIDER`, `BLOCKED`, `FAILED`, `CANCELLED`, `DONE`; illegal transitions fail closed. `REVIEW`/`DONE`/`REWORK` enforce exact frozen candidate generation/SHA provenance, mandatory validation when configured, valid structured review output, and verdict policy. State writes use compare-and-set expectations inside the SQLite write transaction so stale supervisors cannot overwrite newer task/candidate state.

## R04 — Attempt/artifact layout and immutable attempt records — READY
Depends on R02. Per-task/per-attempt durable directories with immutable inputs, command/config snapshots, stdout/stderr, result metadata, timestamps and candidate SHA. Retries allocate new IDs and preserve history/secrets safety.

## R05 — Managed Git workspace and candidate-generation primitives — FUTURE
Depends on R01,R04. Safe dedicated writer/reviewer worktrees, baseline resolution, frozen candidate SHA and generation creation using real temporary Git repos. Never reset/clean unknown user worktrees.

## R06 — Subprocess supervisor foundation — FUTURE
Depends on R04. Argv-only process launch/supervision, process groups, capture, timestamps, exit status, timeout hooks, sparse liveness and SIGTERM/SIGKILL cleanup tested with real subprocesses.

## R07 — Simulated writer provider — FUTURE
Depends on R05,R06. Declarative fake writer: sleep, modify/commit/no-commit, success/failure/hang/crash/malformed/provider-unavailable/partial-work/completion while supervisor absent. Use real temporary Git semantics where relevant.

## R08 — Structured review contract and simulated reviewer — FUTURE
Depends on R05,R06. Enforced verdict/severity schema, staged responses, fresh invocation IDs, malformed/failure/hang/unavailable behaviors. Malformed output can never approve.

## R09 — Generic external-tool model and simulated expensive validator — FUTURE
Depends on R06. Real supervised fake runtime with incremental `runner-status.json`, `cycles.csv`, `summary.md`, metrics, cycle failure/hang/crash/incomplete evidence/orphan child/SIGTERM/SIGKILL behaviors. Exit zero alone never proves success.

## R10 — First end-to-end happy path — FUTURE
Depends on R03,R07,R08,R09. Simulated writer -> frozen candidate -> validation -> independent review -> DONE with exact-generation provenance and semantic status/events.

## R11 — REQUEST_CHANGES/rework generations — FUTURE
Depends on R10. Findings flow to fresh rework; new commit means new generation; validation/re-review use the new generation; historical reviews remain immutable.

## R12 — Provider-unavailable wait/retry — FUTURE
Depends on R10. Durable `WAITING_PROVIDER` metadata and bounded/exponential retry without busy-spin; resume same workflow/candidate state.

## R13 — Generic persisted resource leases — FUTURE
Depends on R02,R09. Capacity-based named leases; two capacity-1 runtime users cannot overlap; unrelated stages may proceed; recovery is safe.

## R14 — Writer restart recovery — FUTURE
Depends on R07,R10. Real separately running fake worker plus orchestrator termination/restart; reconcile commit/result/process evidence and never duplicate completed writer work.

## R15 — External-validation restart recovery — FUTURE
Depends on R09,R13. Recover/reconcile an expensive validator without duplicate launch; ambiguous ownership blocks safely.

## R16 — Cancellation, timeout, stall watchdog, cleanup — FUTURE
Depends on R06,R09. Distinguish timeout/stall/crash/provider-unavailable/legitimate long run; preserve evidence; SIGTERM then forced SIGKILL; no event spam.

## R17 — Review/error guardrails and correction limit — FUTURE
Depends on R11,R12,R16. Required scenarios for malformed review, incomplete validation evidence, repeated correction history and max-round BLOCKED behavior.

## R18 — Complete deterministic integration suite — FUTURE
Depends on R14,R15,R17. All 12 scenarios in `docs/spec.md` run as real integration tests using temporary Git/SQLite/subprocesses and remain reasonably fast.

## R19 — Chaos/stress and invariant sweeps — FUTURE
Depends on R18. Reproducible randomized failures/restarts/provider waits/reviews/tool failures/delayed results. Required demo executes >=100 workflows with recorded seed and invariants after every run.

## R20 — Operator CLI and doctor — FUTURE
Depends on R18. Complete durable `task create/run/status/events/resume/cancel/doctor`; status exposes stage/candidate/process/time/event/review/retry/leases; doctor checks local prerequisites/auth usability without leaking secrets.

## R21 — Real Codex writer adapter — FUTURE
Depends on R18,R20. Isolated authenticated Codex CLI adapter with configurable model/reasoning effort; capture command/PID/timestamps/exit/output/session/handoff/usage where exposed and classify provider unavailability. Test via CLI-compatible fake when real auth is unavailable.

## R22 — Real Claude reviewer adapter/package — FUTURE
Depends on R18,R20. Fresh authenticated Claude CLI process against exact-candidate dedicated worktree; bounded package contains task/criteria/baseline/candidate/diff/paths/deterministic evidence/commands/labeled writer claims, never private writer reasoning.

## R23 — Real shadPS4/Bloodborne tool adapter — FUTURE
Depends on R18,R20. External-config adapter for existing harness; normalize run ID, requested/completed cycles, exit, runner status, cycle data and summary/evidence without copying harness FSM into core.

## R24 — Minimal SSH external-tool runner — FUTURE
Depends on R23. Keep local/remote execution separable; implement minimal safe SSH argv runner only if it does not destabilize core. No distributed scheduler.

## R25 — Documentation/example configuration — FUTURE
Depends on R20,R21,R22,R23. Final architecture/state-machine/providers/simulation/recovery/shadPS4 docs plus credential-free examples and README five-minute simulations.

## R26 — Final correctness/security review and required demonstration — FUTURE
Depends on R19,R21,R22,R23,R25. Adversarial review for races, atomicity, duplicate launches, orphan groups, stale provenance, lease leaks, history loss, unsafe Git/shell and unbounded state/logs; fix findings and actually run/report full suite plus all required demonstrations, including >=100 chaos workflows and exact real-provider/runtime connection steps.

## Non-goals

No Airflow replacement, Kubernetes infrastructure, distributed queue/cluster scheduler, issue-tracker workflow, arbitrary DAG language, web UI, multi-tenant SaaS, LLM-owned orchestration, or Bloodborne-specific core transitions.
