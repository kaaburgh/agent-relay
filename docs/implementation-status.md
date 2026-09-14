# Implementation Status

## Current checkpoint

Completed: `R00`–`R25`.

In progress: `R26`.

Next bounded unit: `R26` — Final correctness/security review and required demonstration.

Acceptance pending: close the remaining adversarial findings, add the final review/connection documentation, then rerun the R26-specific acceptance, full regression suite, all twelve required deterministic scenarios and seeded >=100-workflow chaos demonstration before marking the project complete.

## Latest R26 durable fact — result-publication race targeted GREEN, full-suite compatibility regression RED

The cancellation/result-publication race itself is fixed by production commit `932395d60fce186ea8656d9a4acaf5d5087b665e`. `ArtifactManager.finalize_attempt()` now re-reads terminal state under `BEGIN IMMEDIATE` and serializes result-file creation, result-artifact insertion and attempt terminal DB update while holding the same SQLite writer lock. If cancellation obtained the lock first, the provider path returns the durable `CANCELLED` attempt without publishing a result. If result finalization obtained the lock first, cancellation cannot interleave with partial publication.

GitHub Actions run `34847243860` on `932395d60fce186ea8656d9a4acaf5d5087b665e` established two facts:

1. The new targeted adversarial test `test_cancellation_between_precheck_and_result_commit_cannot_publish_late_result` **passed**. This closes the originally demonstrated cancellation interleaving at the targeted level.
2. The full suite still failed: 164 tests with exactly one error in the pre-existing `ArtifactTests.test_result_is_written_once_and_attempt_cannot_be_refinalized`. That test deliberately expects a second non-cancelled `finalize_attempt()` call to retain the historical write-once filesystem contract and raise `FileExistsError`. The new fast terminal precheck instead raises `StoreError: attempt 1 is already finalized` before touching the immutable result path.

This is a compatibility regression in externally observable artifact semantics, not a failure of the new cancellation serialization. The repair must preserve both invariants:

- `CANCELLED` wins without publishing late result evidence;
- a second ordinary/non-cancelled finalize remains rejected with `FileExistsError` when the immutable result file already exists.

Exact next action:

1. Adjust `ArtifactManager.finalize_attempt()` terminal handling so an already-finalized non-cancelled attempt with an existing immutable result path raises `FileExistsError` as before, both on the cheap precheck and on a concurrent terminal re-read under the write transaction. Do not weaken the `CANCELLED` fast/rechecked return path.
2. Rerun both the targeted cancellation-race test and `ArtifactTests.test_result_is_written_once_and_attempt_cannot_be_refinalized` to green; checkpoint that fact.
3. Run the full suite; only after it is green continue the final read-only audit/documentation work.

## Previous R26 finding — result publication race RED evidence

Test commit `54625a22de269b9e9196de62076d4d3a550d85c6`, run `34846974177`: FAILED, 164 tests with exactly one error. Cancellation committed immediately after the provider's unfinished precheck and old code later raised `StoreError` at `Store.finish_attempt()` after crossing the result-publication boundary.

## R26 finding — stale lease reclaim TOCTOU CLOSED

Red `34846111400` proved a heartbeat refreshed after candidate snapshot could still be reclaimed. Fix `af5e606693937400c8fa514a02655c5412b565d9` revalidates heartbeat/ownership/RUNNING-process state and commits release/event under one write transaction. Green `34846335634`: PASS, 163 tests.

## R26 finding — process-group cancellation CLOSED

Production `cd28a8027da1c0b21fa46296995dbf1d7485e349` made process-group existence authoritative instead of leader-PID liveness. Run `34845859463`: PASS, 162 tests, including the previously red orphan-cancellation test.

## Other R26 findings already closed

- unbounded subprocess logs — bounded tail capture with continuous drain;
- external cancellation vs in-memory process finalization — durable-state reconciliation;
- normal parent exit with inherited child — remaining process-group cleanup;
- stale writer ownership during idempotent candidate freeze — exact owner/provenance validation;
- unsafe local shell/destructive Git cleanup — source audit confirms no production `shell=True`, automatic `git reset --hard`, or `git clean` path.

## Remaining R26 audit scope

After the artifact compatibility regression is closed, R26 must still finish the read-only store/evidence/recovery/provider audit; document the intentionally fail-closed ordinary real `agent-relay run` composition boundary; add `docs/final-review.md` with findings/dispositions and exact authenticated Codex/Claude/local-or-SSH shadPS4 connection steps; add CI acceptance for that final review; and perform the final R26-specific/full/12-scenario/seeded-chaos demonstration on one final production HEAD.

Only after those gates may `R26` move to `DONE`.

## Development protocol guard

Protocol strengthened at `8ed9bf27f456301eee9da2f440e3ab1cf4697807`: every materially new CI fact is checkpointed before the next material fix, red results are durable before repair, and R26 uses targeted red -> checkpoint -> fix -> targeted green -> checkpoint -> full-suite progression.

## Handoff protocol

At every roadmap checkpoint and after every materially new CI/acceptance fact, record tested HEAD, run/test result, important files/modules, decisions, unresolved limitations, missing acceptance and exact next recovery action.
