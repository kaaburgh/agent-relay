# Implementation Status

## Current checkpoint

Completed: `R00`–`R25`.

In progress: `R26`.

Next bounded unit: `R26` — Final correctness/security review and required demonstration.

Acceptance pending: finish the remaining read-only adversarial review, add the final review/connection documentation and its CI contract, then run the final R26-specific/full/required-scenario/seeded-chaos demonstration before marking the project complete.

## Latest R26 durable fact — result publication vs cancellation CLOSED

The attempt result-publication race is closed with targeted red/green evidence and a green full regression suite.

Red: test commit `54625a22de269b9e9196de62076d4d3a550d85c6`, GitHub Actions run `34846974177`: FAILED, 164 tests with exactly one error. A real durable cancellation committed immediately after the provider's unfinished precheck; old `ArtifactManager.finalize_attempt()` continued toward result publication and later raised `StoreError: attempt 1 is already finalized`.

Serialization fix: `932395d60fce186ea8656d9a4acaf5d5087b665e` re-read attempt terminal state under `BEGIN IMMEDIATE` and serialized result-file creation, result-artifact insertion and attempt terminal DB update under the same SQLite writer lock. The targeted cancellation-race test passed, proving cancellation can win without late result publication.

The first full suite on that fix, run `34847243860`, then found one compatibility regression: the historical write-once artifact contract expected a second ordinary non-cancelled `finalize_attempt()` to raise `FileExistsError`, while the new early terminal check raised `StoreError` instead. That CI fact was checkpointed before repair.

Compatibility fix: `b50313a296eeffb4dd5c99cfbe84f28d54abf20d` preserves both invariants. `CANCELLED` remains a durable winner that returns without publishing provider result evidence; ordinary duplicate finalization preserves the existing immutable-result `FileExistsError` behavior.

Final green: GitHub Actions run `34847495507` on `b50313a296eeffb4dd5c99cfbe84f28d54abf20d`: PASS, 164 tests in 45.452s on Python 3.12.14. Both `test_cancellation_between_precheck_and_result_commit_cannot_publish_late_result` and `ArtifactTests.test_result_is_written_once_and_attempt_cannot_be_refinalized` passed, together with all twelve required deterministic scenarios and the seeded 100-workflow chaos sweep.

## Other R26 findings already closed

- **Unbounded subprocess logs** — confirmed red; bounded tail capture with continuous pipe draining prevents pipe deadlock and unbounded attempt-log growth.
- **External cancellation vs in-memory process finalization** — confirmed red; `ManagedProcess.wait()` reconciles already-durable terminal state instead of double-finalizing.
- **Normal parent exit with inherited child** — remaining same-process-group children are cleaned after leader exit, with leader death separated from pipe EOF.
- **Operator cancellation after leader exit** — red `34841585992`; group-liveness fix `cd28a8027da1c0b21fa46296995dbf1d7485e349`; green `34845859463`.
- **Idempotent candidate freeze with stale writer ownership** — exact writer ownership and predecessor/current-generation provenance are required; fixed at `e81b04d3a3893ae25d09d7f08679deb2ebb1c2d1`.
- **Stale lease reclaim heartbeat race** — red `34846111400`; atomic revalidation/release fix `af5e606693937400c8fa514a02655c5412b565d9`; green `34846335634`.
- **Cancellation vs provider result publication** — red `34846974177`; serialized publication plus compatibility fix; green `34847495507`.
- **Unsafe local shell/destructive Git cleanup** — source audit confirms no production `shell=True`, automatic `git reset --hard`, or `git clean` path.

## Remaining R26 audit scope

1. Finish a read-only pass across store/evidence/recovery/provider/artifact paths for any additional material duplicate-launch, crash-recovery, stale-provenance, lease-leak, history-loss or secret-leak findings. Any new material finding must enter the red/checkpoint/fix/green loop before production changes.
2. Explicitly assess crash consistency between SQLite attempt finalization and the managed filesystem result artifact. The current serialization closes concurrent cancellation, but SQLite and the filesystem cannot form one native transaction; determine whether an orphan `result.json` after a process crash requires recovery hardening or can be bounded/documented without violating the product contract.
3. Document the production-composition boundary. Real Codex/Claude/shadPS4/SSH adapters are individually acceptance-tested, while ordinary top-level `agent-relay run` remains intentionally fail-closed instead of claiming an integrated real-provider pipeline that does not exist.
4. Add `docs/final-review.md` with findings/dispositions and exact safe Codex/Claude/local-or-SSH shadPS4 connection steps, including shared-storage/artifact-transfer limitations and the current CLI-composition limitation.
5. Add R26 acceptance so the final-review document and declared limitations cannot silently drift.
6. Run final R26-specific tests, full `python -m unittest discover -s tests -v`, all twelve deterministic required scenarios and seeded >=100 chaos workflows on the same final production HEAD.

Only after those gates may `R26` move to `DONE`.

## Development protocol guard

Protocol strengthened at `8ed9bf27f456301eee9da2f440e3ab1cf4697807`: every materially new CI fact is checkpointed before the next material fix, red results are durable before repair, and R26 uses targeted red -> checkpoint -> fix -> targeted green -> checkpoint -> full-suite progression.

## Handoff protocol

At every roadmap checkpoint and after every materially new CI/acceptance fact, record tested HEAD, run/test result, important files/modules, decisions, unresolved limitations, missing acceptance and exact next recovery action.
