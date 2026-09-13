from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent_relay.store import SCHEMA_VERSION, Store


EXPECTED_TABLES = {
    "tasks",
    "attempts",
    "candidate_generations",
    "processes",
    "artifacts",
    "provider_waits",
    "validations",
    "reviews",
    "resources",
    "leases",
    "events",
}


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp())
        self.path = self.directory / "state.sqlite3"

    def _create_task(self, store: Store, task_id: str = "task-1") -> None:
        store.create_task(
            task_id=task_id,
            repository="/tmp/repo",
            baseline_ref="main",
            task_spec={"repository": "/tmp/repo", "baseline": "main"},
        )

    def test_schema_is_deterministic_and_versioned(self) -> None:
        with Store(self.path) as store:
            self.assertEqual(store.schema_version, SCHEMA_VERSION)
            tables = {
                row[0]
                for row in store._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            self.assertTrue(EXPECTED_TABLES.issubset(tables))
        with Store(self.path) as reopened:
            self.assertEqual(reopened.schema_version, SCHEMA_VERSION)

    def test_create_task_atomically_appends_created_event(self) -> None:
        with Store(self.path) as store:
            task = store.create_task(
                task_id="task-1",
                repository="/tmp/repo",
                baseline_ref="main",
                task_spec={"x": 1},
            )
            self.assertEqual(task.stage, "READY")
            events = store.events("task-1")
            self.assertEqual([event.event_type for event in events], ["task_created"])
            self.assertEqual(events[0].stage, "READY")

    def test_state_and_event_update_commit_together(self) -> None:
        with Store(self.path) as store:
            self._create_task(store)
            updated = store.update_task_with_event(
                task_id="task-1",
                stage="WORK",
                stage_attempt=1,
                event_type="stage_started",
                event_payload={"kind": "writer"},
            )
            self.assertEqual(updated.stage, "WORK")
            self.assertEqual([e.event_type for e in store.events("task-1")], ["task_created", "stage_started"])

    def test_failed_event_insert_rolls_back_task_update(self) -> None:
        with Store(self.path) as store:
            self._create_task(store)
            with self.assertRaises(sqlite3.IntegrityError):
                store.update_task_with_event(
                    task_id="task-1",
                    stage="WORK",
                    stage_attempt=1,
                    event_type=None,
                )
            task = store.get_task("task-1")
            self.assertEqual(task.stage, "READY")
            self.assertEqual(task.stage_attempt, 0)
            self.assertEqual([e.event_type for e in store.events("task-1")], ["task_created"])

    def test_compare_and_set_rejects_stale_transition_snapshot(self) -> None:
        with Store(self.path) as store:
            self._create_task(store)
            store.update_task_with_event(
                task_id="task-1",
                stage="WORK",
                stage_attempt=1,
                event_type="stage_started",
            )
            with self.assertRaisesRegex(Exception, "stale task state"):
                store.update_task_with_event(
                    task_id="task-1",
                    stage="VALIDATE",
                    stage_attempt=1,
                    event_type="validation_started",
                    expected_stage="READY",
                    expected_candidate_sha=None,
                    expected_generation=0,
                    enforce_expected_candidate=True,
                )
            self.assertEqual(store.get_task("task-1").stage, "WORK")
            self.assertEqual(len(store.events("task-1")), 2)

    def test_events_are_append_only_even_for_direct_sql(self) -> None:
        with Store(self.path) as store:
            self._create_task(store)
            sequence = store.events("task-1")[0].sequence
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                store._conn.execute("UPDATE events SET event_type='changed' WHERE sequence=?", (sequence,))
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                store._conn.execute("DELETE FROM events WHERE sequence=?", (sequence,))

    def test_reopen_preserves_state_and_event_history(self) -> None:
        with Store(self.path) as store:
            self._create_task(store)
            store.update_task_with_event(
                task_id="task-1",
                stage="WORK",
                stage_attempt=1,
                event_type="stage_started",
            )
        with Store(self.path) as reopened:
            self.assertEqual(reopened.get_task("task-1").stage, "WORK")
            self.assertEqual(
                [event.event_type for event in reopened.events("task-1")],
                ["task_created", "stage_started"],
            )

    def test_failed_migration_rolls_back_schema_and_version(self) -> None:
        conn = sqlite3.connect(self.path)
        conn.execute("CREATE VIEW events AS SELECT 1 AS x")
        conn.commit()
        conn.close()
        with self.assertRaises(sqlite3.OperationalError):
            Store(self.path)
        check = sqlite3.connect(self.path)
        try:
            self.assertEqual(check.execute("PRAGMA user_version").fetchone()[0], 0)
            self.assertIsNone(
                check.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'"
                ).fetchone()
            )
        finally:
            check.close()

    def test_unknown_newer_schema_fails_closed(self) -> None:
        conn = sqlite3.connect(self.path)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        conn.close()
        with self.assertRaisesRegex(Exception, "newer than supported"):
            Store(self.path)


if __name__ == "__main__":
    unittest.main()
