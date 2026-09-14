from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from agent_relay.models import RunnerConfig
from agent_relay.ssh_runner import (
    SSHExternalToolRunner,
    SSHRunnerError,
    build_ssh_transport_plan,
)
from agent_relay.store import Store
from agent_relay.supervisor import SubprocessSupervisor


_FAKE_SSH = r'''#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import time
from pathlib import Path

capture = Path(__file__).with_name("ssh-capture.json")
script = sys.stdin.read()
capture.write_text(json.dumps({"argv": sys.argv[1:], "stdin": script}), encoding="utf-8")
print("fake-ssh-stdout")
print("fake-ssh-stderr", file=sys.stderr)
mode = os.environ.get("FAKE_SSH_MODE", "success")
if mode == "execute":
    completed = subprocess.run(
        ["/bin/sh", "-s"],
        input=script,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    sys.stdout.write(completed.stdout)
    sys.stderr.write(completed.stderr)
    raise SystemExit(completed.returncode)
if mode == "sleep":
    time.sleep(300)
if mode == "nonzero":
    raise SystemExit(7)
'''

_REMOTE_HELPER = r'''
import json
import os
import sys
from pathlib import Path

output = Path(sys.argv[1])
output.write_text(json.dumps({
    "argv": sys.argv[2:],
    "cwd": os.getcwd(),
    "remote_value": os.environ.get("REMOTE_VALUE"),
}), encoding="utf-8")
'''


class SSHExternalToolRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.store = Store(self.root / "state.sqlite3")
        self.store.create_task(
            task_id="task-1",
            repository=str(self.root),
            baseline_ref="main",
            task_spec={"repository": str(self.root)},
        )
        self.fake_ssh = self.root / "fake-ssh"
        self.fake_ssh.write_text(_FAKE_SSH, encoding="utf-8")
        self.fake_ssh.chmod(self.fake_ssh.stat().st_mode | stat.S_IXUSR)
        self.config = RunnerConfig(
            kind="ssh",
            executable=str(self.fake_ssh),
            host="gpu.example.invalid",
            user="ubuntu",
            base_dir="/srv/agent relay",
            options={"ssh_args": ["-p", "2222"]},
        )
        self.runner = SSHExternalToolRunner(
            supervisor=SubprocessSupervisor(self.store),
            config=self.config,
        )
        self._old_mode = os.environ.pop("FAKE_SSH_MODE", None)

    def tearDown(self) -> None:
        if self._old_mode is None:
            os.environ.pop("FAKE_SSH_MODE", None)
        else:
            os.environ["FAKE_SSH_MODE"] = self._old_mode
        self.store.close()

    def _capture(self) -> dict[str, object]:
        return json.loads((self.root / "ssh-capture.json").read_text(encoding="utf-8"))

    def test_plan_keeps_task_values_out_of_local_argv_and_quotes_remote_script(self) -> None:
        marker = self.root / "must-not-exist"
        dangerous = f"$(touch {marker})"
        plan = build_ssh_transport_plan(
            self.config,
            argv=["python", "tool.py", "a b", "semi;colon", dangerous, "quote'arg"],
            cwd="runs/task 1",
            env_additions={"TOKEN_VALUE": "secret value;$(bad)", "Z": "last"},
        )
        self.assertEqual(
            plan.transport_argv,
            (
                str(self.fake_ssh),
                "-T",
                "-o",
                "BatchMode=yes",
                "-p",
                "2222",
                "ubuntu@gpu.example.invalid",
                "sh",
                "-s",
            ),
        )
        self.assertEqual(plan.remote_cwd, "/srv/agent relay/runs/task 1")
        local_command = " ".join(plan.transport_argv)
        self.assertNotIn("tool.py", local_command)
        self.assertNotIn("TOKEN_VALUE", local_command)
        self.assertNotIn("secret value", local_command)
        self.assertIn("cd -- '/srv/agent relay/runs/task 1'", plan.remote_script)
        self.assertIn("export TOKEN_VALUE='secret value;$(bad)'", plan.remote_script)
        self.assertIn("'semi;colon'", plan.remote_script)
        self.assertIn("'$(touch ", plan.remote_script)
        self.assertFalse(marker.exists())

    def test_real_fake_ssh_process_captures_output_exit_and_transport_metadata(self) -> None:
        async def scenario() -> None:
            out = self.root / "logs" / "stdout.log"
            err = self.root / "logs" / "stderr.log"
            marker = self.root / "no-local-shell"
            process = await self.runner.start(
                task_id="task-1",
                argv=["python", "remote.py", f"$(touch {marker})", "x y"],
                cwd="run",
                env_additions={"REMOTE_ONLY": "value with spaces"},
                stdout_path=out,
                stderr_path=err,
                timeout_seconds=5.0,
            )
            result = await process.wait()
            self.assertEqual(result.state, "SUCCEEDED")
            self.assertEqual(result.returncode, 0)
            self.assertIn("fake-ssh-stdout", out.read_text(encoding="utf-8"))
            self.assertIn("fake-ssh-stderr", err.read_text(encoding="utf-8"))
            capture = self._capture()
            self.assertEqual(
                capture["argv"],
                ["-T", "-o", "BatchMode=yes", "-p", "2222", "ubuntu@gpu.example.invalid", "sh", "-s"],
            )
            script = str(capture["stdin"])
            self.assertIn("exec python remote.py", script)
            self.assertIn("export REMOTE_ONLY='value with spaces'", script)
            self.assertFalse(marker.exists())

            row = self.store._conn.execute(
                "SELECT command_json,state,exit_status FROM processes WHERE process_id=?",
                (result.process_id,),
            ).fetchone()
            persisted = row["command_json"]
            self.assertNotIn("remote.py", persisted)
            self.assertNotIn("REMOTE_ONLY", persisted)
            self.assertEqual((row["state"], row["exit_status"]), ("SUCCEEDED", 0))
        asyncio.run(scenario())

    def test_remote_shell_execution_preserves_hostile_arguments_without_interpretation(self) -> None:
        async def scenario() -> None:
            remote_base = self.root / "remote base"
            remote_cwd = remote_base / "run dir"
            remote_cwd.mkdir(parents=True)
            helper = self.root / "remote_helper.py"
            helper.write_text(textwrap.dedent(_REMOTE_HELPER), encoding="utf-8")
            output = self.root / "remote-result.json"
            marker = self.root / "shell-injection-marker"
            dangerous = f"$(touch {marker})"
            runner = SSHExternalToolRunner(
                supervisor=SubprocessSupervisor(self.store),
                config=RunnerConfig(
                    kind="ssh",
                    executable=str(self.fake_ssh),
                    host="gpu.example.invalid",
                    user="ubuntu",
                    base_dir=str(remote_base),
                ),
            )
            os.environ["FAKE_SSH_MODE"] = "execute"
            expected_args = ["a b", "semi;colon", dangerous, "quote'arg"]
            process = await runner.start(
                task_id="task-1",
                argv=[sys.executable, str(helper), str(output), *expected_args],
                cwd="run dir",
                env_additions={"REMOTE_VALUE": "value;$(echo not-code) 'quoted'"},
                stdout_path=self.root / "execute.out",
                stderr_path=self.root / "execute.err",
                timeout_seconds=5.0,
            )
            result = await process.wait()
            self.assertEqual((result.state, result.returncode), ("SUCCEEDED", 0))
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["argv"], expected_args)
            self.assertEqual(payload["cwd"], str(remote_cwd))
            self.assertEqual(payload["remote_value"], "value;$(echo not-code) 'quoted'")
            self.assertFalse(marker.exists())
        asyncio.run(scenario())

    def test_nonzero_remote_transport_exit_is_failed(self) -> None:
        async def scenario() -> None:
            os.environ["FAKE_SSH_MODE"] = "nonzero"
            process = await self.runner.start(
                task_id="task-1",
                argv=["false"],
                cwd=None,
                stdout_path=self.root / "nonzero.out",
                stderr_path=self.root / "nonzero.err",
            )
            result = await process.wait()
            self.assertEqual(result.state, "FAILED")
            self.assertEqual(result.returncode, 7)
        asyncio.run(scenario())

    def test_stage_timeout_terminates_local_ssh_process_group(self) -> None:
        async def scenario() -> None:
            os.environ["FAKE_SSH_MODE"] = "sleep"
            process = await self.runner.start(
                task_id="task-1",
                argv=["long-running-tool"],
                cwd=None,
                stdout_path=self.root / "timeout.out",
                stderr_path=self.root / "timeout.err",
                timeout_seconds=0.12,
                terminate_grace_seconds=0.1,
            )
            result = await process.wait()
            self.assertEqual(result.state, "TIMED_OUT")
            self.assertTrue(result.timed_out)
        asyncio.run(scenario())

    def test_invalid_remote_inputs_fail_before_launch(self) -> None:
        with self.assertRaisesRegex(SSHRunnerError, "environment variable"):
            build_ssh_transport_plan(
                self.config,
                argv=["true"],
                env_additions={"BAD-NAME": "value"},
            )
        with self.assertRaisesRegex(SSHRunnerError, r"must not contain '\.\.'"):
            build_ssh_transport_plan(self.config, argv=["true"], cwd="../escape")
        with self.assertRaisesRegex(SSHRunnerError, "remain under configured base_dir"):
            build_ssh_transport_plan(self.config, argv=["true"], cwd="/tmp/outside")
        bad_host = RunnerConfig(kind="ssh", host="-oProxyCommand=bad")
        with self.assertRaisesRegex(SSHRunnerError, "host"):
            build_ssh_transport_plan(bad_host, argv=["true"])
        bad_user = RunnerConfig(kind="ssh", host="host", user="bad user")
        with self.assertRaisesRegex(SSHRunnerError, "user"):
            build_ssh_transport_plan(bad_user, argv=["true"])

    def test_command_stdin_is_rejected_because_transport_owns_stdin(self) -> None:
        async def scenario() -> None:
            with self.assertRaisesRegex(SSHRunnerError, "transport stdin"):
                await self.runner.start(
                    task_id="task-1",
                    argv=["cat"],
                    cwd=None,
                    stdout_path=self.root / "stdin.out",
                    stderr_path=self.root / "stdin.err",
                    stdin_text="payload",
                )
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
