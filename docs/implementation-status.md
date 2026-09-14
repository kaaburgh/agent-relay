# Implementation Status

## Current checkpoint

Completed: `R00`–`R25`.

In progress: `R26`.

Next bounded unit: `R26` — Final correctness/security review and required demonstration.

Acceptance pending: the final adversarial audit must convert material risks into executable checks, fix confirmed defects, rerun the full suite and required demonstrations, and record exact real-provider/runtime connection steps before the project can be marked complete.

## R26 WIP — final adversarial correctness/security review

R26 started only after direct R25 acceptance. The audit covers the required failure classes: races/non-atomic transitions, duplicate launches, stale candidate/evidence provenance, orphan process groups, resource lease leaks/unsafe reclaim, unsafe Git/shell behavior, malformed-output fail-open paths, history loss, credential leakage and unbounded state/log growth.

Initial read-only audit results before production changes:

- no production `shell=True` use was found; managed local processes use argv execution and SSH isolates its required remote POSIX shell behind quoted stdin transport;
- no production destructive `git reset --hard` or `git clean` use was found; managed Git work occurs in dedicated worktrees;
- candidate generation, validation/review provenance, append-only events/evidence and capacity-1 lease behavior already have direct deterministic/chaos coverage;
- `SubprocessSupervisor` currently writes child stdout/stderr directly to append-only files with no configured size bound. Long or hostile workers can therefore grow attempt logs without bound. This is a confirmed R26 design gap and requires a bounded capture strategy that does not deadlock child pipes or silently turn successful processes into failures;
- process finalization is guarded only by the in-memory `ManagedProcess._finished` flag. `operator.cancel_task()` can independently mark the same durable process `CANCELLED`; a still-live `ManagedProcess.wait()` may subsequently attempt its own `_finish` and receive `StoreError` because the durable row is already finalized. This cross-owner cancellation/finalization race requires a reproducing test and idempotent/reconciled behavior rather than an uncaught error;
- SSH explicitly does not guarantee cleanup for a remote process that deliberately daemonizes/detaches, and it does not implicitly transfer remote evidence. These are documented transport limits, not hidden guarantees.

The audit must also decide and document the real-backend composition boundary: Codex/Claude/shadPS4/SSH adapters exist and are individually acceptance-tested, while top-level ordinary `agent-relay run` remains intentionally fail-closed instead of pretending a production composition that has not been installed. R26 may keep that limitation only if the product contract is still satisfied and exact safe connection steps are provided; it must not claim a real end-to-end CLI path that does not exist.

Exact R26 recovery action:

1. Add R26-specific regression tests for the external-cancellation/finalization race and bounded stdout/stderr behavior; make them fail against the confirmed gaps before accepting a fix.
2. Implement bounded log capture without `shell=True`, preserving real process-group timeout/stall/cancel semantics and useful tail evidence.
3. Make process finalization reconcile an already-durable terminal process state safely, without overwriting a newer terminal state or losing exit metadata.
4. Review store/evidence/recovery/lease/Git/provider adapters for additional atomicity, stale-provenance, duplicate-launch, orphan, history-loss and secret-leak paths; add tests for any material finding.
5. Add `docs/final-review.md` with findings/dispositions and exact Codex/Claude/local-or-SSH shadPS4 connection steps, including current CLI-composition limitations.
6. Run the R26-specific tests, full `python -m unittest discover -s tests -v`, all twelve deterministic required scenarios, and the seeded >=100 chaos workflow demonstration on the final production HEAD.
7. Only after all material findings are fixed or explicitly bounded by the product contract may ROADMAP/status move R26 to `DONE`.

## R25 acceptance — documentation/example configuration

R25 replaced the stale scaffold README; added `docs/architecture.md`, `docs/state-machine.md`, `docs/providers.md`, `docs/simulation.md`, `docs/recovery.md`, and `docs/shadps4-example.md`; corrected the simulated CLI config to the real `options.simulated_validator: true` contract; and added credential-free Codex/Claude/SSH/Bloodborne examples plus `tests/test_documentation.py`.

The first R25 CI run `34839845987` on `d1b0aca5fa4ebe9e0f9559c70f2e8d2a8f7cfcaa` failed one documentation-contract check because the shadPS4 remote-evidence section described mounted/visible storage without explicitly naming it shared storage. That ambiguity was fixed. The second run `34839968005` on `5b0e3f1551e5e1771903da6fa61da3200e67ddc7` then exposed the same terminology gap in `docs/providers.md`; all other 154 tests passed. That second ambiguity was also fixed rather than weakening the test.

Final R25 acceptance: GitHub Actions run `34840078585` on `126f618e044a1166d3f5ddab0e0bd818f42c7b38`: PASS, 155 tests in 45.001s on Python 3.12.14, including all five documentation-specific acceptance tests and the seeded 100-workflow chaos regression.

## R24 acceptance — minimal SSH external-tool transport

Final R24 acceptance: GitHub Actions run `34836178935` on `a0b4e69abf7f8a7d0ef3f9376cd22f5a3bf008f9`: PASS, 150 tests in 64.759s. The dedicated test executes the generated remote script through real `/bin/sh -s` with shell-looking arguments/environment values and verifies they remain literal. Remote artifact transfer and arbitrary detached-remote-process cleanup remain explicit non-guarantees.

## R23 acceptance — real shadPS4/Bloodborne tool adapter

Final R23 acceptance: GitHub Actions run `34835209968` on `e4b882864cc1b84c3ccbca4c0f5ce5cb026b9d27`: PASS, 143 tests. The dedicated gate previously caught and fixed incomplete-cycle evidence being misclassified as deterministic validation failure.

## Development protocol guard

Development status is machine-checked:

- a partially landed unit is `IN PROGRESS` rather than implicitly complete;
- a full regression suite does not replace unit-specific acceptance evidence;
- WIP checkpoints name missing acceptance and exact recovery action;
- roadmap/status transitions prefer atomic Git tree commits;
- closeout checks roadmap/status/HEAD/acceptance alignment;
- `tests/test_project_status.py` makes roadmap/handoff drift a CI failure.

## Durable decisions

- Python 3.12 / Linux-first.
- Deterministic orchestrator; models are bounded workers, never state-machine owners.
- Simulation-first; real providers/runtime follow deterministic integration/recovery behavior.
- SQLite is durable/fail-closed; semantic events and completed validation/review evidence are append-only.
- Existing user checkouts are never cleaned/reset automatically; mutations happen in dedicated managed worktrees.
- Managed subprocesses use independent process groups with whole-group cleanup and sparse durable liveness metadata.
- Provider/tool process success never substitutes for deterministic Git/evidence acceptance gates.
- Malformed reviewer output and incomplete deterministic validation evidence block rather than fail open.
- Correction rounds are bounded and rejected-generation history remains auditable.
- Resource serialization and restart ownership are durable, never process-local assumptions.
- Required deterministic scenarios are explicitly named; chaos adds reproducible variation and never replaces deterministic acceptance.
- Bloodborne-specific behavior stays outside orchestration core.
- SSH is transport only; it has no implicit artifact transfer or remote orchestrator semantics.

## Handoff protocol

At every roadmap checkpoint, record completed/in-progress unit, exact commands/results, important files/modules, material decisions, unresolved limitations, missing acceptance, and exact next recovery action. Preserve useful history and never mark an acceptance gate complete without direct evidence.
