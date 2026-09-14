# R26 Final Correctness and Security Review

## Conclusion

The bounded R26 production-code adversarial audit is complete. The reviewed core fails closed on malformed provider output, stale candidate provenance, incomplete validation evidence, ambiguous expensive-runtime ownership, cancellation races, and restart ambiguity. Material findings discovered during R26 were converted into executable regressions and closed with red/green evidence before the audit was frozen.

This document is the final handoff for the implemented scope. It does **not** claim that the ordinary top-level `agent-relay run` command provides an integrated real-provider pipeline. `run --simulation` is the complete operator-ready end-to-end backend. Real Codex, Claude, shadPS4 and SSH adapters are individually implemented and tested, while ordinary top-level `agent-relay run` remains intentionally fail-closed until a future composition unit explicitly wires those adapters together.

## Repository and architecture snapshot

The implementation is intentionally small and deterministic:

- `agent_relay/models.py` and `config.py`: typed task/provider/runner/resource configuration;
- `store.py`: SQLite schema, transactions, durable task state and append-only semantic events;
- `workflow.py` and `guardrails.py`: legal transitions, exact candidate/evidence gates and bounded correction policy;
- `artifacts.py`: immutable attempt snapshots/results, bounded logs and credential-shaped redaction;
- `git_workspace.py`: isolated writer worktrees, detached reviewer worktrees and immutable candidate generations;
- `supervisor.py` and `watchdog.py`: argv-only local processes, PID/PGID persistence, timeout/stall/cancel and process-group cleanup;
- `provider_retry.py`: durable `WAITING_PROVIDER` retry/backoff;
- `resource_leases.py`: persisted named-capacity leases such as `bloodborne-runtime`;
- `writer_recovery.py` and `validator_recovery.py`: restart reconciliation that reuses durable work rather than blindly relaunching it;
- `codex_writer.py` and `claude_reviewer.py`: real CLI provider adapters;
- `shadps4_validator.py`: external Bloodborne/shadPS4 machine-evidence adapter;
- `ssh_runner.py`: minimal SSH external-tool transport;
- `operator.py`, `execution.py`, and `cli.py`: operator surfaces;
- `chaos.py` and the integration tests: deterministic recovery/failure demonstrations.

The orchestration engine owns workflow state. Codex, Claude and external tools are bounded workers and cannot advance the state machine themselves. Bloodborne gameplay/death/reload state stays in the external harness.

## State machine and durable schema

Normal lifecycle:

```text
READY -> WORK -> VALIDATE -> REVIEW -> DONE
                         REVIEW --REQUEST_CHANGES--> REWORK -> VALIDATE -> REVIEW
```

Additional durable states are `WAITING_PROVIDER`, `BLOCKED`, `FAILED`, and `CANCELLED`.

SQLite persists tasks, attempts, candidate generations, process metadata, artifacts, validations, reviews, provider waits, resources/leases, and append-only semantic events. State/event transitions use transactions and stale-state checks. Candidate validation and review provenance is bound to an exact `(generation, candidate SHA)`. A stale validation or review cannot authorize a newer candidate.

Completed attempt/evidence history is not reused as mutable scratch state. Retries get new attempt identities. Semantic events are append-only, while high-frequency heartbeat/liveness updates do not flood event history.

## Provider and tool status

### Codex writer

`CodexWriterProvider` launches authenticated local `codex exec` as argv, sends the writer prompt over stdin rather than argv, parses JSONL, records usage/handoff, distinguishes provider unavailability, and accepts success only when the managed writer worktree contains a clean committed descendant of the frozen baseline SHA.

Status: adapter implemented and regression-tested with CLI-compatible subprocesses. CI does not perform a live authenticated OpenAI/Codex call.

### Claude reviewer

`ClaudeReviewerProvider` creates a fresh independent review invocation against a detached candidate worktree. The reviewer is restricted to read-only tools/plan mode, consumes a bounded review package, and must return the structured review contract. Malformed output never becomes approval and any reviewer-worktree write invalidates the review.

Status: adapter implemented and regression-tested with CLI-compatible subprocesses. CI does not perform a live authenticated Claude/Opus call.

### shadPS4/Bloodborne validator

`ShadPS4BloodborneValidator` launches an externally configured harness and normalizes `run_id`, requested/completed cycles, runner state, cycle evidence and summary. Success requires matching run identity, exact N/N counts, explicit per-cycle success proof, and supported complete machine evidence. `cycles.csv` and `cycles.json` are supported, and durable recovery supports both forms without relaunching a completed expensive run.

Status: adapter implemented and tested with fake external harnesses. CI does not launch Bloodborne or a GPU-backed shadPS4 runtime.

### SSH transport

`SSHExternalToolRunner` launches local `ssh` as argv and sends a POSIX-quoted remote script to constant `sh -s` over stdin. Task-controlled argv/cwd/environment values are not interpolated into a local shell.

Status: minimal transport implemented and tested with a real subprocess-compatible fake SSH endpoint. It is not a distributed scheduler and does not provide implicit `scp`/`rsync` artifact transfer.

