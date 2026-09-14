# Implementation Status

## Current checkpoint

Completed: `R00`–`R25`.

In progress: `R26`.

Next bounded unit: `R26` — Final correctness/security review and required demonstration.

Acceptance pending: close the remaining adversarial findings, add the final review/connection documentation, then rerun the R26-specific acceptance, full regression suite, all twelve required deterministic scenarios and seeded >=100-workflow chaos demonstration before marking the project complete.

## Latest R26 durable fact — attempt result publication race CONFIRMED RED

Read-only audit found a second cancellation boundary in `ArtifactManager.finalize_attempt()`: it checks `attempt.ended_at`, then writes `result.json`, registers the result artifact, and only afterwards calls `Store.finish_attempt()` in a separate transaction. Operator cancellation can commit between the precheck and final attempt update.

Test commit `54625a22de269b9e9196de62076d4d3a550d85c6` added `FinalAuditProcessTests.test_cancellation_between_precheck_and_result_commit_cannot_publish_late_result`. The test injects a real durable `CANCELLED` finalization immediately after the provider path reads an unfinished attempt, reproducing the interleaving without mocking SQLite state.

GitHub Actions run `34846974177`: FAILED, 164 tests in 47.761s with exactly one error, the new race test. Current `ArtifactManager.finalize_attempt()` proceeded from the stale precheck and ultimately raised `StoreError: attempt 1 is already finalized` at `Store.finish_attempt()`. Before that error the old path had already crossed the result-publication boundary, so cancellation and provider result publication are not serialized.

Required invariant: either provider finalization obtains the durable write serialization point first and publishes one terminal result, or cancellation obtains it first and the provider returns the existing `CANCELLED` attempt without creating `result.json` or a result artifact. A stale provider callback must not partially publish history.

Exact next repair:

1. Keep an optional cheap initial cancellation check, but re-read the attempt under `BEGIN IMMEDIATE` before publishing a provider result.
2. Under the same SQLite write transaction, verify the attempt remains unfinished; if it is already `CANCELLED`, return that durable state without writing result evidence; if another non-cancel terminal result exists, fail closed as before.
3. While holding that transaction, write the exclusive result file, insert the result artifact row, and update the attempt terminal fields as one serialized publication operation. If an exception rolls the DB transaction back after creating the file, remove only the file created by that invocation.
4. Run the same targeted race test green and checkpoint it before another material audit/fix.
5. Run the full suite before opening another material finding.

## R26 finding — stale lease reclaim TOCTOU CLOSED

Red: `9f3a2fb28d99b77bcb25971d6c2b2b45cfdb3fd8`, run `34846111400`, exactly one failure proving a fresh heartbeat could be reclaimed from a stale snapshot.

Fix: `af5e606693937400c8fa514a02655c5412b565d9` atomically re-reads current lease heartbeat/ownership/process state and commits release/event under one write transaction.

Green: run `34846335634`: PASS, 163 tests in 46.413s, including the same race test, all twelve deterministic scenarios and seeded 100-workflow chaos.

## R26 finding — process-group cancellation CLOSED

Production `cd28a8027da1c0b21fa46296995dbf1d7485e349` made process-group existence authoritative instead of leader-PID liveness. Run `34845859463`: PASS, 162 tests, including the previously red orphan-cancellation test, all twelve deterministic scenarios and seeded 100-workflow chaos.

## Other R26 findings already closed

- **Unbounded subprocess logs** — confirmed red, fixed with bounded tail capture and continuous drain.
- **External cancellation vs in-memory finalization race** — confirmed red, fixed by durable terminal-state reconciliation.
- **Late provider result when cancellation was already durable before finalization entered** — closed; the new finding is the narrower concurrent gap after the initial precheck.
- **Normal parent exit with inherited child process** — fixed with leader-death/pipe-EOF separation and remaining-group cleanup.
- **Idempotent candidate freeze with stale writer ownership** — confirmed red and fixed at `e81b04d3a3893ae25d09d7f08679deb2ebb1c2d1`.
- **Unsafe local shell/destructive Git cleanup** — audit test confirms no production `shell=True`, automatic `git reset --hard`, or `git clean` path.

## Remaining R26 audit scope

After the result-publication race is closed, R26 must still:

- finish the read-only pass across store/evidence/recovery/provider code; material findings follow the red/checkpoint/fix/green protocol;
- document the production-composition boundary. `docs/spec.md` requires reporting real adapter status, known limitations and exact next authenticated-provider/runtime steps; the current ordinary top-level `agent-relay run` deliberately remains fail-closed even though Codex/Claude/shadPS4/SSH adapters are individually acceptance-tested. The final handoff must not imply an integrated real CLI path that does not exist;
- add `docs/final-review.md` with findings/dispositions and exact safe Codex/Claude/local-or-SSH shadPS4 connection steps;
- add R26 acceptance so the final review document and declared composition boundary cannot silently drift;
- run final R26-specific tests, the full suite, all twelve deterministic scenarios and seeded >=100 chaos workflows on the same final production HEAD.

Only after those gates may `R26` move to `DONE`.

## Development protocol guard

Protocol strengthened at `8ed9bf27f456301eee9da2f440e3ab1cf4697807`:

- every materially new CI fact is a durable recovery boundary;
- red results are checkpointed before production repair;
- R26 findings use red -> durable checkpoint -> fix -> targeted green -> durable checkpoint -> full-suite progression;
- unit status remains `IN PROGRESS` until its own acceptance is demonstrated;
- `tests/test_project_status.py` makes roadmap/handoff drift a CI failure.

## Handoff protocol

At every roadmap checkpoint and after every materially new CI/acceptance fact, record tested HEAD, run/test result, important files/modules, decisions, unresolved limitations, missing acceptance and exact next recovery action. Preserve useful history and never mark an acceptance gate complete without direct evidence.
