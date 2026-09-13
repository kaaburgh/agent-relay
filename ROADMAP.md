# Roadmap

This roadmap decomposes the product contract in `docs/spec.md` into bounded, durable work units. It is deliberately not an issue tracker. Coding agents should normally execute exactly one `READY` item per pass and leave a tested Git checkpoint.

Status legend: `DONE`, `READY`, `BLOCKED`, `FUTURE`.

Selection rule: take the first `READY` item whose dependencies are `DONE`, unless the user explicitly selects another item. Do not begin the next item in the same pass.

## R00 — Agent-friendly repository scaffold — DONE

Goal: establish Python 3.12 package shell, CI/test shell, agent instructions, durable specification, roadmap, and implementation handoff.

Acceptance: repository contains `AGENTS.md`, `ROADMAP.md`, `docs/spec.md`, `docs/implementation-status.md`, package metadata/shell, a smoke test, and CI. No orchestrator behavior is claimed yet.

## R01 — Task/config domain model and CLI skeleton — READY

Depends on: R00.

Goal: define typed internal representations for task specification and global configuration without implementing execution. Add CLI command shells for `task create`, `run`, `status`, `events`, `resume`, `cancel`, and `doctor` with clear not-yet-implemented behavior where appropriate.

Acceptance: valid sample task/config files can be loaded and validated; invalid required fields fail clearly; no provider credentials are stored; tests cover parsing and CLI routing. Preserve room for local/SSH tool runners and named resource capacities.

## R02 — SQLite schema, transactions, and append-only event store — FUTURE

Depends on: R01.

Goal: implement durable storage for tasks, current workflow state, attempts, candidate generations, provider waits/failures, process metadata, artifacts, review records, validation records, leases, timestamps, and semantic events.

Acceptance: schema is created/migrated deterministically; transitions plus event append can be committed atomically; event history is append-only through public APIs; unit tests cover rollback and reopen/reload.

## R03 — Pure workflow state machine and invariants — FUTURE

Depends on: R02.

Goal: encode explicit generic states (`READY`, `WORK`, `VALIDATE`, `REVIEW`, `REWORK`, `WAITING_PROVIDER`, `BLOCKED`, `FAILED`, `CANCELLED`, `DONE`) and only the minimal useful internal substates.

Acceptance: transitions are deterministic and persisted through R02; illegal transitions fail; core invariants from `docs/spec.md` are executable assertions/tests, including same-generation candidate/validation/review provenance and no malformed-output approval.

## R04 — Attempt/artifact layout and immutable attempt records — FUTURE

Depends on: R02.

Goal: create understandable per-task/per-attempt directories with immutable inputs, normalized command/config snapshots, stdout/stderr paths, result metadata, timestamps, and candidate SHA where applicable.

Acceptance: retries allocate new attempt IDs and never overwrite prior attempt artifacts; secrets are redacted/not copied; tests prove historical attempts remain intact.

## R05 — Managed Git workspace and candidate-generation primitives — FUTURE

Depends on: R01, R04.

Goal: implement safe managed writer/reviewer worktrees, baseline resolution, candidate SHA detection/freezing, and candidate generation creation.

Acceptance: works against tiny real temporary Git repos; never hard-resets/cleans unknown user worktrees; reviewer worktree can be checked out at exact candidate SHA; a rework commit creates a new generation rather than mutating old provenance.

## R06 — Subprocess supervisor foundation — FUTURE

Depends on: R04.

Goal: implement local argv-based process launch/supervision with PID/process-group metadata, stdout/stderr capture, start/end timestamps, exit status, SIGTERM/SIGKILL cleanup, stage timeout hooks, and sparse liveness tracking.

Acceptance: integration tests use real child processes; complete process groups are cleaned up; no `shell=True`; normal long-running operations are not killed by arbitrary short global timeouts.

## R07 — Simulated writer provider — FUTURE

Depends on: R05, R06.

Goal: implement declaratively scripted fake writer behaviors: sleep, file modification, commit, no commit, success/failure, hang, crash, malformed result, provider rate limit/unavailability, partial work then crash, and completion while supervisor/orchestrator is absent.

Acceptance: uses tiny real temporary Git repositories/worktrees where Git semantics matter; normalized provider result distinguishes provider unavailable/process failure/malformed output/success; deterministic tests cover every behavior class.

## R08 — Structured review contract, parser, and simulated reviewer — FUTURE

Depends on: R05, R06.

Goal: define robust review schema and fake reviewer with per-attempt scripted responses for `APPROVE`, `APPROVE_WITH_FOLLOWUPS`, `REQUEST_CHANGES`, `BLOCKED_BY_MISSING_EVIDENCE`, malformed output, failure, hang, and provider unavailable.

