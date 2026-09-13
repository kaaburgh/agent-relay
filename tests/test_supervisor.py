from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

from agent_relay.artifacts import ArtifactManager
from agent_relay.store import Store
from agent_relay.supervisor import SubprocessSupervisor


class SupervisorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.store = Store(self.root / "state.sqlite3")
        self.store.create_task(
            task_id="task-1",
            repository=str(self.root),
            baseline_ref="main",
            task_spec={"repository": str(self.root)},
        )
        self.artifacts = ArtifactManager(self.root, self.store)
        self.supervisor = SubprocessSupervisor(self.store)

    def tearDown(self) -> None:
        self.store.close()

    def test_stdout_stderr_exit_and_process_metadata_are_real(self) -> None:
        async def scenario() -> None:
            layout = self.artifacts.create_attempt(task_id="task-1", kind="tool")
            handle = await self.supervisor.start(
                task_id="task-1",
                attempt_id=layout.attempt.attempt_id,
                argv=[
                    sys.executable,
                    "-c",
                    "import sys; print('stdout-value'); print('stderr-value', file=sys.stderr)",
                ],
                cwd=self.root,
                stdout_path=layout.stdout_path,
                stderr_path=layout.stderr_path,
                heartbeat_interval=0.05,
            )
            result = await handle.wait()
            self.assertEqual(result.state, "SUCCEEDED")
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.pid, result.process_group_id)
            self.assertIn("stdout-value", layout.stdout_path.read_text())
            self.assertIn("stderr-value", layout.stderr_path.read_text())
            row = self.store._conn.execute(
                "SELECT * FROM processes WHERE process_id=?", (result.process_id,)
            ).fetchone()
            self.assertEqual(row["state"], "SUCCEEDED")
            self.assertEqual(row["exit_status"], 0)
            self.assertIsNotNone(row["ended_at"])

        asyncio.run(scenario())

    def test_timeout_terminates_long_running_process(self) -> None:
        async def scenario() -> None:
            layout = self.artifacts.create_attempt(task_id="task-1", kind="tool")
            handle = await self.supervisor.start(
                task_id="task-1",
                attempt_id=layout.attempt.attempt_id,
                argv=[sys.executable, "-c", "import time; time.sleep(30)"],
                cwd=self.root,
                stdout_path=layout.stdout_path,
                stderr_path=layout.stderr_path,
                timeout_seconds=0.15,
                terminate_grace_seconds=0.1,
                heartbeat_interval=0.03,
            )
            result = await handle.wait()
            self.assertEqual(result.state, "TIMED_OUT")
            self.assertTrue(result.timed_out)
            self.assertNotEqual(result.returncode, 0)

        asyncio.run(scenario())

    def test_sigterm_ignore_escalates_to_sigkill_after_ready_checkpoint(self) -> None:
        async def scenario() -> None:
            ready = self.root / "ready"
            layout = self.artifacts.create_attempt(task_id="task-1", kind="tool")
            code = (
                "import signal,time,pathlib; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                f"pathlib.Path({str(ready)!r}).write_text('ready'); "
                "time.sleep(30)"
            )
            handle = await self.supervisor.start(
                task_id="task-1",
                attempt_id=layout.attempt.attempt_id,
                argv=[sys.executable, "-c", code],
                cwd=self.root,
                stdout_path=layout.stdout_path,
                stderr_path=layout.stderr_path,
                terminate_grace_seconds=0.05,
                heartbeat_interval=0.03,
            )
            for _ in range(100):
                if ready.exists():
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(ready.exists())
            result = await handle.terminate()
            self.assertEqual(result.state, "TERMINATED")
            self.assertTrue(result.forced_kill)
            self.assertNotEqual(result.returncode, 0)

        asyncio.run(scenario())

    def test_explicit_termination_targets_complete_process_group(self) -> None:
        async def scenario() -> None:
            child_pid_file = self.root / "child.pid"
            layout = self.artifacts.create_attempt(task_id="task-1", kind="tool")
            code = (
                "import subprocess,sys,time,pathlib; "
                "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
                f"pathlib.Path({str(child_pid_file)!r}).write_text(str(p.pid)); "
                "time.sleep(30)"
            )
            handle = await self.supervisor.start(
                task_id="task-1",
                attempt_id=layout.attempt.attempt_id,
                argv=[sys.executable, "-c", code],
                cwd=self.root,
                stdout_path=layout.stdout_path,
                stderr_path=layout.stderr_path,
                terminate_grace_seconds=0.2,
                heartbeat_interval=0.03,
            )
            for _ in range(100):
                if child_pid_file.exists():
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(child_pid_file.exists())
            child_pid = int(child_pid_file.read_text())
            result = await handle.terminate()
            self.assertEqual(result.state, "TERMINATED")
            for _ in range(100):
                proc_stat = Path(f"/proc/{child_pid}/stat")
                if not proc_stat.exists():
                    break
                try:
                    state = proc_stat.read_text().split()[2]
                except (FileNotFoundError, IndexError):
                    break
                if state == "Z":
                    break
                await asyncio.sleep(0.01)
            else:
                self.fail("child process remained live after process-group termination")

        asyncio.run(scenario())

    def test_heartbeat_updates_process_row_without_event_spam(self) -> None:
        async def scenario() -> None:
            before_events = len(self.store.events("task-1"))
            layout = self.artifacts.create_attempt(task_id="task-1", kind="tool")
            handle = await self.supervisor.start(
                task_id="task-1",
                attempt_id=layout.attempt.attempt_id,
                argv=[sys.executable, "-c", "import time; time.sleep(0.18)"],
                cwd=self.root,
                stdout_path=layout.stdout_path,
                stderr_path=layout.stderr_path,
                heartbeat_interval=0.03,
            )
            result = await handle.wait()
            row = self.store._conn.execute(
                "SELECT started_at,last_liveness_at FROM processes WHERE process_id=?",
                (result.process_id,),
            ).fetchone()
            self.assertNotEqual(row["started_at"], row["last_liveness_at"])
            self.assertEqual(len(self.store.events("task-1")), before_events)

        asyncio.run(scenario())

    def test_no_global_short_timeout_is_applied(self) -> None:
        async def scenario() -> None:
            layout = self.artifacts.create_attempt(task_id="task-1", kind="tool")
            started = time.monotonic()
            handle = await self.supervisor.start(
                task_id="task-1",
                argv=[sys.executable, "-c", "import time; time.sleep(0.2)"],
                cwd=self.root,
                stdout_path=layout.stdout_path,
                stderr_path=layout.stderr_path,
                heartbeat_interval=0.04,
            )
            result = await handle.wait()
            self.assertEqual(result.state, "SUCCEEDED")
            self.assertGreaterEqual(time.monotonic() - started, 0.15)

        asyncio.run(scenario())

    def test_argv_is_not_shell_interpreted_and_persisted_command_is_redacted(self) -> None:
        async def scenario() -> None:
            marker = self.root / "must-not-exist"
            layout = self.artifacts.create_attempt(task_id="task-1", kind="tool")
            literal = f"; touch {marker}"
            handle = await self.supervisor.start(
                task_id="task-1",
                argv=[
                    sys.executable,
                    "-c",
                    "import sys; print(sys.argv[1])",
                    literal,
                    "--token=super-secret",
                ],
                cwd=self.root,
                stdout_path=layout.stdout_path,
                stderr_path=layout.stderr_path,
            )
            result = await handle.wait()
            self.assertEqual(result.returncode, 0)
            self.assertFalse(marker.exists())
            self.assertIn(literal, layout.stdout_path.read_text())
            row = self.store._conn.execute(
                "SELECT command_json FROM processes WHERE process_id=?", (result.process_id,)
            ).fetchone()
            persisted = row["command_json"]
            self.assertNotIn("super-secret", persisted)
            self.assertIn("<redacted>", json.loads(persisted)[-1])

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
