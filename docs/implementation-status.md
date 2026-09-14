# Implementation Status

## Current checkpoint

Completed: `R00`–`R25`.

In progress: `R26`.

Next bounded unit: `R26` — Final correctness/security review and required demonstration.

Acceptance pending: finish the remaining adversarial review, add the final review/connection documentation, then rerun the R26-specific acceptance, full regression suite, all twelve required deterministic scenarios and seeded >=100-workflow chaos demonstration before marking the project complete.

## Latest R26 durable fact — stale lease reclaim TOCTOU CLOSED

The stale-lease reclaim race is closed with a direct red/green pair.

Red: test commit `9f3a2fb28d99b77bcb25971d6c2b2b45cfdb3fd8`, GitHub Actions run `34846111400`: FAILED, 163 tests with exactly one failure, `ResourceLeaseTests.test_stale_reclaim_rechecks_heartbeat_after_candidate_snapshot`. The test recorded a real heartbeat at `06:59:59` after the stale candidate snapshot, but old reclaim logic still released the lease at `07:00:00`.

Fix: production commit `af5e606693937400c8fa514a02655c5412b565d9` keeps the cheap candidate scan but, for each candidate, enters the SQLite write transaction and re-reads current lease state. Under the same transaction it revalidates active status, current heartbeat cutoff, attempt binding, and absence of a durable `RUNNING` process, then writes `released_at` plus `resource_released` event atomically. It no longer calls a later separate `release_lease()` based on stale snapshot state.

Green: GitHub Actions run `34846335634` on `af5e606693937400c8fa514a02655c5412b565d9`: PASS, 163 tests in 46.413s on Python 3.12.14. The same heartbeat-after-snapshot adversarial test passed, together with all twelve deterministic scenarios and the seeded 100-workflow chaos sweep.

## Previous R26 durable fact — process-group cancellation CLOSED

Production commit `cd28a8027da1c0b21fa46296995dbf1d7485e349` made process-group existence authoritative instead of leader-PID liveness for operator cancellation. GitHub Actions run `34845859463`: PASS, 162 tests in 43.969s, including the previously red orphan-cancellation test, all twelve deterministic scenarios and the seeded 100-workflow chaos sweep.

## R26 findings already closed

- **Unbounded subprocess logs** — confirmed red, then fixed with bounded tail capture and continuous pipe draining.
- **External cancellation vs in-memory finalization race** — confirmed red, then fixed so `ManagedProcess.wait()` reconciles an already-durable terminal state.
- **Late provider result after cancellation** — a durably cancelled attempt rejects late result finalization/history pollution.
- **Normal parent exit with inherited child process** — fixed so parent completion does not leave an unmanaged same-group child alive.
- **Operator cancellation after leader exit** — red in `34841585992`, green in `34845859463`.
- **Idempotent candidate freeze with stale writer ownership** — confirmed red and fixed at `e81b04d3a3893ae25d09d7f08679deb2ebb1c2d1`.
- **Stale lease reclaim heartbeat race** — red in `34846111400`, green in `34846335634`; release decision is now revalidated and committed atomically.
- **Unsafe local shell/destructive Git cleanup** — source audit test confirms no production `shell=True`, automatic `git reset --hard`, or `git clean` path.

## Remaining R26 audit scope

R26 must still:

- perform a final read-only pass across store/evidence/recovery/provider code for additional duplicate-launch, stale-provenance, lease-leak, history-loss and secret-leak paths; material findings must follow the red/checkpoint/fix/green protocol;
- decide and document the production-composition boundary: real Codex/Claude/shadPS4/SSH adapters are individually acceptance-tested, while ordinary top-level `agent-relay run` remains intentionally fail-closed rather than claiming an unimplemented production composition;
- add `docs/final-review.md` with findings/dispositions and exact safe Codex/Claude/local-or-SSH shadPS4 connection steps, including current CLI-composition limitations;
- add/extend R26 acceptance so the final review document and declared composition boundary cannot silently drift;
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
