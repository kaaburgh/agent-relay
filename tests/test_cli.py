from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_relay.cli import build_parser, main
from agent_relay.store import Store
from tests.test_config import VALID_CONFIG, VALID_TASK


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())

    def _write(self, content: str, name: str = "input.yaml") -> Path:
        path = self.root / name
        path.write_text(content, encoding="utf-8")
        return path

    def _call(self, argv: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = main(argv)
        return rc, stdout.getvalue(), stderr.getvalue()

    def _git(self, repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
        )

    def _simulation_files(self) -> tuple[Path, Path, Path]:
        repo = self.root / "repo"
        repo.mkdir()
        self._git(repo, "init", "-b", "main")
        self._git(repo, "config", "user.email", "agent-relay@example.invalid")
        self._git(repo, "config", "user.name", "Agent Relay CLI Test")
        (repo / "example.txt").write_text("baseline\n", encoding="utf-8")
        self._git(repo, "add", "example.txt")
        self._git(repo, "commit", "-m", "baseline")

        state = self.root / "state"
        config = self._write(
            f"""
writer:
  provider: simulated
reviewer:
  provider: simulated
runners:
  sim:
    kind: local
    options:
      simulated_validator: true
      requested_cycles: 2
resources: {{}}
state_dir: {state}
""",
            "config.yaml",
        )
        task = self._write(
            f"""
task_id: sim-task
repository: {repo}
baseline: main
writer_instructions: Simulate a committed candidate.
validation:
  - name: simulated-runtime
    runner: sim
    argv: [simulated-validator]
review_instructions: Review the simulated candidate.
acceptance_criteria:
  - simulated validation passes
  - simulated review approves
max_correction_rounds: 2
workspace:
  mode: managed
""",
            "task.yaml",
        )
        return config, task, state

    def test_expected_command_routes_exist(self) -> None:
        parser = build_parser()
        for command in ("run", "status", "events", "resume", "cancel"):
            args = parser.parse_args([command, "task-1"])
            self.assertEqual(args.command, command)
            self.assertTrue(callable(args.handler))
        args = parser.parse_args(["task", "create", "task.yaml"])
        self.assertEqual(args.task_command, "create")
        self.assertTrue(callable(args.handler))
        args = parser.parse_args(["doctor"])
        self.assertEqual(args.command, "doctor")

    def test_task_create_persists_snapshot_and_status_events_are_readable(self) -> None:
        state = self.root / "state"
        task_text = "task_id: task-1\n" + VALID_TASK
        task_path = self._write(task_text, "task.yaml")
        rc, stdout, stderr = self._call(
            ["task", "create", str(task_path), "--state-dir", str(state)]
        )
        self.assertEqual((rc, stderr), (0, ""))
        created = json.loads(stdout)
        self.assertEqual(created["task_id"], "task-1")
        self.assertEqual(created["stage"], "READY")
        self.assertTrue((state / "tasks" / "task-1" / "task.yaml").is_file())
        self.assertTrue((state / "state.sqlite3").is_file())

        rc, stdout, _ = self._call(["status", "task-1", "--state-dir", str(state)])
        self.assertEqual(rc, 0)
        status = json.loads(stdout)
        self.assertEqual(status["stage"], "READY")
        self.assertEqual(status["last_event"]["type"], "task_created")

        rc, stdout, _ = self._call(
            ["events", "task-1", "--limit", "1", "--state-dir", str(state)]
        )
        self.assertEqual(rc, 0)
        events = json.loads(stdout)["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "task_created")

    def test_run_without_real_backend_fails_closed_without_mutating_ready_task(self) -> None:
        state = self.root / "state"
        task_path = self._write("task_id: task-1\n" + VALID_TASK, "task.yaml")
        self.assertEqual(
            self._call(["task", "create", str(task_path), "--state-dir", str(state)])[0],
            0,
        )
        rc, stdout, _ = self._call(["run", "task-1", "--state-dir", str(state)])
        self.assertEqual(rc, 2)
        self.assertEqual(json.loads(stdout)["action"], "backend-not-installed")
        with Store(state / "state.sqlite3") as store:
            self.assertEqual(store.get_task("task-1").stage, "READY")
            self.assertEqual(len(store.attempts("task-1")), 0)

    def test_simulation_run_reaches_done_and_is_visible_through_status(self) -> None:
        config, task, state = self._simulation_files()
        rc, stdout, stderr = self._call(
            ["task", "create", str(task), "--config", str(config)]
        )
        self.assertEqual((rc, stderr), (0, ""))
        self.assertEqual(json.loads(stdout)["task_id"], "sim-task")

        rc, stdout, stderr = self._call(
            ["run", "sim-task", "--simulation", "--config", str(config)]
        )
        self.assertEqual((rc, stderr), (0, ""))
        result = json.loads(stdout)
        self.assertEqual(result["backend"], "simulation")
        self.assertEqual(result["stage"], "DONE")
        self.assertEqual(result["generation"], 1)
        self.assertEqual(result["status"]["review"]["verdict"], "APPROVE")
        self.assertEqual(result["status"]["validation"]["status"], "SUCCESS")

        rc, stdout, _ = self._call(
            ["status", "sim-task", "--state-dir", str(state)]
        )
        self.assertEqual(rc, 0)
        status = json.loads(stdout)
        self.assertEqual(status["stage"], "DONE")
        self.assertEqual(status["generation"], 1)
        self.assertIsNotNone(status["candidate_sha"])

    def test_resume_ready_is_safe_and_cancel_transitions_durably(self) -> None:
        state = self.root / "state"
        task = self._write("task_id: task-1\n" + VALID_TASK, "task.yaml")
        self.assertEqual(
            self._call(["task", "create", str(task), "--state-dir", str(state)])[0],
            0,
        )
        rc, stdout, _ = self._call(["resume", "task-1", "--state-dir", str(state)])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(stdout)["action"], "ready-use-run")

        rc, stdout, stderr = self._call(
            ["cancel", "task-1", "--grace-seconds", "0.1", "--state-dir", str(state)]
        )
        self.assertEqual((rc, stderr), (0, ""))
        self.assertEqual(json.loads(stdout)["stage"], "CANCELLED")
        with Store(state / "state.sqlite3") as store:
            self.assertEqual(store.get_task("task-1").stage, "CANCELLED")
            self.assertEqual(store.events("task-1")[-1].event_type, "task_cancelled")

    def test_doctor_checks_config_state_and_task_repository(self) -> None:
        config, task, _state = self._simulation_files()
        rc, stdout, stderr = self._call(
            ["doctor", "--config", str(config), "--task", str(task)]
        )
        self.assertEqual((rc, stderr), (0, ""))
        payload = json.loads(stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(all(item["ok"] for item in payload["providers"]))
        self.assertTrue(all(item["ok"] for item in payload["task_checks"]))

    def test_invalid_task_returns_error(self) -> None:
        rc, _stdout, stderr = self._call(
            ["task", "create", str(self._write("baseline: main\n"))]
        )
        self.assertEqual(rc, 2)
        self.assertIn("task.repository", stderr)


if __name__ == "__main__":
    unittest.main()
