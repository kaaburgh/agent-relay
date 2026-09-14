from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_relay.resource_leases import (
    LeaseUnavailable,
    acquire_lease,
    active_leases,
    configure_resource,
    heartbeat_lease,
    reclaim_stale_leases,
    release_lease,
)
from agent_relay.store import Store
from agent_relay.supervisor import SubprocessSupervisor


class ResourceLeaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.db = self.root / "state.sqlite3"
        self.store = Store(self.db)
        for task_id in ("task-1", "task-2"):
            self.store.create_task(
                task_id=task_id,
                repository=str(self.root),
                baseline_ref="main",
                task_spec={"repository": str(self.root)},
            )
        self.attempt1 = self.store.allocate_attempt(task_id="task-1", kind="validation", generation=1)
        self.attempt2 = self.store.allocate_attempt(task_id="task-2", kind="validation", generation=1)
        self.t0 = datetime(2026, 9, 14, 6, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.store.close()

    def test_capacity_one_prevents_overlap_and_release_unblocks_next_holder(self) -> None:
        configure_resource(self.store, "bb_runtime", 1)
        first = acquire_lease(
            self.store,
            resource_name="bb_runtime",
            holder_id="holder-1",
            task_id="task-1",
            attempt_id=self.attempt1.attempt_id,
            now=self.t0,
        )
        with self.assertRaises(LeaseUnavailable):
            acquire_lease(
                self.store,
                resource_name="bb_runtime",
                holder_id="holder-2",
                task_id="task-2",
                attempt_id=self.attempt2.attempt_id,
                now=self.t0,
            )
        release_lease(
            self.store,
            lease_id=first.lease_id,
            holder_id=first.holder_id,
            now=self.t0 + timedelta(seconds=1),
        )
        second = acquire_lease(
            self.store,
            resource_name="bb_runtime",
            holder_id="holder-2",
            task_id="task-2",
            attempt_id=self.attempt2.attempt_id,
            now=self.t0 + timedelta(seconds=2),
        )
        self.assertEqual(second.task_id, "task-2")
        self.assertEqual(len(active_leases(self.store, "bb_runtime")), 1)

    def test_different_resources_do_not_serialize_unrelated_work(self) -> None:
        configure_resource(self.store, "runtime-a", 1)
        configure_resource(self.store, "runtime-b", 1)
        one = acquire_lease(
            self.store, resource_name="runtime-a", holder_id="a1", task_id="task-1",
            attempt_id=self.attempt1.attempt_id, now=self.t0,
        )
        two = acquire_lease(
            self.store, resource_name="runtime-b", holder_id="b1", task_id="task-2",
            attempt_id=self.attempt2.attempt_id, now=self.t0,
        )
        self.assertNotEqual(one.resource_name, two.resource_name)
        self.assertEqual(len(active_leases(self.store)), 2)

    def test_capacity_two_allows_two_and_rejects_third(self) -> None:
        self.store.create_task(
            task_id="task-3", repository=str(self.root), baseline_ref="main",
            task_spec={"repository": str(self.root)},
        )
        attempt3 = self.store.allocate_attempt(task_id="task-3", kind="validation", generation=1)
        configure_resource(self.store, "gpu", 2)
        acquire_lease(self.store, resource_name="gpu", holder_id="h1", task_id="task-1", attempt_id=self.attempt1.attempt_id)
        acquire_lease(self.store, resource_name="gpu", holder_id="h2", task_id="task-2", attempt_id=self.attempt2.attempt_id)
        with self.assertRaises(LeaseUnavailable):
            acquire_lease(self.store, resource_name="gpu", holder_id="h3", task_id="task-3", attempt_id=attempt3.attempt_id)

    def test_active_lease_survives_database_reopen_and_heartbeat_does_not_spam_events(self) -> None:
        configure_resource(self.store, "runtime", 1)
        lease = acquire_lease(
            self.store, resource_name="runtime", holder_id="durable-holder", task_id="task-1",
            attempt_id=self.attempt1.attempt_id, now=self.t0,
        )
        before = len(self.store.events("task-1"))
        heartbeat_lease(
            self.store,
            lease_id=lease.lease_id,
            holder_id=lease.holder_id,
            now=self.t0 + timedelta(seconds=5),
        )
        self.assertEqual(len(self.store.events("task-1")), before)
        self.store.close()
        self.store = Store(self.db)
        recovered = active_leases(self.store, "runtime")
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0].holder_id, "durable-holder")

    def test_stale_recovery_will_not_reclaim_lease_with_real_running_process(self) -> None:
        async def scenario() -> None:
            configure_resource(self.store, "runtime", 1)
            supervisor = SubprocessSupervisor(self.store)
            stdout = self.root / "live.stdout"
            stderr = self.root / "live.stderr"
            handle = await supervisor.start(
                task_id="task-1",
                attempt_id=self.attempt1.attempt_id,
                argv=[sys.executable, "-c", "import time; time.sleep(30)"],
                cwd=self.root,
                stdout_path=stdout,
                stderr_path=stderr,
                heartbeat_interval=0.05,
                terminate_grace_seconds=0.1,
            )
            lease = acquire_lease(
                self.store,
                resource_name="runtime",
                holder_id="running-holder",
                task_id="task-1",
                attempt_id=self.attempt1.attempt_id,
                now=self.t0,
            )
            not_reclaimed = reclaim_stale_leases(
                self.store,
                stale_after=timedelta(seconds=10),
                now=self.t0 + timedelta(hours=1),
            )
            self.assertEqual(not_reclaimed, ())
            self.assertEqual(len(active_leases(self.store, "runtime")), 1)
            await handle.terminate()
            reclaimed = reclaim_stale_leases(
                self.store,
                stale_after=timedelta(seconds=10),
                now=self.t0 + timedelta(hours=1, seconds=1),
            )
            self.assertEqual(len(reclaimed), 1)
            self.assertEqual(reclaimed[0].lease_id, lease.lease_id)
            self.assertEqual(active_leases(self.store, "runtime"), ())
            events = [event.event_type for event in self.store.events("task-1")]
            self.assertIn("resource_acquired", events)
            self.assertEqual(events[-1], "resource_released")
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