## Recovery and resource ownership

Recovery reconciles durable evidence before relaunch. The important boundaries are:

- a writer provider result plus valid committed Git candidate is reused after supervisor absence;
- complete validation evidence is reused against the exact generation/SHA;
- incomplete validation evidence remains ambiguous and keeps the expensive-runtime lease;
- CSV and JSON validation evidence recover consistently;
- provider unavailability enters durable `WAITING_PROVIDER` with bounded retry metadata;
- process cancellation/timeout/stall clean local process groups;
- a capacity-one `bloodborne-runtime` lease prevents overlap;
- stale-lease reclaim revalidates heartbeat/process ownership atomically before release;
- cancellation and provider-result publication are serialized so a late worker cannot overwrite a durable operator cancellation;
- an uncommitted managed `result.json` left by a crash is recoverable only when SQLite proves there is no committed result artifact; durable evidence is never silently deleted.

## R26 adversarial findings and dispositions

| Finding | Red evidence | Green evidence / disposition |
| --- | --- | --- |
| unbounded subprocess output | R26 audit regression | bounded tail capture drains continuously without blocking child |
| external cancellation racing managed wait | R26 audit regression | managed wait reconciles durable cancellation |
| child survives normal parent exit | R26 audit regression | remaining local process group is cleaned |
| leader dies before operator cancellation | Actions `34841585992` | `34845859463`; whole group still terminated/escalated |
| stale writer owns idempotent candidate freeze | R26 audit regression | candidate-generation idempotency also verifies writer ownership |
| stale lease reclaim races heartbeat | `34846111400` | `34846335634`; heartbeat/process ownership rechecked under write transaction |
| cancellation races provider result publication | `34846974177` | `34847495507`; cancellation/result publication serialized while duplicate write-once semantics preserved |
| crash leaves uncommitted `result.json` | `34847845590` | `34848116328`; only proven uncommitted managed residue can be replaced |
| cycle records lack explicit success proof | `34857262874` | `34857598533`; missing status/result/outcome is incomplete evidence |
| successful JSON validation cannot recover | `34858018738` | `34858291629`; CSV/JSON recovery boundary normalized |
| unsafe local shell/destructive Git cleanup | executable source audit | no production `shell=True`, automatic `git reset --hard`, or `git clean` path |

The final bounded read-only pass found no additional material production-code finding after the CSV/JSON recovery repair. Production audit scope was then frozen so closeout cannot expand indefinitely.

## Demonstration map

The full CI command is:

```bash
python -m unittest discover -s tests -v
```

The last production-code gate before this review artifact was GitHub Actions run `34858291629` on `3a79fe1571a1522afffd88a2ac2b9de58a6d32a2`: 167 tests passed in 53.786s. The final closeout gate must rerun the suite after this document and its acceptance test are present.

Required demonstrations are executable tests, not prose-only claims:

1. happy path: `RequiredIntegrationScenarios.test_s01_happy_path_writer_validate_review_done`;
2. `REQUEST_CHANGES` -> rework -> approval: `test_s02_review_correction_fresh_rework_and_review`;
3. orchestrator absent while writer is active/finishes: `test_s03_orchestrator_absent_while_writer_finishes_no_duplicate`;
4. orchestrator absent during expensive validation: `test_s04_orchestrator_absent_during_external_validation_no_duplicate`;
5. provider unavailable -> retry -> success: `test_s05_real_provider_unavailable_wait_retry_then_writer_resumes`;
6. malformed reviewer never approves: `test_s06_malformed_reviewer_never_approves`;
7. incomplete validation never passes: `test_s07_exit_zero_incomplete_validation_never_passes`;
8. hanging tool watchdog/process-group cleanup: `test_s08_hanging_tool_watchdog_cleans_group_and_preserves_evidence`;
9. two tasks contend for one expensive runtime: `test_s09_two_real_fake_runtimes_obey_capacity_one_lease`;
10. two corrections before approval: `test_s10_two_review_rework_rounds_before_approval_keep_history`;
11. repeated `REQUEST_CHANGES` reaches the configured correction limit: `test_s11_correction_limit_blocks_instead_of_looping`;
12. worker completion while supervisor is absent reuses the checkpoint: `test_s12_worker_completes_while_supervisor_absent_checkpoint_is_reused`.

Randomized stress is `ChaosIntegrationTests.test_reproducible_100_workflow_chaos_sweep`: exactly 100 workflows with seed `20260914`. Its modes cover happy path, review changes, writer crash, provider wait, validation failure/incomplete evidence, malformed review, delayed result, writer restart and tool crash.

## Exact connection steps for real components

These are component connection steps. They are **not** a claim that one ordinary `agent-relay run` command composes the real pipeline today.

### Authenticated Codex / Luna writer

