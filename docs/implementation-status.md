# Implementation Status

## Current checkpoint

Completed: `R00`–`R25`.

In progress: `R26`.

Next bounded unit: `R26` — Final correctness/security review and required demonstration.

Acceptance pending: close the remaining adversarial findings, add the final review/connection documentation, then rerun the R26-specific acceptance, full regression suite, all twelve required deterministic scenarios and seeded >=100-workflow chaos demonstration before marking the project complete.

## Latest R26 durable fact — process-group cancellation finding CLOSED

The operator orphan-cancellation defect is now closed. Production commit `cd28a8027da1c0b21fa46296995dbf1d7485e349` changed operator cleanup to make process-group existence authoritative instead of leader-PID liveness. It probes the PGID, sends SIGTERM even if the original leader has already exited while children remain, waits for live group members, and escalates the group to SIGKILL after grace. Linux `/proc` state is used to avoid treating zombie-only groups as live work.

GitHub Actions run `34845859463` on `cd28a8027da1c0b21fa46296995dbf1d7485e349`: PASS, 162 tests in 43.969s on Python 3.12.14. The previously red `test_final_audit.FinalAuditProcessTests.test_operator_cancel_kills_group_even_after_leader_dies` passed, together with all twelve required deterministic scenarios and the seeded 100-workflow chaos sweep.

The red predecessor was run `34841585992` on `e81b04d3a3893ae25d09d7f08679deb2ebb1c2d1`, where exactly that test failed because a SIGTERM-ignoring same-PGID child survived after leader exit. The red/green pair is now durable evidence for this finding.

## Next R26 finding — stale lease reclaim TOCTOU

The next material finding is `agent_relay/resource_leases.py::reclaim_stale_leases()`. Current code takes an `active_leases()` snapshot, decides a heartbeat is stale and checks for a RUNNING process, then later calls `release_lease()` in a separate transaction. A holder can refresh its heartbeat between the stale snapshot and the release transaction, yet the stale reclaim can still release the now-live lease.

Exact next action, before any production fix:

1. Add an adversarial lease test that injects a real `heartbeat_lease()` database update after `active_leases()` returns the stale snapshot but before reclaim releases it. The expected invariant is that a freshly heartbeated lease remains active and is not returned as reclaimed.
2. Demonstrate that test red against the current implementation and checkpoint the exact run/result.
3. Repair `reclaim_stale_leases()` so each candidate's current heartbeat, active ownership and absence of a durable RUNNING process are revalidated atomically under the SQLite write transaction immediately before the release/event write.
4. Run the same targeted test green and checkpoint it before continuing the remaining audit.
5. Then run the full suite before opening another material finding.

## R26 findings already closed

- **Unbounded subprocess logs** — confirmed red, then fixed with bounded tail capture and continuous pipe draining so noisy children cannot fill a pipe or grow attempt logs without bound.
- **External cancellation vs in-memory finalization race** — confirmed red, then fixed so `ManagedProcess.wait()` reconciles an already-durable terminal state instead of double-finalizing and raising `StoreError`.
- **Late provider result after cancellation** — a durably cancelled attempt rejects late result finalization/history pollution.
- **Normal parent exit with inherited child process** — fixed so parent completion does not leave an unmanaged same-group child alive; leader death is separated from pipe EOF and the remaining group is cleaned.
- **Operator cancellation after leader exit** — red in `34841585992`, green in `34845859463`; surviving same-group children are now escalated independently of leader PID.
- **Idempotent candidate freeze with stale writer ownership** — confirmed red and fixed at `e81b04d3a3893ae25d09d7f08679deb2ebb1c2d1`; repeating the same candidate SHA is idempotent only when writer ownership and predecessor/current-generation provenance agree.
- **Unsafe local shell/destructive Git cleanup** — source audit test confirms no production `shell=True`, automatic `git reset --hard`, or `git clean` path.

A previous green checkpoint for the first group of fixes was GitHub Actions run `34841275810` on `b08f7bf1...`: 160 tests PASS.

## Remaining R26 audit scope

After lease-reclaim atomicity is closed, R26 must still:

- review remaining store/evidence/recovery/provider paths for duplicate launch, stale provenance, lease leak, history loss and secret leakage;
- decide and document the production-composition boundary: real Codex/Claude/shadPS4/SSH adapters are individually acceptance-tested, while ordinary top-level `agent-relay run` remains intentionally fail-closed rather than claiming an unimplemented production composition;
- add `docs/final-review.md` with findings/dispositions and exact safe Codex/Claude/local-or-SSH shadPS4 connection steps, including current CLI-composition limitations;
- run the final R26-specific tests, full `python -m unittest discover -s tests -v`, all twelve deterministic required scenarios, and seeded >=100 chaos workflows on the same final production HEAD.

Only after those gates may `R26` move to `DONE`.

## Development protocol guard

Protocol was strengthened at `8ed9bf27f456301eee9da2f440e3ab1cf4697807`:

- every materially new CI fact is a durable recovery boundary;
- red results are checkpointed before production repair;
- R26 findings use red -> durable checkpoint -> fix -> targeted green -> durable checkpoint -> full-suite progression;
- a partially landed unit remains `IN PROGRESS`;
- a full regression suite does not replace unit-specific acceptance evidence;
- `tests/test_project_status.py` makes roadmap/handoff drift a CI failure.

## Durable decisions

- Python 3.12 / Linux-first.
- Deterministic orchestrator; models are bounded workers, never state-machine owners.
- SQLite is durable/fail-closed; semantic events and completed validation/review evidence are append-only.
- Existing user checkouts are never cleaned/reset automatically; mutations happen in dedicated managed worktrees.
- Managed subprocesses use independent process groups; cleanup is a group-level invariant rather than a leader-PID assumption.
- Provider/tool process success never substitutes for deterministic Git/evidence acceptance gates.
- Resource serialization and restart ownership are durable, never process-local assumptions.
- Required deterministic scenarios are explicit; chaos adds reproducible variation and never replaces deterministic acceptance.
- Bloodborne-specific behavior stays outside orchestration core.
- SSH is transport only; it has no implicit artifact transfer or remote orchestrator semantics.

## Handoff protocol

At every roadmap checkpoint and after every materially new CI/acceptance fact, record tested HEAD, run/test result, important files/modules, decisions, unresolved limitations, missing acceptance and exact next recovery action. Preserve useful history and never mark an acceptance gate complete without direct evidence.
