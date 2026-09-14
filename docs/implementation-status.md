# Implementation Status

## Current checkpoint

Completed: `R00`–`R25`.

In progress: `R26`.

Next bounded unit: `R26` — Final correctness/security review and required demonstration.

Acceptance pending: production audit is frozen after the final confirmed recovery fix; add `docs/final-review.md` plus CI acceptance for its documented boundaries/connection steps, then run the final R26-specific/full/required-scenario/seeded-chaos demonstration on one production HEAD before marking the project complete.

## Latest R26 durable fact — JSON validation recovery gap CLOSED GREEN

Production HEAD `3a79fe1571a1522afffd88a2ac2b9de58a6d32a2`, GitHub Actions run `34858291629`: PASS, 167 tests in 53.786s.

`R26ValidatorRecoveryJsonTests.test_completed_json_validation_evidence_recovers_without_rerun` is green. Durable validator recovery now reads either `cycles.csv` or `cycles.json`, accepts the same canonical success-state/status aliases used by the real shadPS4 boundary, verifies exact requested/completed counts and ordered cycle identifiers when present, requires explicit per-cycle success proof, registers the actual cycle-evidence path, reuses the existing attempt and releases the held runtime lease. Incomplete or malformed evidence remains `AMBIGUOUS` and retains its lease.

Red evidence remains GitHub Actions run `34858018738` on `dc068c89a4424a1b73614426680b570e45afb766`: 167 tests in 45.746s with exactly one failure, where a complete successful `cycles.json` run returned `AMBIGUOUS` instead of `RECOVERED`.

The green run also passed existing CSV validator recovery tests, all shadPS4 adapter tests, all twelve deterministic integration scenarios and the seeded 100-workflow chaos sweep.

## Previous R26 durable fact — per-cycle success evidence fail-open CLOSED GREEN

Production HEAD `5ab31b3469f5c4d427c427ff3ef8fd2bb9a00faa`, GitHub Actions run `34857598533`: PASS, 166 tests in 44.196s.

`ShadPS4BloodborneValidator._cycle_failure()` requires every cycle record to contain a non-empty `status`, `result`, or `outcome`. Missing per-cycle success proof is `INCOMPLETE_EVIDENCE`; explicit known failure statuses remain `VALIDATION_FAILED`.

## R26 findings closed by the adversarial audit

- unbounded subprocess logs — bounded tail capture with continuous drain;
- external cancellation vs in-memory process finalization — durable terminal-state reconciliation;
- normal parent exit with inherited child — remaining process-group cleanup;
- operator cancellation after leader exit — red `34841585992`, green `34845859463`;
- stale writer ownership during idempotent candidate freeze — exact owner/provenance validation;
- stale lease reclaim heartbeat race — red `34846111400`, green `34846335634`;
- cancellation vs provider result publication — red `34846974177`, green `34847495507` after compatibility preservation;
- result-file/SQLite crash residue — red `34847845590`, green `34848116328`;
- missing per-cycle success evidence — red `34857262874`, green `34857598533`;
- CSV/JSON validation recovery mismatch — red `34858018738`, green `34858291629`;
- unsafe local shell/destructive Git cleanup — no production `shell=True`, automatic `git reset --hard`, or `git clean` path.

## Audit conclusion and remaining R26 scope

The bounded production-code adversarial audit is now frozen. No additional material production finding remains open from the required R26 categories. The remaining work is acceptance/documentation only:

1. add `docs/final-review.md` with findings/dispositions, architecture/state/schema/provider/recovery summary, known limitations and exact safe Codex/Claude/local-or-SSH shadPS4 connection steps;
2. explicitly state that real adapters are individually implemented/tested while ordinary top-level `agent-relay run` remains intentionally fail-closed rather than providing an integrated real-provider composition;
3. add CI acceptance for those documented boundaries and required demonstration mapping;
4. run one final R26 production/documentation HEAD through the R26-specific tests, full suite, all twelve deterministic scenarios and seeded >=100 chaos workflows;
5. if green, atomically mark `R26` DONE and synchronize `ROADMAP.md` / this handoff, then verify final metadata CI.

## Development protocol guard

Protocol strengthened at `8ed9bf27f456301eee9da2f440e3ab1cf4697807`: every materially new CI fact is checkpointed before the next material fix, red results are durable before repair, and R26 uses targeted red -> checkpoint -> fix -> targeted green -> checkpoint -> full-suite progression.

## Handoff protocol

At every roadmap checkpoint and after every materially new CI/acceptance fact, record tested HEAD, run/test result, important files/modules, decisions, unresolved limitations, missing acceptance and exact next recovery action.
