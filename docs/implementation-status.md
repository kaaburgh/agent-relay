# Implementation Status

## Current checkpoint

Completed: `R00`, `R01`.

Next bounded unit: `R02 — SQLite schema, transactions, and append-only event store`.

## R01 evidence

Implemented:

- frozen typed task/config domain models in `agent_relay/models.py`;
- YAML/JSON configuration loader in `agent_relay/config.py`;
- provider config with model/reasoning/executable/options fields and no credential fields;
- local/SSH runner shapes and named resource capacities;
- task repository/baseline/writer/review/acceptance/validation/resource/correction/workspace fields;
- CLI routes for `task create`, `run`, `status`, `events`, `resume`, `cancel`, `doctor`;
- execution commands deliberately fail closed until durable execution exists;
- credential-free example task/global config;
- CI installs package dependencies before tests;
- durable-unit protocol now treats a successful checkpoint as a continuation point, not an automatic end of the overall agent run.

Acceptance command:

```bash
python3 -m unittest discover -s tests -v
```

Result during R01 implementation: PASS, 13 tests.

Environment note: local verification ran on Python 3.13.5 with PyYAML 6.0.3; repository/CI target remains Python 3.12 and GitHub CI installs the declared dependency.

## Current product state

Task/config parsing and CLI routing exist. Workflow execution, SQLite persistence, state machine, attempt artifacts, providers, subprocess supervision, leases, simulation, recovery and shadPS4 integration are not yet claimed.

## Durable decisions

- Python 3.12 / Linux-first.
- Deterministic orchestrator; models are bounded workers, never state-machine owners.
- Simulation-first; real Codex/Claude/shadPS4 integration follows deterministic integration/recovery behavior.
- SQLite remains the durable-store choice.
- YAML/JSON input is parsed with `yaml.safe_load`/`json.loads`; configuration models intentionally contain no secret/token fields.
- Runners explicitly reserve `local` and `ssh` kinds so remote validation can be added without changing task shape.
- Bloodborne-specific behavior remains outside orchestration core.
- No issue tracker is required for execution/development routing.

## Handoff protocol

At every roadmap checkpoint, record completed/in-progress unit, exact commands/results, important files/modules, material decisions, unresolved limitations and exact next `READY` unit. Preserve useful history and never mark an acceptance gate complete without evidence.
