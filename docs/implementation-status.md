# Implementation Status

## Current checkpoint

Completed: `R00`–`R25`.

In progress: `R26`.

Next bounded unit: `R26` — Final correctness/security review and required demonstration.

Acceptance pending: close the confirmed JSON validation-recovery gap, then add `docs/final-review.md` plus CI acceptance for its documented boundaries/connection steps and run the final R26 demonstration on one production HEAD before marking the project complete.

## Latest R26 durable fact — JSON validation recovery gap CONFIRMED RED

Adversarial test commit `dc068c89a4424a1b73614426680b570e45afb766` added `R26ValidatorRecoveryJsonTests.test_completed_json_validation_evidence_recovers_without_rerun`.

GitHub Actions run `34858018738`: FAILED, 167 tests in 45.746s with exactly one failure, the new JSON recovery test. The durable validation attempt had a matching completed `runner-status.json`, exactly three ordered successful records in the real adapter's supported `cycles.json` format, `summary.md`, an active capacity-one lease and no live process. `reconcile_validation_attempt()` returned `AMBIGUOUS` instead of `RECOVERED` because recovery only looked for `cycles.csv`.

This is a restart-recovery defect: the real shadPS4 adapter accepts both `cycles.csv` and `cycles.json`, but the durable recovery path could not recover the JSON success form. A successful expensive run could therefore remain ambiguous and retain its lease rather than being reused without rerun.

Exact next repair:

1. Extend validator recovery to read the same supported CSV/JSON cycle evidence boundary, without inventing evidence or launching a new attempt.
2. Return/register the actual cycle-evidence path (`cycles.csv` or `cycles.json`) used for recovery.
3. Preserve fail-closed behavior for malformed/incomplete evidence and keep the existing CSV recovery tests unchanged.
4. Make `R26ValidatorRecoveryJsonTests.test_completed_json_validation_evidence_recovers_without_rerun` green, run existing validator recovery tests and the full suite, then checkpoint the green result before documentation work.
5. After green, freeze production audit scope unless the fix itself exposes a new material regression; proceed to final review documentation and final demonstration.

## Previous R26 durable fact — per-cycle success evidence fail-open CLOSED GREEN

Production HEAD `5ab31b3469f5c4d427c427ff3ef8fd2bb9a00faa`, GitHub Actions run `34857598533`: PASS, 166 tests in 44.196s.

`ShadPS4BloodborneValidator._cycle_failure()` now requires every cycle record to contain a non-empty `status`, `result`, or `outcome`. Missing per-cycle success proof is `INCOMPLETE_EVIDENCE`; explicit known failure statuses remain `VALIDATION_FAILED`.

Red evidence: GitHub Actions run `34857262874` on `5352fa306e557fd9b5a847665c7314c5160750a6` — 166 tests with exactly one failure where matching completed N/N evidence without per-cycle status was incorrectly accepted as `SUCCESS`.

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

## Development protocol guard

Protocol strengthened at `8ed9bf27f456301eee9da2f440e3ab1cf4697807`: every materially new CI fact is checkpointed before the next material fix, red results are durable before repair, and R26 uses targeted red -> checkpoint -> fix -> targeted green -> checkpoint -> full-suite progression.

## Handoff protocol

At every roadmap checkpoint and after every materially new CI/acceptance fact, record tested HEAD, run/test result, important files/modules, decisions, unresolved limitations, missing acceptance and exact next recovery action.