Acceptance: malformed output can never become approval; severity enum is enforced; fresh invocation IDs exist per review attempt; parser tests include noisy/malformed process output.

## R09 — Generic external-tool result model and simulated expensive validator — FUTURE

Depends on: R06.

Goal: implement external-tool abstraction plus a Bloodborne-shaped fake runner that incrementally writes `runner-status.json`, `cycles.csv`, `summary.md`, configurable metrics, cycle success/failure/hang/crash, incomplete evidence, orphan child behavior, SIGTERM response/ignore behavior, and nonzero exits.

Acceptance: process supervision is real; success requires normalized evidence rather than exit code alone; requested-vs-completed cycles are validated; artifacts survive failures.

## R10 — First end-to-end happy-path orchestration — FUTURE

Depends on: R03, R07, R08, R09.

Goal: wire `writer -> candidate -> validate -> review -> DONE` using simulated components.

Acceptance: scenario 1 passes as an integration test; `DONE` is impossible without successful validation and valid review for the exact same candidate generation; status/events expose semantic transitions without polling spam.

## R11 — REQUEST_CHANGES, rework, and candidate generations — FUTURE

Depends on: R10.

Goal: implement reviewer findings flowing into a new writer/rework attempt, then new candidate generation, validation, and fresh review.

Acceptance: scenario 2 passes; review attempt IDs differ; validation/review for generation 2 cannot reuse generation 1 evidence; historical findings/reviews remain immutable.

## R12 — Provider-unavailable durable wait/retry — FUTURE

Depends on: R10.

Goal: implement `WAITING_PROVIDER` with provider, reason, first-seen, last-attempt, next-retry, attempt-count and bounded/exponential retry policy owned by adapters/policy rather than ad-hoc loops.

Acceptance: scenario 5 passes; no busy-spin; provider recovery resumes the same workflow without losing candidate/evidence state.

## R13 — Generic persisted resource leases — FUTURE

Depends on: R02, R09.

Goal: implement capacity-based named leases/semaphores suitable for one GPU/runtime/display/profile without encoding Bloodborne in core.

Acceptance: two concurrent simulated tasks contending for a capacity-1 `bloodborne-runtime` cannot overlap validator ownership; unrelated stages may proceed; lease state is persisted or safely reconstructable.

## R14 — Writer restart recovery and checkpoint reconciliation — FUTURE

Depends on: R07, R10.

Goal: recover safely when orchestrator disappears during writer work or after writer success but before final callback persistence.

Acceptance: scenarios 3 and 12 pass using a real separately running fake worker and actual orchestrator process restart/termination; recovery inspects candidate commit/result artifacts/process existence and does not duplicate a completed successful writer stage.

## R15 — External-validation restart recovery and lease reconciliation — FUTURE

Depends on: R09, R13.

Goal: define and implement ownership/recovery behavior when orchestrator dies while an expensive external validator continues or terminates.

Acceptance: scenario 4 passes; restart cannot accidentally launch a second expensive validator; durable artifacts/process evidence are reconciled; unrecoverable ambiguity is documented and blocks safely rather than duplicating work.

## R16 — Cancellation, stage timeout, stall watchdog, and process cleanup — FUTURE

Depends on: R06, R09.

Goal: distinguish timeout, apparent stall, process crash, provider unavailable, and legitimate long-running work; add low-cost watchdog and cancellation semantics.

Acceptance: scenario 8 passes including SIGTERM then forced SIGKILL when configured fake tool ignores termination; evidence remains; no high-frequency semantic event spam.

## R17 — Review/error guardrails and correction limit — FUTURE

Depends on: R11, R12, R16.

Goal: finish malformed-review policy, incomplete-evidence policy, repeated correction history, and maximum correction rounds.

Acceptance: scenarios 6, 7, 10, and 11 pass; malformed review never approves; exit-zero/incomplete validation never passes; two or more correction rounds preserve all findings; correction limit ends in `BLOCKED` rather than infinite loop.

## R18 — Complete deterministic integration scenario suite — FUTURE

Depends on: R14, R15, R17.

Goal: consolidate and run all 12 required scenarios from `docs/spec.md` as real integration tests with temporary Git/SQLite/subprocesses.

Acceptance: every required scenario has a named test/demo command; failures print enough durable paths/IDs to debug; suite remains reasonably fast.

## R19 — Chaos/stress runner and invariant sweeps — FUTURE

Depends on: R18.

Goal: randomized but reproducible short simulated workflows with process crash, orchestrator restart, temporary provider failure, review changes, tool failure, and delayed result/evidence publication.

