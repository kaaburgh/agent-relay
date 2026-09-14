# State machine and invariants

Normal flow:

```text
READY -> WORK -> VALIDATE -> REVIEW -> DONE
                         REVIEW --REQUEST_CHANGES--> REWORK -> VALIDATE -> REVIEW
```

Additional durable states are `WAITING_PROVIDER`, `BLOCKED`, `FAILED`, and `CANCELLED`.

## Ownership

The deterministic orchestrator is the only component allowed to advance workflow state. Writer/reviewer/tool processes only produce commits, results and evidence. State changes and semantic events are persisted together.

## Candidate generations

Every committed writer/rework result becomes an explicit candidate generation. Example:

```text
generation 1 / SHA aaa / validation V1 / review R1 -> REQUEST_CHANGES
generation 2 / SHA bbb / validation V2 / review R2 -> APPROVE
```

Validation or review for `aaa` can never authorize `bbb`.

## Review gate

`REVIEW` requires a frozen candidate SHA and, when validation is mandatory, successful validation for the exact candidate generation/SHA. `DONE` requires valid structured reviewer output with `APPROVE` or `APPROVE_WITH_FOLLOWUPS` for that same candidate.

Malformed reviewer output never becomes approval. `REQUEST_CHANGES` requires valid findings and sends those findings into a fresh writer attempt. Re-review is a fresh reviewer invocation against a new detached candidate worktree.

## Correction limits

Rework rounds are bounded by `max_correction_rounds`. If another `REQUEST_CHANGES` would exceed the configured limit, the review remains durable history and the task transitions to `BLOCKED`; no extra candidate generation is created.

## Provider unavailability

Provider quota/rate-limit/temporary-unavailability is not corruption. The task moves to `WAITING_PROVIDER` with durable provider/reason/first-seen/last-attempt/next-retry/attempt-count metadata. Retry resumes the interrupted stage only when due; no busy-spin polling is used.

## Process outcomes

The process layer distinguishes:

- `SUCCEEDED` / ordinary nonzero `FAILED`;
- `TIMED_OUT` from an absolute stage deadline;
- `STALLED` from absent evidence progress;
- `CANCELLED` from explicit operator cancellation;
- provider-unavailable classification inside provider adapters.

A successful process exit is not sufficient evidence of workflow success. For example, external validation with exit zero but only 2/3 required cycles is incomplete and cannot pass.

## Core invariants

1. At most one writer owns a task at once.
2. Reviewer never writes to writer workspace and a reviewer worktree must remain unchanged.
3. Review cannot start without a frozen candidate SHA.
4. `DONE` cannot be reached without the configured review gate.
5. Mandatory validation must match the reviewed candidate generation/SHA.
6. `REQUEST_CHANGES` creates a new generation before fresh validation/review.
7. Historical attempts, validations and reviews remain auditable.
8. Retries allocate new attempt identity instead of overwriting prior artifacts.
9. Named resource occupancy never exceeds configured capacity.
10. Restart recovery cannot silently regress a completed durable stage.
11. Malformed provider output cannot be interpreted as success.
12. Ambiguous expensive-runtime ownership fails closed rather than creating overlap.

These invariants are exercised by `tests/test_required_integration_scenarios.py` and the seeded chaos runner.
