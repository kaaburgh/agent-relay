from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FinalReviewDocumentationTests(unittest.TestCase):
    def test_final_review_covers_dod_boundaries_and_real_connection_steps(self) -> None:
        review_path = ROOT / "docs" / "final-review.md"
        self.assertTrue(review_path.exists())
        text = review_path.read_text(encoding="utf-8")

        required_markers = (
            "READY -> WORK -> VALIDATE -> REVIEW -> DONE",
            "CodexWriterProvider",
            "ClaudeReviewerProvider",
            "ShadPS4BloodborneValidator",
            "SSHExternalToolRunner",
            "bloodborne-runtime",
            "cycles.csv",
            "cycles.json",
            "WAITING_PROVIDER",
            "agent-relay doctor",
            "shared storage",
            "100 workflows",
            "20260914",
            "ordinary top-level `agent-relay run` remains intentionally fail-closed",
            "there is no integrated real Codex -> real validation -> real Claude composition",
        )
        for marker in required_markers:
            with self.subTest(marker=marker):
                self.assertIn(marker, text)

        required_demonstrations = (
            "test_s01_happy_path_writer_validate_review_done",
            "test_s02_review_correction_fresh_rework_and_review",
            "test_s03_orchestrator_absent_while_writer_finishes_no_duplicate",
            "test_s04_orchestrator_absent_during_external_validation_no_duplicate",
            "test_s05_real_provider_unavailable_wait_retry_then_writer_resumes",
            "test_s06_malformed_reviewer_never_approves",
            "test_s07_exit_zero_incomplete_validation_never_passes",
            "test_s08_hanging_tool_watchdog_cleans_group_and_preserves_evidence",
            "test_s09_two_real_fake_runtimes_obey_capacity_one_lease",
            "test_s10_two_review_rework_rounds_before_approval_keep_history",
            "test_s11_correction_limit_blocks_instead_of_looping",
            "test_s12_worker_completes_while_supervisor_absent_checkpoint_is_reused",
            "test_reproducible_100_workflow_chaos_sweep",
        )
        for name in required_demonstrations:
            with self.subTest(demonstration=name):
                self.assertIn(name, text)

    def test_readme_links_final_review_and_runtime_code_stays_fail_closed(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("[Final R26 review](docs/final-review.md)", readme)

        execution = (ROOT / "agent_relay" / "execution.py").read_text(encoding="utf-8")
        self.assertIn('"action": "backend-not-installed"', execution)
        self.assertIn("use --simulation", execution)


if __name__ == "__main__":
    unittest.main()
