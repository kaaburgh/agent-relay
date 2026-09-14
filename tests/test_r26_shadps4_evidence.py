from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from agent_relay.artifacts import ArtifactManager
from agent_relay.shadps4_validator import (
    ShadPS4BloodborneValidator,
    ShadPS4ValidationResultKind,
)
from agent_relay.store import Store
from agent_relay.supervisor import SubprocessSupervisor


_HARNESS = r'''
import argparse
import csv
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--run-id", required=True)
parser.add_argument("--requested-cycles", required=True, type=int)
parser.add_argument("--evidence-dir", required=True)
args = parser.parse_args()

root = Path(args.evidence_dir)
root.mkdir(parents=True, exist_ok=True)
(root / "runner-status.json").write_text(json.dumps({
    "run_id": args.run_id,
    "requested_cycles": args.requested_cycles,
    "completed_cycles": args.requested_cycles,
    "state": "completed",
}), encoding="utf-8")

with (root / "cycles.csv").open("w", encoding="utf-8", newline="") as stream:
    writer = csv.DictWriter(stream, fieldnames=["cycle", "metric"])
    writer.writeheader()
    for cycle in range(1, args.requested_cycles + 1):
        writer.writerow({"cycle": cycle, "metric": cycle * 10})

(root / "summary.md").write_text("runner claims complete\n", encoding="utf-8")
'''


class R26ShadPS4EvidenceTests(unittest.TestCase):
    def test_cycle_records_without_success_status_never_validate(self) -> None:
        async def scenario() -> None:
            root = Path(tempfile.mkdtemp())
            store = Store(root / "state.sqlite3")
            try:
                store.create_task(
                    task_id="task-1",
                    repository=str(root),
                    baseline_ref="main",
                    task_spec={"repository": str(root)},
                )
                artifacts = ArtifactManager(root / "artifacts", store)
                validator = ShadPS4BloodborneValidator(
                    store=store,
                    artifacts=artifacts,
                    supervisor=SubprocessSupervisor(store),
                )
                harness = root / "missing_cycle_status_harness.py"
                harness.write_text(textwrap.dedent(_HARNESS), encoding="utf-8")
                result = await validator.run(
                    task_id="task-1",
                    cwd=root,
                    generation=1,
                    candidate_sha="c" * 40,
                    requested_cycles=3,
                    argv_template=[
                        sys.executable,
                        str(harness),
                        "--run-id",
                        "{run_id}",
                        "--requested-cycles",
                        "{requested_cycles}",
                        "--evidence-dir",
                        "{evidence_dir}",
                    ],
                )
                self.assertEqual(
                    result.kind,
                    ShadPS4ValidationResultKind.INCOMPLETE_EVIDENCE,
                )
                self.assertIn("missing", result.reason.lower())
            finally:
                store.close()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
