# Simulation

Simulation is a major product surface, not a mock-only test shortcut. It exercises real subprocesses, Git commits/worktrees, SQLite state, process cleanup and durable artifacts without provider quota, GPU, shadPS4 or credentials.

## Simulated writer

The fake writer is a separate process driven by declarative actions. It can sleep, modify files, make real Git commits, return success/failure, produce no commit, crash, hang, emit malformed output, report provider unavailability/rate limit, and leave partial work before crashing.

A successful worker result does not invent a candidate: a clean committed descendant must exist in the writer worktree.

## Simulated reviewer

Each review is a fresh process/invocation bound to an exact detached candidate worktree. Behaviors cover all structured verdicts plus malformed output, process failure, hang and provider unavailability. Structured output is parsed with the same fail-closed core contract used by real review.

## Simulated expensive validator

The fake runtime produces incremental machine evidence:

```text
evidence/<run-id>/
  runner-status.json
  cycles.csv
  summary.md
```

It supports N/N success, cycle failure, crash, nonzero exit, incomplete exit-zero evidence, hang, orphan child, SIGTERM handling and SIGTERM-ignore/SIGKILL cleanup. Metrics are synthetic; process/evidence semantics are real.

## Quick scenario commands

Happy path:

```bash
python -m unittest \
  tests.test_orchestrator_happy_path.HappyPathIntegrationTests.test_writer_validator_independent_reviewer_reaches_done_with_exact_provenance -v
```

One `REQUEST_CHANGES`/rework generation:

```bash
python -m unittest \
  tests.test_orchestrator_rework.ReworkIntegrationTests.test_request_changes_flows_to_fresh_rework_generation_and_fresh_review -v
```

Writer completion across orchestrator absence/reopen:

```bash
python -m unittest \
  tests.test_writer_recovery.WriterRecoveryTests.test_separate_worker_finishes_after_store_closes_and_new_store_recovers_once -v
```

All twelve required deterministic scenarios:

```bash
python -m unittest tests.test_required_integration_scenarios -v
```

100-workflow reproducible chaos sweep:

```bash
python -m unittest tests.test_chaos -v
```

The chaos test uses a fixed recorded seed and never replaces deterministic scenario acceptance.

## CLI simulation configuration

`agent-relay run TASK_ID --simulation --config examples/config.simulated.yaml` requires:

- simulated writer and reviewer providers;
- exactly one validation step in the task;
- that step's configured runner marked `options.simulated_validator: true`;
- a real Git repository/baseline because worktree and commit behavior remains real.

The default simulated writer creates a marker file and commit. Default reviewer approves. Provider/runner `options.behavior` can override behavior for tests or experiments.
