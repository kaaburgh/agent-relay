# Recovery

Recovery is evidence reconciliation, not automatic rerun.

## What is durable

The SQLite store and attempt directories preserve workflow stage, stage attempts, candidate generation/SHA, process metadata, provider results, validation/review evidence, resource leases, provider retry metadata and append-only semantic events.

A restarted orchestrator should first inspect those facts before deciding whether another provider/tool process is safe.

## Writer recovery

A writer process can outlive the orchestrator process. The worker writes an independent provider result and may commit a candidate while the Store owner is absent. On reopen, recovery inspects:

- original immutable writer attempt;
- last durable process ownership/state;
- provider result;
- writer worktree HEAD/cleanliness;
- frozen baseline and existing candidate-generation row.

If the candidate/result prove completion, recovery records/reuses the original attempt and candidate idempotently. It must not allocate a duplicate writer attempt merely because the callback was missed.

If the process is still live, ownership remains with that attempt and no duplicate is launched. Missing/contradictory evidence fails closed.

## External validation recovery

Expensive validation couples process ownership with a persisted resource lease. Complete recovery requires matching run ID, candidate generation/SHA and complete machine-readable evidence.

If a separately running validator finishes while the orchestrator is absent, reopen/reconcile finalizes the same attempt, records validation and releases its lease.

If the durable process is dead but evidence is incomplete, the state is ambiguous. Recovery creates no successful validation and intentionally retains the lease instead of risking a second expensive runtime overlapping an uncertain first run.

## Real-provider crash I/O boundary

Durable launch authorization does not by itself make provider stdin/stdout/stderr survive orchestrator death. The current local supervisor still owns those pipe endpoints. An authorized provider may therefore see EOF/partial stdin or lose captured terminal output if the orchestrator process or machine dies while it is running.

Until a crash-surviving real-provider I/O/completion protocol is implemented:

- `authorized_at`, a dead process, or a candidate commit alone must never prove provider success;
- automatic recovery requires the provider-specific durable terminal evidence expected by that recovery path;
- when that evidence is missing after orchestrator loss, recovery remains ambiguous/fail-closed and explicit local/operator recovery is allowed;
- this limitation is acceptable while ordinary unattended real-provider `agent-relay run` is not enabled;
- enabling unattended real Codex/Claude orchestration requires a crash-surviving input/output/completion contract first.

Track the implementation work in issue #37 together with the related real-provider recovery boundaries (#14 and #18). This is intentionally not a merge blocker for PR #28.

## Provider retry

Temporary quota/rate-limit/unavailability enters `WAITING_PROVIDER` with durable first/last timestamps, attempt count and next retry. Retry uses bounded exponential backoff and resumes the interrupted stage only when due. Wait metadata is cleared only after provider success; history remains in events.

## Operator resume/cancel

`agent-relay resume` is conservative. It may start a due provider retry, but it does not blindly relaunch an ambiguous active stage. A live process is reported as already running; a dead but unreconciled active stage requires its recovery adapter.

`agent-relay cancel` terminates active managed local process groups before committing `CANCELLED` state. SSH cancellation has the same local SSH-process guarantee but cannot promise cleanup of a remote process that deliberately daemonized/detached from the SSH session.

## Failure categories

Keep these distinct during recovery:

- deterministic validation failure;
- incomplete/malformed evidence;
- ordinary process failure/crash;
- stage timeout;
- evidence-progress stall;
- provider unavailable;
- explicit operator cancellation;
- ambiguous ownership after restart.

Conflating them encourages unsafe reruns or fail-open completion.

## Tests

Important recovery coverage uses real temporary subprocesses and SQLite files:

```bash
python -m unittest tests.test_writer_recovery -v
python -m unittest tests.test_validator_recovery -v
python -m unittest tests.test_provider_retry -v
python -m unittest tests.test_required_integration_scenarios -v
```
