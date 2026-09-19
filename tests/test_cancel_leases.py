from __future__ import annotations

import asyncio
import stat
import sys
import tempfile
import unittest
from pathlib import Path

from agent_relay.models import RunnerConfig
from agent_relay.operator import cancel_task
from agent_relay.resource_leases import acquire_lease, active_leases, configure_resource
from agent_relay.ssh_runner import SSHExternalToolRunner
from agent_relay.store import Store
from agent_relay.supervisor import SubprocessSupervisor


class CancelLeaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.store = Store(self.root / "state.sqlite3")
        configure_resource(self.store, "runtime", 1)

    def tearDown(self) -> None:
        self.store.close()

    def _create_task_attempt(self, task_id: str):
        self.store.create_task(
            task_id=task_id,
            repository=str(self.root),
            baseline_ref="main",
            task_spec={"repository": str(self.root), "baseline": "main"},
        )
        attempt = self.store.allocate_attempt(
            task_id=task_id,
            kind="validation",
            generation=1,
            command=["test-validator"],
        )
        lease = acquire_lease(
            self.store,
            resource_name="runtime",
            holder_id=f"holder-{task_id}",
            task_id=task_id,
            attempt_id=attempt.attempt_id,
        )
        return attempt, lease

    def test_cancel_releases_attempt_lease_after_proven_local_process_cleanup(self) -> None:
        async def scenario() -> None:
            attempt, lease = self._create_task_attempt("task-local")
            managed = await SubprocessSupervisor(self.store).start(
                task_id="task-local",
                attempt_id=attempt.attempt_id,
                argv=[sys.executable, "-c", "import time; time.sleep(60)"],
                cwd=self.root,
                stdout_path=self.root / "local.out",
                stderr_path=self.root / "local.err",
                timeout_seconds=10,
                terminate_grace_seconds=0.1,
            )

            result = cancel_task(self.store, "task-local", grace_seconds=0.1)
            self.assertEqual(result["stage"], "CANCELLED")
            self.assertEqual(
                result["released_leases"],
                [{
                    "lease_id": lease.lease_id,
                    "resource": "runtime",
                    "attempt_id": attempt.attempt_id,
                    "reason": "local-process-terminated",
                }],
            )
            self.assertEqual(result["retained_leases"], [])
            self.assertEqual(active_leases(self.store, "runtime"), ())
            await managed.wait()

            self.store.create_task(
                task_id="task-next",
                repository=str(self.root),
                baseline_ref="main",
                task_spec={"repository": str(self.root), "baseline": "main"},
            )
            next_attempt = self.store.allocate_attempt(
                task_id="task-next", kind="validation", generation=1
            )
            next_lease = acquire_lease(
                self.store,
                resource_name="runtime",
                holder_id="holder-next",
                task_id="task-next",
                attempt_id=next_attempt.attempt_id,
            )
            self.assertIsNone(next_lease.released_at)

        asyncio.run(scenario())

    def test_cancel_retains_default_ssh_attempt_lease_and_reports_reason(self) -> None:
        async def scenario() -> None:
            attempt, lease = self._create_task_attempt("task-remote")
            fake_ssh = self.root / "ssh"
            fake_ssh.write_text(
                "#!/usr/bin/env python3\nimport sys,time\nsys.stdin.read()\ntime.sleep(60)\n",
                encoding="utf-8",
            )
            fake_ssh.chmod(fake_ssh.stat().st_mode | stat.S_IXUSR)
            runner = SSHExternalToolRunner(
                supervisor=SubprocessSupervisor(self.store),
                config=RunnerConfig(kind="ssh", executable=str(fake_ssh), host="example.invalid"),
            )
            managed = await runner.start(
                task_id="task-remote",
                attempt_id=attempt.attempt_id,
                argv=["python", "remote-tool.py"],
                cwd="/tmp",
                stdout_path=self.root / "remote.out",
                stderr_path=self.root / "remote.err",
                timeout_seconds=10,
                terminate_grace_seconds=0.1,
            )

            result = cancel_task(self.store, "task-remote", grace_seconds=0.1)
            self.assertEqual(result["released_leases"], [])
            self.assertEqual(result["retained_leases"][0]["lease_id"], lease.lease_id)
            self.assertEqual(result["retained_leases"][0]["reason"], "remote-ssh-transport")
            remaining = active_leases(self.store, "runtime")
            self.assertEqual(len(remaining), 1)
            self.assertEqual(remaining[0].lease_id, lease.lease_id)
            await managed.wait()

        asyncio.run(scenario())

    def test_cancel_retains_custom_named_ssh_transport_lease(self) -> None:
        async def scenario() -> None:
            attempt, lease = self._create_task_attempt("task-custom-remote")
            custom_transport = self.root / "company-remote-wrapper"
            custom_transport.write_text(
                "#!/usr/bin/env python3\nimport sys,time\nsys.stdin.read()\ntime.sleep(60)\n",
                encoding="utf-8",
            )
            custom_transport.chmod(custom_transport.stat().st_mode | stat.S_IXUSR)
            runner = SSHExternalToolRunner(
                supervisor=SubprocessSupervisor(self.store),
                config=RunnerConfig(
                    kind="ssh",
                    executable=str(custom_transport),
                    host="example.invalid",
                ),
            )
            managed = await runner.start(
                task_id="task-custom-remote",
                attempt_id=attempt.attempt_id,
                argv=["python", "remote-tool.py"],
                cwd="/tmp",
                stdout_path=self.root / "custom-remote.out",
                stderr_path=self.root / "custom-remote.err",
                timeout_seconds=10,
                terminate_grace_seconds=0.1,
            )

            result = cancel_task(self.store, "task-custom-remote", grace_seconds=0.1)
            self.assertEqual(result["released_leases"], [])
            self.assertEqual(result["retained_leases"][0]["lease_id"], lease.lease_id)
            self.assertEqual(result["retained_leases"][0]["reason"], "remote-ssh-transport")
            self.assertEqual(active_leases(self.store, "runtime")[0].lease_id, lease.lease_id)
            await managed.wait()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
