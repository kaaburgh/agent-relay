from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from agent_relay.chaos import CHAOS_MODES, run_chaos


class ChaosIntegrationTests(unittest.TestCase):
    def test_reproducible_100_workflow_chaos_sweep(self) -> None:
        root = Path(tempfile.mkdtemp())
        seed = 20260914
        summary = asyncio.run(run_chaos(root, workflows=100, seed=seed))

        self.assertEqual(summary.seed, seed)
        self.assertEqual(summary.workflows, 100)
        self.assertEqual(sum(summary.mode_counts.values()), 100)
        self.assertEqual(set(summary.mode_counts), set(CHAOS_MODES))
        self.assertTrue(all(count > 0 for count in summary.mode_counts.values()))

        report = json.loads(summary.report_path.read_text(encoding="utf-8"))
        self.assertEqual(report["seed"], seed)
        self.assertEqual(report["requested_workflows"], 100)
        self.assertEqual(report["completed_workflows"], 100)
        self.assertTrue(report["completed"])
        self.assertNotIn("failure", report)
        self.assertEqual(len(report["runs"]), 100)
        self.assertEqual(report["mode_counts"], summary.mode_counts)


if __name__ == "__main__":
    unittest.main()
