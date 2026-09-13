from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent_relay.artifacts import ArtifactError, ArtifactManager
from agent_relay.store import Store, StoreError


class ArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.store = Store(self.root / "state.sqlite3")
        self.store.create_task(
            task_id="task-1",
            repository="/tmp/repo",
            baseline_ref="main",
            task_spec={"repository": "/tmp/repo"},
        )
        self.manager = ArtifactManager(self.root, self.store)

    def tearDown(self) -> None:
        self.store.close()

    def test_retries_allocate_new_attempt_ids_and_directories(self) -> None:
        first = self.manager.create_attempt(task_id="task-1", kind="writer")
        second = self.manager.create_attempt(task_id="task-1", kind="writer")
        self.assertNotEqual(first.attempt.attempt_id, second.attempt.attempt_id)
        self.assertEqual(first.attempt.attempt_no, 1)
        self.assertEqual(second.attempt.attempt_no, 2)
        self.assertEqual(first.directory.name, "writer-001")
        self.assertEqual(second.directory.name, "writer-002")
        self.assertTrue(first.directory.is_dir())
        self.assertTrue(second.directory.is_dir())
        self.assertEqual(len(self.store.attempts("task-1", "writer")), 2)

    def test_attempt_layout_preserves_inputs_command_logs_and_candidate_provenance(self) -> None:
        sha = "a" * 40
        layout = self.manager.create_attempt(
            task_id="task-1",
            kind="review",
            generation=2,
            candidate_sha=sha,
            inputs={"baseline": "main", "candidate": sha},
            command=["reviewer", "--model", "opus"],
            config={"provider": "simulated"},
        )
        self.assertEqual(json.loads(layout.metadata_path.read_text())["candidate_sha"], sha)
        self.assertEqual(json.loads(layout.metadata_path.read_text())["generation"], 2)
        self.assertEqual(json.loads(layout.inputs_path.read_text())["candidate"], sha)
        self.assertEqual(json.loads(layout.command_path.read_text())[0], "reviewer")
        self.assertEqual(json.loads(layout.config_path.read_text())["provider"], "simulated")
        self.assertEqual(layout.stdout_path.read_text(), "")
        self.assertEqual(layout.stderr_path.read_text(), "")

    def test_snapshots_redact_nested_and_command_line_secrets(self) -> None:
        layout = self.manager.create_attempt(
            task_id="task-1",
            kind="writer",
            inputs={"safe": "visible", "password": "dont-log-me"},
            command=[
                "writer",
                "--token",
                "secret-token",
                "--api-key=another-secret",
                "Authorization=Bearer deadbeef",
            ],
            config={
                "provider": "simulated",
                "env": {"OPENAI_API_KEY": "sk-secret", "NORMAL": "visible"},
                "header": "Bearer abc.def.ghi",
            },
        )
        combined = "\n".join(
            path.read_text()
            for path in (layout.inputs_path, layout.command_path, layout.config_path)
        )
        for secret in ("dont-log-me", "secret-token", "another-secret", "sk-secret", "deadbeef", "abc.def.ghi"):
            self.assertNotIn(secret, combined)
        self.assertIn("visible", combined)
        self.assertIn("<redacted>", combined)

    def test_result_is_written_once_and_attempt_cannot_be_refinalized(self) -> None:
        layout = self.manager.create_attempt(task_id="task-1", kind="validation")
        finished = self.manager.finalize_attempt(
            layout,
            status="SUCCESS",
            result={"ok": True, "token": "never-store"},
            exit_status=0,
        )
        self.assertEqual(finished.status, "SUCCESS")
        self.assertEqual(finished.exit_status, 0)
        self.assertEqual(json.loads(layout.result_path.read_text())["token"], "<redacted>")
        with self.assertRaises(FileExistsError):
            self.manager.finalize_attempt(layout, status="SUCCESS", result={"ok": True})
        self.assertEqual(self.store.get_attempt(layout.attempt.attempt_id).result["token"], "<redacted>")

    def test_attempt_identity_and_artifact_rows_are_immutable_at_database_layer(self) -> None:
        layout = self.manager.create_attempt(task_id="task-1", kind="writer")
        attempt_id = layout.attempt.attempt_id
        with self.assertRaisesRegex(sqlite3.IntegrityError, "attempt identity is immutable"):
            self.store._conn.execute(
                "UPDATE attempts SET attempt_no=99 WHERE attempt_id=?", (attempt_id,)
            )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "attempt history is immutable"):
            self.store._conn.execute("DELETE FROM attempts WHERE attempt_id=?", (attempt_id,))
        artifact_id = self.store._conn.execute(
            "SELECT artifact_id FROM artifacts WHERE attempt_id=? ORDER BY artifact_id LIMIT 1",
            (attempt_id,),
        ).fetchone()[0]
        with self.assertRaisesRegex(sqlite3.IntegrityError, "artifacts are append-only"):
            self.store._conn.execute(
                "UPDATE artifacts SET path='other' WHERE artifact_id=?", (artifact_id,)
            )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "artifacts are append-only"):
            self.store._conn.execute("DELETE FROM artifacts WHERE artifact_id=?", (artifact_id,))

    def test_finalize_store_api_rejects_second_result_even_without_filesystem(self) -> None:
        attempt = self.store.allocate_attempt(task_id="task-1", kind="writer", command=["fake"])
        self.store.finish_attempt(attempt_id=attempt.attempt_id, status="SUCCESS", result={"ok": True})
        with self.assertRaisesRegex(StoreError, "already finalized"):
            self.store.finish_attempt(attempt_id=attempt.attempt_id, status="SUCCESS", result={"ok": True})

    def test_path_segments_fail_closed(self) -> None:
        with self.assertRaises(ArtifactError):
            self.manager.create_attempt(task_id="../task-1", kind="writer")
        with self.assertRaises(ArtifactError):
            self.manager.create_attempt(task_id="task-1", kind="../writer")
        self.assertEqual(self.store.attempts("task-1"), ())


if __name__ == "__main__":
    unittest.main()
