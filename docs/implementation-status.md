# Implementation Status

## Current checkpoint

Completed: `R00`–`R25`.

In progress: `R26`.

Next bounded unit: `R26` — Final correctness/security review and required demonstration.

Acceptance pending: close the remaining crash-consistency finding, finish the final review/connection documentation and its CI contract, then run the final R26-specific/full/required-scenario/seeded-chaos demonstration before marking the project complete.

## Latest R26 durable fact — uncommitted result-file crash residue CONFIRMED RED

The remaining DB/filesystem crash-consistency risk is now demonstrated as a material recovery defect.

Test commit `8f227322b3229bf02fa1e5d842bfac94d9ab124f` added `FinalAuditProcessTests.test_uncommitted_orphan_result_file_does_not_block_recovery_finalization`. It models the durable state left by a hard process crash after managed `result.json` creation but before the SQLite transaction commits: the attempt row remains unfinished, there is no durable `artifacts(kind='result')` row, but the exclusive managed result path exists with crash residue.

GitHub Actions run `34847845590`: FAILED, 165 tests in 53.065s with exactly one error, the new orphan-recovery test. `ArtifactManager.finalize_attempt()` obtained the SQLite write transaction, saw the attempt still unfinished, then failed at `result_path.open('x')` with `FileExistsError`. Thus a hard crash at that point can permanently block safe recovery finalization despite SQLite correctly rolling back its transaction.

Required invariant: while holding the SQLite writer lock, an unfinished attempt with no durable result-artifact row may treat an existing managed `result.json` as uncommitted crash residue. A concurrent live finalizer cannot be writing that path at the same time because it would own the same SQLite write lock. Recovery may therefore unlink only that proven orphan path and recreate it. If a durable result-artifact row exists while the attempt is unfinished, fail closed as inconsistent state rather than deleting evidence.

Exact next repair:

1. In `ArtifactManager.finalize_attempt()`, after the unfinished-attempt re-read under `BEGIN IMMEDIATE`, query whether a durable `result` artifact row already exists for the attempt.
2. If the managed `result.json` exists and no durable result-artifact row exists, unlink that path under the writer lock as crash residue before exclusive creation.
3. If a durable result-artifact row exists while the attempt is unfinished, raise/fail closed; do not delete the path.
4. Preserve all already-green contracts: cancellation wins without late evidence, ordinary duplicate finalized attempts raise historical `FileExistsError`, and successful publication remains write-once.
5. Run the orphan-recovery test plus cancellation-race and duplicate-finalization tests green; checkpoint the result before any further material work.
6. Run the full suite before moving to final-review documentation.

## Previous R26 durable fact — cancellation/result publication CLOSED

Final production `b50313a296eeffb4dd5c99cfbe84f28d54abf20d`, GitHub Actions run `34847495507`: PASS, 164 tests in 45.452s. The cancellation/finalization race test and historical duplicate-finalization `FileExistsError` contract both passed, along with all twelve deterministic scenarios and seeded 100-workflow chaos.

## Other R26 findings already closed

- unbounded subprocess logs — bounded tail capture with continuous drain;
- external cancellation vs in-memory process finalization — durable terminal-state reconciliation;
- normal parent exit with inherited child — remaining process-group cleanup;
- operator cancellation after leader exit — red `34841585992`, green `34845859463`;
- stale writer ownership during idempotent candidate freeze — exact owner/provenance validation;
- stale lease reclaim heartbeat race — red `34846111400`, green `34846335634`;
- cancellation vs provider result publication — red `34846974177`, green `34847495507` after compatibility preservation;
- unsafe local shell/destructive Git cleanup — no production `shell=True`, automatic `git reset --hard`, or `git clean` path.

## Remaining R26 scope after this finding

After crash-residue recovery is closed, perform one final read-only pass for additional material findings. If none appear, add `docs/final-review.md` with findings/dispositions and exact safe Codex/Claude/local-or-SSH shadPS4 connection steps. The document must state plainly that real adapters are individually implemented/tested while ordinary top-level `agent-relay run` remains intentionally fail-closed rather than providing an integrated real-provider composition. Add CI acceptance for that boundary and then run the final R26-specific/full/12-scenario/seeded-chaos demonstration on one production HEAD.

## Development protocol guard

Protocol strengthened at `8ed9bf27f456301eee9da2f440e3ab1cf4697807`: every materially new CI fact is checkpointed before the next material fix, red results are durable before repair, and R26 uses targeted red -> checkpoint -> fix -> targeted green -> checkpoint -> full-suite progression.

## Handoff protocol

At every roadmap checkpoint and after every materially new CI/acceptance fact, record tested HEAD, run/test result, important files/modules, decisions, unresolved limitations, missing acceptance and exact next recovery action.
