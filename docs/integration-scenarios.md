# Deterministic integration scenario matrix

`tests/test_required_integration_scenarios.py` is the executable acceptance matrix for the twelve scenarios required by `docs/spec.md`. Each method is named `test_sNN_...` to keep the product contract visible in CI rather than relying on incidental lower-level coverage.

| # | Required scenario | Executable acceptance |
|---|---|---|
| 1 | Happy writer -> validation -> approval -> DONE | `test_s01_happy_path_writer_validate_review_done` |
| 2 | REQUEST_CHANGES -> rework -> fresh validation/review -> DONE | `test_s02_review_correction_fresh_rework_and_review` |
| 3 | Orchestrator absent while writer finishes; no duplicate writer | `test_s03_orchestrator_absent_while_writer_finishes_no_duplicate` |
| 4 | Orchestrator absent during external validation; no duplicate runtime | `test_s04_orchestrator_absent_during_external_validation_no_duplicate` |
| 5 | Real provider subprocess unavailable -> WAITING_PROVIDER -> retry -> resumed writer | `test_s05_real_provider_unavailable_wait_retry_then_writer_resumes` |
| 6 | Malformed reviewer output never approves | `test_s06_malformed_reviewer_never_approves` |
| 7 | Exit-zero incomplete validation evidence never passes | `test_s07_exit_zero_incomplete_validation_never_passes` |
| 8 | Hanging tool is detected/cleaned and partial evidence remains | `test_s08_hanging_tool_watchdog_cleans_group_and_preserves_evidence` |
| 9 | Two real fake runtimes contend for capacity-1 lease and never overlap | `test_s09_two_real_fake_runtimes_obey_capacity_one_lease` |
| 10 | At least two correction generations before fresh approval; history retained | `test_s10_two_review_rework_rounds_before_approval_keep_history` |
| 11 | Always-request-changes reaches configured correction limit and BLOCKED | `test_s11_correction_limit_blocks_instead_of_looping` |
| 12 | Worker completes while supervisor is absent and checkpoint is reused | `test_s12_worker_completes_while_supervisor_absent_checkpoint_is_reused` |

The acceptance class deliberately re-executes the established real Git/SQLite/subprocess integration cases for scenarios already covered elsewhere. Scenarios 5 and 9 contain additional integration logic because their earlier coverage was only at primitive state/lease level.

Run the whole repository suite with:

```bash
python -m unittest discover -s tests -v
```
