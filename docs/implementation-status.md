# Implementation Status

## Current checkpoint

Completed: `R00`–`R25`.

In progress: `R26`.

Next bounded unit: `R26` — Final correctness/security review and required demonstration.

Acceptance pending: close the remaining adversarial findings, add the final review/connection documentation, then rerun the R26-specific acceptance, full regression suite, all twelve required deterministic scenarios and seeded >=100-workflow chaos demonstration before marking the project complete.

## Latest R26 durable fact — operator process-group cancellation

Protocol was strengthened at commit `8ed9bf27f456301eee9da2f440e3ab1cf4697807`: every materially new CI fact is now a durable recovery boundary; a red result must be checkpointed before the next production fix; and R26 findings follow an explicit red -> checkpoint -> fix -> targeted green -> checkpoint -> full-suite loop.

The latest production HEAD before that protocol-only commit was `e81b04d3a3893ae25d09d7f08679deb2ebb1c2d1` (`R26: bind idempotent candidate freeze to exact writer ownership`). GitHub Actions run `34841585992` executed 162 tests and failed exactly one:

`test_final_audit.FinalAuditProcessTests.test_operator_cancel_kills_group_even_after_leader_dies`

Observed failure: `operator.cancel_task()` sends SIGTERM to the stored process group but `_terminate_process_group()` decides whether cleanup is complete by checking only the original leader PID. If the leader exits while a same-PGID child ignores SIGTERM, the function returns before escalating the still-existing process group to SIGKILL. The adversarial test leaves that child live and fails with `operator cancellation left a process-group child live`.

Implication: operator-side cleanup must track process-group existence, not leader-PID liveness. The intended narrow repair is to probe the PGID directly (for example `os.killpg(group, 0)` with appropriate `ProcessLookupError` handling), send SIGTERM even when the leader PID is already dead but the group still exists, wait for the group to disappear, and escalate the group to SIGKILL after the configured grace period. The existing targeted test is the acceptance gate for this repair.

Exact next action:

1. Fix `agent_relay/operator.py::_terminate_process_group()` to make PGID existence authoritative for group cleanup.
2. Run the targeted orphan-cancellation regression to green.
3. Checkpoint that green result in this file before the full suite or another material fix.
4. Run the full suite after the cancellation repair.
5. Then address the already identified stale-lease reclaim TOCTOU: `reclaim_stale_leases()` currently performs stale-heartbeat/process-state observation before a separate release transaction, so the reclaim decision must be revalidated atomically under the write transaction.
6. Continue the remaining R26 audit and final demonstration only after those findings are individually closed.

## R26 findings already closed

The final adversarial audit has already converted several risks into executable tests and fixes:

- **Unbounded subprocess logs** — confirmed red, then fixed with bounded tail capture and continuous pipe draining so noisy children cannot fill a pipe or grow attempt logs without bound.
- **External cancellation vs in-memory finalization race** — confirmed red, then fixed so `ManagedProcess.wait()` reconciles an already-durable terminal state instead of double-finalizing and raising `StoreError`.
- **Late provider result after cancellation** — a durably cancelled attempt rejects late result finalization/history pollution.
- **Normal parent exit with inherited child process** — fixed so parent completion does not leave an unmanaged same-group child alive; the supervisor separates leader death from pipe EOF and cleans the remaining group.
- **Idempotent candidate freeze with stale writer ownership** — confirmed red and fixed at `e81b04d3a3893ae25d09d7f08679deb2ebb1c2d1`; repeating the same candidate SHA is idempotent only when writer ownership and predecessor/current-generation provenance agree.
- **Unsafe local shell/destructive Git cleanup** — source audit test confirms no production `shell=True`, automatic `git reset --hard`, or `git clean` path.

A previous green checkpoint for the first group of fixes was GitHub Actions run `34841275810` on `b08f7bf1...`: 160 tests PASS, including bounded logs, cancellation reconciliation, orphan cleanup after normal parent exit, required scenarios and the seeded 100-workflow chaos sweep.

