# Implementation Status

## Current checkpoint

Completed: `R00`–`R25`.

In progress: `R26`.

Next bounded unit: `R26` — Final correctness/security review and required demonstration.

Acceptance pending: close the newly confirmed shadPS4 per-cycle evidence fail-open, then finish the final review/connection documentation and its CI contract, and run the final R26-specific/full/required-scenario/seeded-chaos demonstration on one production HEAD before marking the project complete.

## Latest R26 durable fact — missing per-cycle success evidence CONFIRMED RED

Adversarial test commit `5352fa306e557fd9b5a847665c7314c5160750a6` added `R26ShadPS4EvidenceTests.test_cycle_records_without_success_status_never_validate`.

GitHub Actions run `34857262874`: FAILED, 166 tests in 45.046s with exactly one failure, the new R26 shadPS4 evidence test. The fake external harness produced a matching completed runner status, requested/completed 3/3 cycles, exactly three ordered cycle records, a summary and exit 0, but every cycle record omitted `status`/`result`/`outcome`. `ShadPS4BloodborneValidator` nevertheless returned `SUCCESS`; the test required `INCOMPLETE_EVIDENCE`.

This violates the product contract requiring exactly N successful cycle records. Presence/count/order alone cannot prove per-cycle success. Current `_cycle_failure()` silently continues when a cycle has no status field, creating a deterministic-evidence fail-open.

Exact next repair:

1. In `agent_relay/shadps4_validator.py`, make `_cycle_failure()` reject a cycle record with no non-empty `status`/`result`/`outcome` as incomplete/unsupported evidence rather than accepting it.
2. Keep explicit known failure statuses classified as `VALIDATION_FAILED`; missing/unknown success proof must remain `INCOMPLETE_EVIDENCE` through the existing `_explicit_validation_failure()` boundary.
3. Run `tests.test_r26_shadps4_evidence.R26ShadPS4EvidenceTests.test_cycle_records_without_success_status_never_validate` green and the existing shadPS4 validator tests.
4. Checkpoint the green result before any further material audit/documentation work.
5. Run the full suite, then resume final read-only audit/closeout.

## Previous R26 durable fact — uncommitted result-file crash residue CLOSED GREEN

Production HEAD `e1a5cdca548cce68f84799bbd81a76f0d20bb27b`, GitHub Actions run `34848116328`: PASS, 165 tests in 65.205s.

The previously red `FinalAuditProcessTests.test_uncommitted_orphan_result_file_does_not_block_recovery_finalization` is green. While holding the same SQLite writer lock used by all finalizers, `ArtifactManager.finalize_attempt()` revalidates that the attempt is unfinished and that no durable `artifacts(kind='result')` row exists before treating an existing managed `result.json` as crash residue. Only that proven uncommitted path is unlinked and recreated; an unfinished attempt with a durable result-artifact row fails closed instead of deleting evidence. Symlinks are unlinked as links rather than followed.

The same run also kept the established contracts green: cancellation wins without late result publication, ordinary duplicate finalized attempts preserve the historical write-once `FileExistsError`, all twelve deterministic integration scenarios pass, and the seeded 100-workflow chaos sweep passes.

## R26 findings already closed

- unbounded subprocess logs — bounded tail capture with continuous drain;
- external cancellation vs in-memory process finalization — durable terminal-state reconciliation;
- normal parent exit with inherited child — remaining process-group cleanup;
- operator cancellation after leader exit — red `34841585992`, green `34845859463`;
- stale writer ownership during idempotent candidate freeze — exact owner/provenance validation;
- stale lease reclaim heartbeat race — red `34846111400`, green `34846335634`;
- cancellation vs provider result publication — red `34846974177`, green `34847495507` after compatibility preservation;
- result-file/SQLite crash residue — red `34847845590`, green `34848116328`;
- unsafe local shell/destructive Git cleanup — no production `shell=True`, automatic `git reset --hard`, or `git clean` path.

## Remaining R26 scope after this finding

Perform the remaining read-only pass for additional material findings. If none appear, add `docs/final-review.md` with findings/dispositions and exact safe Codex/Claude/local-or-SSH shadPS4 connection steps. The document must state plainly that real adapters are individually implemented/tested while ordinary top-level `agent-relay run` remains intentionally fail-closed rather than providing an integrated real-provider composition. Add CI acceptance for that boundary and then run the final R26-specific/full/12-scenario/seeded-chaos demonstration on one production HEAD.

## Development protocol guard

Protocol strengthened at `8ed9bf27f456301eee9da2f440e3ab1cf4697807`: every materially new CI fact is checkpointed before the next material fix, red results are durable before repair, and R26 uses targeted red -> checkpoint -> fix -> targeted green -> checkpoint -> full-suite progression.

## Handoff protocol

At every roadmap checkpoint and after every materially new CI/acceptance fact, record tested HEAD, run/test result, important files/modules, decisions, unresolved limitations, missing acceptance and exact next recovery action.