Acceptance: at least 100 workflows per required demo run; seed is printed/recorded; invariants are checked after every run; no duplicate committed successful stage, lost candidate, stale approval, simultaneous writer, or over-capacity lease.

## R20 — Operator CLI behavior and `doctor` — FUTURE

Depends on: R18.

Goal: complete usable `task create`, `run`, `status`, `events`, `resume`, `cancel`, and `doctor` commands.

Acceptance: `status` quickly reports task/stage/candidate/active process/time/last event/review/next retry/leases; `doctor` checks Python, git, configured executables, repo/runtime paths, SQLite writability, and detectable auth usability without exposing secrets.

## R21 — Real Codex writer adapter — FUTURE

Depends on: R18, R20.

Goal: implement isolated non-interactive adapter for an already-authenticated Codex CLI/session, with configurable provider/model/reasoning effort and provider-specific unavailability detection.

Acceptance: adapter captures command, PID, timestamps, exit, stdout/stderr, session/run ID/final handoff/usage when exposed; exact CLI syntax is confined to adapter; no credentials in repository/config examples. Real invocation may be environment-gated, but adapter unit/integration behavior must be tested with a CLI-compatible fake.

## R22 — Real Claude reviewer adapter and bounded review package — FUTURE

Depends on: R18, R20.

Goal: implement already-authenticated Claude CLI adapter and exact-candidate read-only review package containing task spec, criteria, baseline/candidate SHA, diff, relevant paths, deterministic validation evidence, commands, and explicitly labeled writer claims.

Acceptance: no private writer reasoning/history is passed; fresh process per review; reviewer cannot mutate writer worktree by policy and is isolated in dedicated worktree; structured parser from R08 is authoritative.

## R23 — Real shadPS4/Bloodborne tool adapter — FUTURE

Depends on: R18, R20.

Goal: add adapter/configuration for the existing external runner without embedding its menu/death FSM. Paths must be external configuration, including the currently known runner shape around `tools/run_bb_death_reload_benchmark.py`.

Acceptance: normalizes run ID, requested/completed cycles, runner exit, `runner-status.json`, cycles data, summary/evidence paths; missing local paths do not break simulation; evidence completeness is machine-decided.

## R24 — Minimal SSH external-tool runner — FUTURE

Depends on: R23.

Goal: cleanly separate local vs remote execution and, if still simple/stable, implement minimal SSH argv execution suitable for a permanent orchestrator node invoking a GPU node.

Acceptance: no distributed scheduler is introduced; quoting/argument handling is safe and tested; remote process/evidence ownership/recovery limitations are explicit. If SSH implementation proves destabilizing, preserve a clean runner interface and document the deferred bounded follow-up instead of contaminating core orchestration.

## R25 — Documentation set and example configuration — FUTURE

Depends on: R20, R21, R22, R23.

Goal: write final `docs/architecture.md`, `docs/state-machine.md`, `docs/providers.md`, `docs/simulation.md`, `docs/recovery.md`, and `docs/shadps4-example.md`, plus credential-free examples for Codex Luna writer, Claude Opus reviewer, simulations, real/remote shadPS4, and resource capacity 1.

Acceptance: README has a five-minute simulation quick start and commands for happy path, REQUEST_CHANGES, and restart recovery with no real providers/GPU/credentials.

## R26 — Final correctness/security review and required demonstration — FUTURE

Depends on: R19, R21, R22, R23, R25.

Goal: adversarially inspect race conditions, transaction boundaries, duplicate launches, orphan processes, candidate/evidence association, lease leaks, stale approval/validation, history loss, unsafe Git/shell behavior, and unbounded state/log growth; fix meaningful findings.

Acceptance: actually run and record pass/fail + commands for the full automated suite and all 10 required demonstrations: happy path; correction then approval; restart during writer; restart after writer completed while supervisor absent; provider unavailable then retry; two-task capacity-1 runtime contention; malformed reviewer; repeated changes to correction limit; restart/recovery coverage; and chaos/stress with at least 100 workflows and recorded seed(s). `docs/implementation-status.md` must summarize repository structure, architecture/state/storage/provider interfaces, real-adapter status, simulation capabilities, leases, recovery, demo results, known limitations, and exact connection steps for authenticated Codex/Luna, Claude/Opus, and remote shadPS4/Bloodborne.

## Non-goals for this roadmap

- generic Airflow replacement;
- Kubernetes infrastructure;
- distributed queue/cluster scheduler;
- issue-tracker-driven workflow;
- arbitrary DAG language;
- web UI or multi-tenant SaaS;
- LLM-powered orchestration/state management;
- Bloodborne-specific state transitions in orchestration core.