## Remaining R26 audit scope

After the operator process-group repair and lease-reclaim atomicity fix, R26 must still:

- review remaining store/evidence/recovery/provider paths for duplicate launch, stale provenance, lease leak, history loss and secret leakage;
- decide and document the production-composition boundary: real Codex/Claude/shadPS4/SSH adapters are individually acceptance-tested, while ordinary top-level `agent-relay run` remains intentionally fail-closed rather than claiming an unimplemented production composition;
- add `docs/final-review.md` with findings/dispositions and exact safe Codex/Claude/local-or-SSH shadPS4 connection steps, including current CLI-composition limitations;
- run the final R26-specific tests, full `python -m unittest discover -s tests -v`, all twelve deterministic required scenarios, and seeded >=100 chaos workflows on the same final production HEAD.

Only after those gates may `R26` move to `DONE`.

## R25 acceptance — documentation/example configuration

R25 replaced the stale scaffold README; added architecture/state-machine/providers/simulation/recovery/shadPS4 docs; corrected the simulated CLI config to the real `options.simulated_validator: true` contract; and added credential-free Codex/Claude/SSH/Bloodborne examples plus documentation acceptance tests.

Final R25 acceptance: GitHub Actions run `34840078585` on `126f618e044a1166d3f5ddab0e0bd818f42c7b38`: PASS, 155 tests on Python 3.12.14, including the seeded 100-workflow chaos regression.

## R24 acceptance — minimal SSH external-tool transport

Final R24 acceptance: GitHub Actions run `34836178935` on `a0b4e69abf7f8a7d0ef3f9376cd22f5a3bf008f9`: PASS, 150 tests. The dedicated test executes the generated remote script through real `/bin/sh -s` with shell-looking arguments/environment values and verifies they remain literal. Remote artifact transfer and arbitrary detached-remote-process cleanup remain explicit non-guarantees.

## R23 acceptance — real shadPS4/Bloodborne tool adapter

Final R23 acceptance: GitHub Actions run `34835209968` on `e4b882864cc1b84c3ccbca4c0f5ce5cb026b9d27`: PASS, 143 tests. The dedicated gate previously caught and fixed incomplete-cycle evidence being misclassified as deterministic validation failure.

## Development protocol guard

Development status is machine-checked and CI facts are now checkpointed incrementally:

- a partially landed unit is `IN PROGRESS` rather than implicitly complete;
- a full regression suite does not replace unit-specific acceptance evidence;
- every materially new CI/acceptance fact is persisted before the next material investigation/fix;
- a red result is made durable before its production repair begins;
- R26 findings use targeted red/green evidence plus a later full regression gate;
- roadmap/status transitions prefer atomic Git tree commits;
- `tests/test_project_status.py` makes roadmap/handoff drift a CI failure.

## Durable decisions

- Python 3.12 / Linux-first.
- Deterministic orchestrator; models are bounded workers, never state-machine owners.
- SQLite is durable/fail-closed; semantic events and completed validation/review evidence are append-only.
- Existing user checkouts are never cleaned/reset automatically; mutations happen in dedicated managed worktrees.
- Managed subprocesses use independent process groups; cleanup is a group-level invariant rather than a leader-PID assumption.
- Provider/tool process success never substitutes for deterministic Git/evidence acceptance gates.
- Malformed reviewer output and incomplete deterministic validation evidence block rather than fail open.
- Resource serialization and restart ownership are durable, never process-local assumptions.
- Required deterministic scenarios are explicit; chaos adds reproducible variation and never replaces deterministic acceptance.
- Bloodborne-specific behavior stays outside orchestration core.
- SSH is transport only; it has no implicit artifact transfer or remote orchestrator semantics.

## Handoff protocol

At every roadmap checkpoint and after every materially new CI/acceptance fact, record tested HEAD, run/test result, important files/modules, decisions, unresolved limitations, missing acceptance and exact next recovery action. Preserve useful history and never mark an acceptance gate complete without direct evidence.