1. Install the Codex CLI on the orchestrator host and authenticate it using the CLI's supported local authentication mechanism. Do not place tokens, cookies or API keys in repository YAML/artifacts.
2. Start from `examples/config.real.example.yaml`. Set `writer.provider: codex`, `writer.executable: codex`, the desired available model (for example the account's Luna model identifier), reasoning effort, sandbox, approval policy and optional network policy.
3. Point the task `repository`/`baseline` at the real source repository. Let `GitWorkspaceManager` create the managed writer worktree from the frozen baseline; do not give the adapter an arbitrary dirty user checkout.
4. Run `agent-relay doctor --config <real-config.yaml> --task <task.yaml>` and require the Codex executable/auth/prerequisite checks to pass.
5. An integrator connects `CodexWriterProvider.start(...)`/`finish(...)` to the deterministic WORK/REWORK stage. The provider receives the prompt on stdin; success is accepted only after the adapter proves a clean committed descendant of the frozen baseline.
6. Map `PROVIDER_UNAVAILABLE` to the existing durable `WAITING_PROVIDER` retry path; do not reinterpret it as success or create a duplicate writer attempt while ownership is ambiguous.

### Authenticated Claude / Opus reviewer

1. Install and authenticate the Claude CLI outside repository configuration; keep credentials out of YAML and persisted artifacts.
2. Configure `reviewer.provider: claude`, `reviewer.executable: claude`, the desired Opus model, plan/read-only permission mode and the bounded read-only tool set shown by `examples/config.real.example.yaml` / `docs/providers.md`.
3. Run `agent-relay doctor --config <real-config.yaml> --task <task.yaml>` and require reviewer prerequisites/authentication checks to pass.
4. Build the bounded `ReviewPackage` for the exact validated generation/SHA and create a detached reviewer worktree at that SHA.
5. Connect `ClaudeReviewerProvider.start(...)`/`finish(...)` to REVIEW. Treat writer summaries only as untrusted claims. A malformed structured result, a candidate mismatch, or any reviewer-worktree write must fail closed and can never authorize `DONE`.

### Local shadPS4 / Bloodborne validation

1. Configure a named resource such as `bloodborne-runtime` with capacity `1`.
2. Start from `examples/task.shadps4.example.yaml`; replace repository and harness paths with operator-owned real paths. The external argv template must pass `{run_id}`, `{requested_cycles}`, and `{evidence_dir}` to the existing benchmark harness, for example `run_bb_death_reload_benchmark.py`.
3. The harness, not agent-relay core, owns Bloodborne menu/death/reload/gameplay state. It must emit `runner-status.json`, `summary.md`, and either `cycles.csv` or `cycles.json` under the provided evidence directory.
4. Every successful cycle record must explicitly contain a supported success `status`, `result`, or `outcome`; N/N record presence alone is not success.
5. Acquire the `bloodborne-runtime` lease before launch, run `ShadPS4BloodborneValidator`, bind normalized evidence to the exact generation/SHA, and release the lease only after deterministic completion/recovery. Ambiguous evidence keeps the lease and blocks unsafe overlap.

### Remote shadPS4 / Bloodborne over SSH

1. Configure an SSH runner (`kind: ssh`) with operator-owned host/user/base directory and trusted `ssh_args`; use SSH agent/key configuration outside task YAML.
2. Verify the remote host has the shadPS4 fork, Bloodborne 1.09/runtime inputs, harness dependencies and the configured benchmark harness path.
3. Use `SSHExternalToolRunner` only as transport. It does not own workflow state and does not implicitly transfer evidence.
4. Make the harness evidence directory visible to the local orchestrator through shared storage, or add an explicit transfer step that completes before local evidence reconciliation. Do not assume `scp`/`rsync` exists implicitly.
5. Feed the transferred/shared evidence through the same exact-generation shadPS4 validation/recovery contract and hold `bloodborne-runtime` for the full ownership interval.
6. Local SSH timeout/cancellation terminates the local SSH process group; it does not guarantee termination of arbitrary daemonized remote descendants. The remote harness/host must therefore provide its own bounded lifecycle if it can daemonize work.

## Known limitations

- Ordinary top-level `agent-relay run` remains intentionally fail-closed for real providers; there is no integrated real Codex -> real validation -> real Claude composition in the operator command yet.
- CI validates adapter contracts with deterministic CLI/harness doubles rather than live authenticated Codex/Claude services or a GPU-backed Bloodborne session.
- SSH is transport only: no distributed scheduler, remote durable SQLite authority, implicit artifact copy or guaranteed remote-descendant cleanup.
- Validation recovery trusts only complete machine evidence under the exact run/generation/SHA contract; ambiguous evidence may require operator inspection rather than speculative rerun.
- The project intentionally omits a web UI, issue tracker, arbitrary DAG engine and generic distributed queue.

## Final acceptance boundary

R26 may be marked `DONE` only after a post-documentation HEAD passes:

- R26 audit regressions, including cancellation/result races, lease TOCTOU, crash residue, missing cycle status and JSON recovery;
- the complete automated suite;
- all twelve deterministic required scenarios;
- the seeded 100-workflow chaos sweep (`20260914`);
- documentation acceptance proving this file continues to disclose the fail-closed real-run boundary and exact Codex/Claude/shadPS4/SSH connection steps.

`ROADMAP.md` and `docs/implementation-status.md` must be changed to R26 DONE together only after that gate is green.
