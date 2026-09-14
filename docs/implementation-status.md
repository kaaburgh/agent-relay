# Implementation Status

## Current checkpoint

Completed: `R00`–`R26`.

In progress: none.

Next bounded unit: none — roadmap complete.

Acceptance pending: none for the implemented roadmap. R26 and the complete R00–R26 roadmap are accepted by the product/documentation gate below. Ordinary top-level real-provider `agent-relay run` remains an explicitly documented limitation/future composition concern, not an unreported acceptance gap.

## Final accepted product/documentation gate

Accepted HEAD `a9668502dda6f1f2094dd3b13bb8620de3c020f1`, GitHub Actions run `34858958981`: PASS, 169 tests in 44.090s.

This gate includes `docs/final-review.md`, its executable documentation acceptance tests and the README link. Both `FinalReviewDocumentationTests` passed, proving the final review continues to disclose the intentionally fail-closed ordinary real `agent-relay run` boundary, maps all twelve required deterministic demonstrations plus the seeded chaos sweep, and documents exact safe connection steps/boundaries for Codex, Claude, local shadPS4 and SSH-transported shadPS4.

The same accepted run passed:

- the complete automated suite;
- every R26 adversarial regression, including process-group/cancellation races, lease reclaim TOCTOU, result publication/crash residue, missing per-cycle success evidence and CSV/JSON recovery;
- all twelve `RequiredIntegrationScenarios` (`s01`–`s12`);
- `ChaosIntegrationTests.test_reproducible_100_workflow_chaos_sweep`: exactly 100 workflows with seed `20260914`;
- real-adapter contract tests for Codex, Claude, shadPS4 and SSH;
- project-status development-protocol consistency tests.

`docs/final-review.md` is the completed Definition-of-Done handoff for the implemented scope: repository/architecture, state machine, persistent schema, provider interfaces/status, simulations, leases, recovery, required demonstration mapping/results, chaos seed/count, known limitations and exact next connection steps for authenticated Codex/Luna, authenticated Claude/Opus and local/remote shadPS4/Bloodborne.

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

## Known implemented-scope limitation

Real Codex, Claude, shadPS4 and SSH adapters are individually implemented and tested. `run --simulation` is the complete operator-ready end-to-end path. Ordinary top-level `agent-relay run` without `--simulation` intentionally fails closed rather than pretending an integrated real Codex -> validation -> Claude operator composition exists. Exact component connection steps and SSH/shared-evidence boundaries are recorded in `docs/final-review.md`.

## Closeout verification

This handoff and `ROADMAP.md` are changed atomically to mark R26 DONE. The resulting metadata-only closeout HEAD must pass CI/project-status consistency; that CI is a verification of the already accepted product/documentation gate above and does not reopen production scope.

## Development protocol guard

Protocol strengthened at `8ed9bf27f456301eee9da2f440e3ab1cf4697807`: every materially new CI fact is checkpointed before the next material fix, red results are durable before repair, and R26 used targeted red -> checkpoint -> fix -> targeted green -> checkpoint -> full-suite progression.

## Handoff protocol

At every roadmap checkpoint and after every materially new CI/acceptance fact, record tested HEAD, run/test result, important files/modules, decisions, unresolved limitations, missing acceptance and exact next recovery action.
