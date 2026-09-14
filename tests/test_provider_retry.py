from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_relay.provider_retry import (
    clear_provider_wait,
    get_provider_wait,
    record_provider_unavailable,
    resume_provider_if_due,
)
from agent_relay.store import Store
from agent_relay.workflow import WorkflowStage, WorkflowStateMachine


class ProviderRetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.store = Store(self.root / "state.sqlite3")
        self.store.create_task(
            task_id="task-1",
            repository=str(self.root),
            baseline_ref="main",
            task_spec={"repository": str(self.root)},
        )
        self.workflow = WorkflowStateMachine(self.store)
        self.workflow.transition("task-1", WorkflowStage.WORK)
        self.t0 = datetime(2026, 9, 14, 6, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.store.close()

    def test_wait_metadata_and_no_busy_spin_before_due_time(self) -> None:
        wait = record_provider_unavailable(
            self.store,
            task_id="task-1",
            provider="simulated-writer",
            reason="rate limit",
            now=self.t0,
            base_delay_seconds=10,
            max_delay_seconds=40,
        )
        self.assertEqual(self.store.get_task("task-1").stage, "WAITING_PROVIDER")
        self.assertEqual(wait.attempt_count, 1)
        self.assertEqual(wait.resume_stage, "WORK")
        self.assertEqual(datetime.fromisoformat(wait.next_retry), self.t0 + timedelta(seconds=10))
        before = len(self.store.events("task-1"))
        self.assertFalse(
            resume_provider_if_due(
                self.store, task_id="task-1", now=self.t0 + timedelta(seconds=9)
            )
        )
        self.assertEqual(len(self.store.events("task-1")), before)
        self.assertEqual(self.store.get_task("task-1").stage, "WAITING_PROVIDER")

    def test_due_retry_resumes_same_stage_and_backoff_survives_repeated_unavailability(self) -> None:
        first = record_provider_unavailable(
            self.store,
            task_id="task-1",
            provider="writer",
            reason="quota",
            now=self.t0,
            base_delay_seconds=10,
            max_delay_seconds=40,
        )
        self.assertTrue(
            resume_provider_if_due(
                self.store, task_id="task-1", now=self.t0 + timedelta(seconds=10)
            )
        )
        resumed = self.store.get_task("task-1")
        self.assertEqual(resumed.stage, "WORK")
        self.assertEqual(resumed.stage_attempt, 2)
        self.assertEqual(get_provider_wait(self.store, "task-1").attempt_count, 1)

        second_time = self.t0 + timedelta(seconds=11)
        second = record_provider_unavailable(
            self.store,
            task_id="task-1",
            provider="writer",
            reason="quota again",
            now=second_time,
            base_delay_seconds=10,
            max_delay_seconds=40,
        )
        self.assertEqual(second.attempt_count, 2)
        self.assertEqual(second.first_seen, first.first_seen)
        self.assertEqual(datetime.fromisoformat(second.next_retry), second_time + timedelta(seconds=20))
        self.assertTrue(
            resume_provider_if_due(
                self.store, task_id="task-1", now=second_time + timedelta(seconds=20)
            )
        )

        third_time = second_time + timedelta(seconds=21)
        third = record_provider_unavailable(
            self.store,
            task_id="task-1",
            provider="writer",
            reason="quota third",
            now=third_time,
            base_delay_seconds=10,
            max_delay_seconds=40,
        )
        self.assertEqual(third.attempt_count, 3)
        self.assertEqual(datetime.fromisoformat(third.next_retry), third_time + timedelta(seconds=40))
        self.assertTrue(
            resume_provider_if_due(
                self.store, task_id="task-1", now=third_time + timedelta(seconds=40)
            )
        )
        fourth_time = third_time + timedelta(seconds=41)
        fourth = record_provider_unavailable(
            self.store,
            task_id="task-1",
            provider="writer",
            reason="quota fourth",
            now=fourth_time,
            base_delay_seconds=10,
            max_delay_seconds=40,
        )
        self.assertEqual(fourth.attempt_count, 4)
        self.assertEqual(datetime.fromisoformat(fourth.next_retry), fourth_time + timedelta(seconds=40))

    def test_wait_survives_reopen_and_is_cleared_only_after_provider_success(self) -> None:
        record_provider_unavailable(
            self.store,
            task_id="task-1",
            provider="writer",
            reason="temporary unavailable",
            now=self.t0,
            base_delay_seconds=5,
            max_delay_seconds=20,
        )
        self.store.close()
        self.store = Store(self.root / "state.sqlite3")
        wait = get_provider_wait(self.store, "task-1")
        self.assertIsNotNone(wait)
        self.assertEqual(wait.attempt_count, 1)
        self.assertTrue(
            resume_provider_if_due(
                self.store, task_id="task-1", now=self.t0 + timedelta(seconds=5)
            )
        )
        self.assertIsNotNone(get_provider_wait(self.store, "task-1"))
        self.assertTrue(
            clear_provider_wait(
                self.store,
                task_id="task-1",
                provider="writer",
                now=self.t0 + timedelta(seconds=6),
            )
        )
        self.assertIsNone(get_provider_wait(self.store, "task-1"))
        events = [event.event_type for event in self.store.events("task-1")]
        self.assertIn("provider_unavailable", events)
        self.assertIn("retry_scheduled", events)
        self.assertIn("provider_retry_started", events)
        self.assertEqual(events[-1], "provider_available")


if __name__ == "__main__":
    unittest.main()
