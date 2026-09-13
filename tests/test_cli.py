from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from agent_relay.cli import build_parser, main
from tests.test_config import VALID_CONFIG, VALID_TASK


class CliTests(unittest.TestCase):
    def _write(self, content: str) -> Path:
        directory = Path(tempfile.mkdtemp())
        path = directory / "input.yaml"
        path.write_text(content, encoding="utf-8")
        return path

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

    def test_task_create_validates_and_reports_summary(self) -> None:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            rc = main(["task", "create", str(self._write(VALID_TASK))])
        self.assertEqual(rc, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "valid")
        self.assertEqual(payload["validation_steps"], 2)

    def test_execution_commands_fail_closed_until_implemented(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            rc = main(["run", "task-1"])
        self.assertEqual(rc, 2)
        self.assertIn("not implemented", stderr.getvalue())

    def test_doctor_can_validate_global_config(self) -> None:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            rc = main(["doctor", "--config", str(self._write(VALID_CONFIG))])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(stdout.getvalue())["resource_count"], 1)

    def test_invalid_task_returns_configuration_error(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            rc = main(["task", "create", str(self._write("baseline: main\n"))])
        self.assertEqual(rc, 2)
        self.assertIn("configuration error", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
