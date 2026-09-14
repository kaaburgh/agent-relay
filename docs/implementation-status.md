# Implementation Status

## Current checkpoint

Completed: `R00`–`R25`.

In progress: `R26`.

Next bounded unit: `R26` — Final correctness/security review and required demonstration.

Acceptance pending: close the remaining adversarial findings, add the final review/connection documentation, then rerun the R26-specific acceptance, full regression suite, all twelve required deterministic scenarios and seeded >=100-workflow chaos demonstration before marking the project complete.

## Latest R26 durable fact — stale lease reclaim TOCTOU CONFIRMED RED

The stale-lease reclaim race is now demonstrated by executable evidence. Test commit `9f3a2fb28d99b77bcb25971d6c2b2b45cfdb3fd8` added `ResourceLeaseTests.test_stale_reclaim_rechecks_heartbeat_after_candidate_snapshot`.

GitHub Actions run `34846111400`: FAILED, 163 tests in 47.336s with exactly one failure, the new stale-reclaim test. The test takes a stale `active_leases()` candidate snapshot, then performs a real `heartbeat_lease()` database update to `2026-09-14T06:59:59+00:00` before reclaim proceeds at `07:00:00`. Current `reclaim_stale_leases()` still returned that lease as reclaimed with `released_at=07:00:00`, proving that the release decision uses stale heartbeat state and is not revalidated atomically.

Observed invariant violation: a holder that refreshed its lease after the recovery snapshot but before the release transaction can lose the now-live lease. For a capacity-1 expensive runtime, this can make a second owner eligible while the original owner is active.

Exact next repair:

1. Keep the initial `active_leases()` enumeration as a cheap candidate scan.
2. For each candidate, enter the SQLite write transaction and re-read the lease row by `lease_id`.
3. Under that same transaction, verify it is still active, its current `heartbeat_at` is still <= the stale cutoff, it remains attempt-bound, and no durable `RUNNING` process exists for that attempt.
4. Only then write `released_at` and the `resource_released` semantic event in that same transaction; do not call a separate later `release_lease()` transaction based on the stale snapshot.
5. Run the same `test_stale_reclaim_rechecks_heartbeat_after_candidate_snapshot` green and checkpoint that result before further audit work.
6. Run the full suite before opening another material finding.

## Previous R26 durable fact — process-group cancellation CLOSED

Production commit `cd28a8027da1c0b21fa46296995dbf1d7485e349` made process-group existence authoritative instead of leader-PID liveness for operator cancellation. GitHub Actions run `34845859463`: PASS, 162 tests in 43.969s, including the previously red orphan-cancellation test, all twelve deterministic scenarios and the seeded 100-workflow chaos sweep.

Its red predecessor was run `34841585992` on `e81b04d3a3893ae25d09d7f08679deb2ebb1c2d1`, where a SIGTERM-ignoring same-PGID child survived after leader exit.

## R26 findings already closed

- **Unbounded subprocess logs** — confirmed red, then fixed with bounded tail capture and continuous pipe draining.
- **External cancellation vs in-memory finalization race** — confirmed red, then fixed so `ManagedProcess.wait()` reconciles an already-durable terminal state.
- **Late provider result after cancellation** — a durably cancelled attempt rejects late result finalization/history pollution.
- **Normal parent exit with inherited child process** — fixed so parent completion does not leave an unmanaged same-group child alive.
- **Operator cancellation after leader exit** — red in `34841585992`, green in `34845859463`.
- **Idempotent candidate freeze with stale writer ownership** — confirmed red and fixed at `e81b04d3a3893ae25d09d7f08679deb2ebb1c2d1`.
- **Unsafe local shell/destructive Git cleanup** — source audit test confirms no production `shell=True`, automatic `git reset --hard`, or `git clean` path.

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
