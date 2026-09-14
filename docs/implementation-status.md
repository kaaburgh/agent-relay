# Implementation Status

## Current checkpoint

Completed: `R00`–`R25`.

In progress: `R26`.

Next bounded unit: `R26` — Final correctness/security review and required demonstration.

Acceptance pending: finish the final read-only adversarial pass, add `docs/final-review.md` plus CI acceptance for its documented boundaries/connection steps, then run the final R26-specific/full/required-scenario/seeded-chaos demonstration on one production HEAD before marking the project complete.

## Latest R26 durable fact — per-cycle success evidence fail-open CLOSED GREEN

Production HEAD `5ab31b3469f5c4d427c427ff3ef8fd2bb9a00faa`, GitHub Actions run `34857598533`: PASS, 166 tests in 44.196s.

The R26 adversarial test `R26ShadPS4EvidenceTests.test_cycle_records_without_success_status_never_validate` is now green. `ShadPS4BloodborneValidator._cycle_failure()` requires every cycle record to contain a non-empty `status`, `result`, or `outcome`. Missing per-cycle success proof is therefore `INCOMPLETE_EVIDENCE`; explicit known failure statuses remain `VALIDATION_FAILED`, and supported explicit success statuses are required for `SUCCESS`.

Red evidence remains GitHub Actions run `34857262874` on `5352fa306e557fd9b5a847665c7314c5160750a6`: 166 tests in 45.046s with exactly one failure, where matching completed N/N evidence without per-cycle status was incorrectly accepted as `SUCCESS`.

The green run also passed all existing shadPS4 adapter tests, all twelve deterministic integration scenarios and the seeded 100-workflow chaos sweep.

## Previous R26 durable fact — uncommitted result-file crash residue CLOSED GREEN

Production HEAD `e1a5cdca548cce68f84799bbd81a76f0d20bb27b`, GitHub Actions run `34848116328`: PASS, 165 tests in 65.205s.

The previously red `FinalAuditProcessTests.test_uncommitted_orphan_result_file_does_not_block_recovery_finalization` is green. While holding the same SQLite writer lock used by all finalizers, `ArtifactManager.finalize_attempt()` revalidates that the attempt is unfinished and that no durable `artifacts(kind='result')` row exists before treating an existing managed `result.json` as crash residue. Only that proven uncommitted path is unlinked and recreated; an unfinished attempt with a durable result-artifact row fails closed instead of deleting evidence. Symlinks are unlinked as links rather than followed.

## R26 findings already closed

- unbounded subprocess logs — bounded tail capture with continuous drain;
- external cancellation vs in-memory process finalization — durable terminal-state reconciliation;
- normal parent exit with inherited child — remaining process-group cleanup;
- operator cancellation after leader exit — red `34841585992`, green `34845859463`;
- stale writer ownership during idempotent candidate freeze — exact owner/provenance validation;
- stale lease reclaim heartbeat race — red `34846111400`, green `34846335634`;
- cancellation vs provider result publication — red `34846974177`, green `34847495507` after compatibility preservation;
- result-file/SQLite crash residue — red `34847845590`, green `34848116328`;
- missing per-cycle success evidence — red `34857262874`, green `34857598533`;
- unsafe local shell/destructive Git cleanup — no production `shell=True`, automatic `git reset --hard`, or `git clean` path.

## Remaining R26 scope

Perform the final read-only pass for any remaining material finding. If none appear, add `docs/final-review.md` with findings/dispositions and exact safe Codex/Claude/local-or-SSH shadPS4 connection steps. The document must state plainly that real adapters are individually implemented/tested while ordinary top-level `agent-relay run` remains intentionally fail-closed rather than providing an integrated real-provider composition. Add CI acceptance for that boundary and then run the final R26-specific/full/12-scenario/seeded-chaos demonstration on one production HEAD.

## Development protocol guard

Protocol strengthened at `8ed9bf27f456301eee9da2f440e3ab1cf4697807`: every materially new CI fact is checkpointed before the next material fix, red results are durable before repair, and R26 uses targeted red -> checkpoint -> fix -> targeted green -> checkpoint -> full-suite progression.

## Handoff protocol

At every roadmap checkpoint and after every materially new CI/acceptance fact, record tested HEAD, run/test result, important files/modules, decisions, unresolved limitations, missing acceptance and exact next recovery action.
