from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_relay.artifacts import ArtifactManager
from agent_relay.git_workspace import record_candidate_generation
from agent_relay.resource_leases import acquire_lease, active_leases, configure_resource
from agent_relay.store import Store
from agent_relay.validator_recovery import reconcile_validation_attempt


class R26ValidatorRecoveryJsonTests(unittest.TestCase):
    def test_completed_json_validation_evidence_recovers_without_rerun(self) -> None:
        root = Path(tempfile.mkdtemp())
        store = Store(root / "state.sqlite3")
        try:
            store.create_task(
                task_id="task-1",
                repository=str(root),
                baseline_ref="main",
                task_spec={"repository": str(root)},
            )
            writer = store.allocate_attempt(task_id="task-1", kind="writer")
            candidate_sha = "d" * 40
            record_candidate_generation(
                store,
                task_id="task-1",
                candidate_sha=candidate_sha,
                writer_attempt_id=writer.attempt_id,
                expected_previous_generation=0,
            )

            artifact_root = root / "artifacts"
            artifacts = ArtifactManager(artifact_root, store)
            layout = artifacts.create_attempt(
                task_id="task-1",
                kind="validation",
                generation=1,
                candidate_sha=candidate_sha,
                inputs={"requested_cycles": 3},
                command=["detached-real-validator"],
            )
            run_id = "json-recovery-run"
            evidence_dir = layout.directory / "evidence" / run_id
            evidence_dir.mkdir(parents=True)
            (evidence_dir / "runner-status.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "requested_cycles": 3,
                        "completed_cycles": 3,
                        "state": "completed",
                    }
                ),
                encoding="utf-8",
            )
            (evidence_dir / "cycles.json").write_text(
                json.dumps(
                    {
                        "cycles": [
                            {"cycle": 1, "status": "ok"},
                            {"cycle": 2, "status": "ok"},
                            {"cycle": 3, "status": "ok"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (evidence_dir / "summary.md").write_text("completed 3/3\n", encoding="utf-8")

            configure_resource(store, "bloodborne-runtime", 1)
            lease = acquire_lease(
                store,
                resource_name="bloodborne-runtime",
                holder_id=f"validation-{layout.attempt.attempt_id}",
                task_id="task-1",
                attempt_id=layout.attempt.attempt_id,
            )

            recovered = reconcile_validation_attempt(
                store,
                artifact_root=artifact_root,
                task_id="task-1",
                attempt_id=layout.attempt.attempt_id,
                generation=1,
                candidate_sha=candidate_sha,
                run_id=run_id,
                requested_cycles=3,
                evidence_dir=evidence_dir,
                lease=lease,
            )

            self.assertEqual(recovered.action, "RECOVERED")
            self.assertIsNotNone(recovered.validation)
            self.assertEqual(recovered.validation.candidate_sha, candidate_sha)
            self.assertEqual(recovered.validation.result["completed_cycles"], 3)
            self.assertEqual(active_leases(store, "bloodborne-runtime"), ())
            self.assertEqual(len(store.attempts("task-1", "validation")), 1)
            cycles_artifact = store._conn.execute(
                "SELECT path FROM artifacts WHERE attempt_id=? AND kind='cycles'",
                (layout.attempt.attempt_id,),
            ).fetchone()
            self.assertIsNotNone(cycles_artifact)
            self.assertTrue(cycles_artifact["path"].endswith("cycles.json"))
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
