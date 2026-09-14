# Implementation Status

## Current checkpoint

Completed: `R00`, `R01`, `R02`, `R03`, `R04`, `R05`, `R06`, `R07`, `R08`, `R09`, `R10`, `R11`, `R12`, `R13`, `R14`, `R15`, `R16`.

Next bounded unit: `R17 — Review/error guardrails and correction limit`.

## R01–R15 summary

Task/config parsing, durable SQLite state/events, workflow invariants, immutable attempts/evidence, managed Git workspaces, real subprocess supervision, simulated providers/tools, happy-path and multi-generation rework orchestration, provider retry, persisted resource leases, and restart recovery for writer/expensive validation are implemented with real temporary Git/SQLite/subprocess semantics.

## R16 evidence

Implemented `agent_relay/watchdog.py`, extended `agent_relay/supervisor.py` and integrated progress supervision into `agent_relay/simulated_validator.py`; added `tests/test_watchdog.py`.

Watchdog/cleanup guarantees:

- process outcomes remain distinct: wall-clock timeout is `TIMED_OUT`, evidence-progress stall is `STALLED`, process crash/nonzero remains `FAILED`, and explicit cancellation is `CANCELLED`;
- stage timeout is an absolute monotonic deadline anchored at actual subprocess launch, so adding a progress watchdog cannot postpone or bypass it;
- stall detection ignores liveness heartbeats and watches only changes to configured evidence files, allowing a live-but-logically-stuck process to be detected;
- a run lasting longer than the stall window succeeds when it continues to update progress evidence;
- stalled validator tests preserve the partial `runner-status.json` and `cycles.csv` evidence showing the last completed/current cycle;
- stall and timeout cleanup target the whole process group, including descendants, with SIGTERM followed by SIGKILL when required;
- watchdog polling creates no semantic-event chatter;
- provider-unavailable remains a separate provider-adapter classification and is covered by the existing writer/reviewer regression suite.

The first R16 acceptance run intentionally failed because progress polling waited on the low-level subprocess and bypassed the supervisor timeout path. The fix moved timeout semantics to an absolute managed-process deadline and made the watchdog honor it. This regression is now covered explicitly.

Acceptance command:

```bash
python -m unittest discover -s tests -v
```

GitHub Actions on `86d2bddad554c512852f9727366d33acf316dab5`: PASS on Python 3.12. The preceding run `5e7c96bb9961cada8f44f7947bc3eb8807b61c52` failed exactly on timeout-vs-stall classification and was not accepted as a checkpoint.

## Current product state

The simulation/recovery core can now tell a genuinely stalled expensive run from a merely long-running one, while preserving independent wall-clock timeout and process-group cleanup semantics. Next is the final pre-suite guardrail layer: malformed review/incomplete validation must fail closed, repeated correction history must remain intact, and exceeding `max_correction_rounds` must transition durably to `BLOCKED` rather than loop indefinitely.

## Durable decisions

- Python 3.12 / Linux-first.
- Deterministic orchestrator; models are bounded workers, never state-machine owners.
- Simulation-first; real providers/runtime follow deterministic integration/recovery behavior.
- SQLite is durable/fail-closed; semantic events and completed validation/review evidence are append-only.
- Existing user checkouts are never cleaned/reset automatically; mutations happen in dedicated managed worktrees.
- Managed subprocesses use independent process groups with whole-group cleanup and sparse durable liveness metadata.
- Wall-clock timeout and evidence-progress stall are separate policies; liveness heartbeat is not proof of useful progress.
- Resource serialization is named/capacity-based and persisted rather than a global in-memory mutex.
- Restart recovery reconciles durable result/process/filesystem evidence before any expensive relaunch.
- Ambiguous expensive-validator ownership retains its lease instead of failing open.
- Bloodborne-specific behavior stays outside orchestration core.

## Handoff protocol

At every roadmap checkpoint, record completed/in-progress unit, exact commands/results, important files/modules, material decisions, unresolved limitations and exact next `READY` unit. Preserve useful history and never mark an acceptance gate complete without evidence.
