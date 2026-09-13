# Contributing

`agent-relay` is developed as a sequence of bounded, durable roadmap passes.

Before making changes, read `AGENTS.md`, `docs/spec.md`, `ROADMAP.md`, and `docs/implementation-status.md`.

## Setup

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

## Checks

```bash
python -m unittest discover -s tests -v
```

## Change discipline

- Keep one implementation pass focused on one roadmap unit unless explicitly directed otherwise.
- Preserve the product contract in `docs/spec.md`.
- Add/update tests whenever behavior changes.
- Prefer real temporary subprocess/Git/SQLite integration tests for semantics that depend on them.
- Update `docs/implementation-status.md` with commands/results and the next bounded unit.
- Never commit credentials or provider authentication material.
- Do not automatically hard-reset or clean user-owned worktrees.

GitHub issues/PRs may be used for collaboration, but they are not the workflow state store or required development queue.
