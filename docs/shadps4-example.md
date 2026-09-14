# shadPS4 / Bloodborne external validation

The real adapter is `agent_relay.shadps4_validator.ShadPS4BloodborneValidator`. It wraps an existing external harness; it does **not** copy Bloodborne menu/death/reload logic into agent-relay.

A known harness shape from the target environment is:

```text
/home/ubuntu/bb-shadPS4-correctness-instrumentation/tools/run_bb_death_reload_benchmark.py
```

That path is an example from one machine, not a repository default. Configure the actual executable/path externally.

## Contract

The adapter renders only these command-template placeholders:

- `{run_id}`
- `{requested_cycles}`
- `{evidence_dir}`

Unknown placeholders fail before a durable validation attempt is allocated.

The external harness should produce machine-readable evidence under the supplied evidence directory:

```text
evidence/<run-id>/
  runner-status.json
  cycles.csv       # or cycles.json
  summary.md
```

The adapter normalizes:

- run ID;
- requested/completed cycle counts;
- runner state;
- process exit/result;
- cycle records/statuses;
- summary/evidence paths;
- exact task attempt, candidate generation and candidate SHA.

## Success gate

Success requires all of the following:

1. managed process outcome is compatible with completion;
2. status run ID matches the invocation;
3. requested cycle count matches;
4. completed cycles are N/N;
5. exactly N cycle records exist;
6. cycle IDs, when present, are exactly ordered 1..N;
7. no cycle reports failure/unsupported status;
8. runner state reports completion/success;
9. `summary.md` exists.

Exit zero alone never passes. Missing, mismatched or out-of-order evidence is `INCOMPLETE_EVIDENCE`. Explicit runner/cycle failure is `VALIDATION_FAILED`. Crash, timeout or watchdog stall is a process failure unless complete deterministic failure evidence proves otherwise.

## Local example

An adapter caller can conceptually supply an argv template such as:

```text
python /configured/path/run_bb_death_reload_benchmark.py \
  --run-id {run_id} \
  --cycles {requested_cycles} \
  --evidence-dir {evidence_dir}
```

The exact flags belong to the installed harness contract; agent-relay does not assume them globally.

## Remote / SSH use

`SSHExternalToolRunner` can transport a command to another machine, but it does **not** copy evidence back. `ShadPS4BloodborneValidator` reads evidence locally after the process finishes. Therefore a remote harness can be connected safely only when one of these is true:

- the configured evidence path is on shared storage mounted/visible at the same logical location to the orchestrator and remote node; or
- an explicit artifact-transfer step copies the completed evidence into the local attempt/evidence directory before the adapter parses it.

The current SSH transport has no implicit SCP/rsync layer. Do not configure a remote path and assume the local validator can see it.

For a single GPU/Bloodborne instance, configure a capacity-1 named resource such as `bloodborne-runtime`. Resource leasing belongs to generic orchestration; Bloodborne-specific runtime behavior remains external.

## Acceptance

The adapter has dedicated real-subprocess tests for CSV/JSON success, provenance/artifacts, incomplete/mismatched/out-of-order evidence, explicit failure, crash/nonzero, timeout/stall and argv-template validation:

```bash
python -m unittest tests.test_shadps4_validator -v
```
