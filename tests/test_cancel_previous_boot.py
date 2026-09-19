from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_relay.operator import cancel_task
from agent_relay.store import Store, utc_now
from agent_relay.supervisor import SubprocessSupervisor


class PreviousBootCancellationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.store = Store(self.root / "state.sqlite3")
        self.store.create_task(
            task_id="task-old-boot",
            repository=str(self.root),
            baseline_ref="main",
            task_spec={"repository": str(self.root), "baseline": "main"},
        )
        # Installs the runtime-safety identity table/indexes without launching a process.
        SubprocessSupervisor(self.store)

    def tearDown(self) -> None:
        self.store.close()

    def test_cancel_treats_previous_boot_owner_as_dead_without_signalling(self) -> None:
        attempt = self.store.allocate_attempt(
            task_id="task-old-boot", kind="writer", command=["fake-writer"]
        )
        started = utc_now()
        with self.store._transaction():
            self.store._conn.execute(
                "UPDATE attempts SET status='RUNNING',pid=? WHERE attempt_id=?",
                (424242, attempt.attempt_id),
            )
            cursor = self.store._conn.execute(
                """
                INSERT INTO processes(
                    task_id,attempt_id,pid,process_group_id,state,command_json,
                    started_at,last_liveness_at
                ) VALUES (?,?,?,?, 'RUNNING', ?,?,?)
                """,
                (
                    "task-old-boot",
                    attempt.attempt_id,
                    424242,
                    424242,
                    '["fake-writer"]',
                    started,
                    started,
                ),
            )
            process_id = int(cursor.lastrowid)
            self.store._conn.execute(
                """
                INSERT INTO process_identities(
                    process_id,boot_id,start_time_ticks,process_group_id,session_id
                ) VALUES (?,?,?,?,?)
                """,
                (process_id, "definitely-a-previous-boot", 123, 424242, 424242),
            )

        with patch("agent_relay.operator.os.killpg") as killpg:
            result = cancel_task(self.store, "task-old-boot", grace_seconds=0.01)

        killpg.assert_not_called()
        self.assertEqual(result["stage"], "CANCELLED")
        self.assertFalse(result["terminated_processes"][0]["sigterm_sent"])
        self.assertFalse(result["terminated_processes"][0]["sigkill_sent"])
        row = self.store._conn.execute(
            "SELECT state FROM processes WHERE process_id=?", (process_id,)
        ).fetchone()
        self.assertEqual(row["state"], "CANCELLED")
        finalized = self.store.get_attempt(attempt.attempt_id)
        self.assertEqual(finalized.status, "CANCELLED")
        self.assertIsNotNone(finalized.ended_at)


if __name__ == "__main__":
    unittest.main()
