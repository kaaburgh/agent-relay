from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from agent_relay.cli import main
from agent_relay.models import ConfigError, ValidationStep


class TaskEnvironmentSecretTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())

    def _task(self, env_block: str) -> Path:
        path = self.root / "task.yaml"
        path.write_text(
            f"""
task_id: secret-env-test
repository: /tmp/example-repo
baseline: main
writer_instructions: Implement the requested change.
validation:
  - name: tests
    runner: local
    argv: [python, -m, unittest]
{env_block}
review_instructions: Review independently.
acceptance_criteria:
  - tests pass
workspace:
  mode: managed
""",
            encoding="utf-8",
        )
        return path

    def _call(self, argv: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = main(argv)
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_task_create_rejects_credential_key_before_durable_state_is_written(self) -> None:
        secret = "literal-secret-that-must-not-persist"
        task = self._task(f"    env:\n      API_TOKEN: {secret}\n")
        state = self.root / "state"

        rc, _stdout, stderr = self._call(
            ["task", "create", str(task), "--state-dir", str(state)]
        )
        self.assertEqual(rc, 2)
        self.assertIn("inherited agent-relay process environment", stderr)

        if state.exists():
            for path in state.rglob("*"):
                if path.is_file():
                    self.assertNotIn(secret.encode(), path.read_bytes())

    def test_credential_shaped_value_is_rejected_even_under_generic_key(self) -> None:
        with self.assertRaisesRegex(ConfigError, "credential-shaped"):
            ValidationStep(
                name="tests",
                runner="local",
                argv=("python", "-m", "unittest"),
                env={"EXTRA_ARGS": "Authorization=Bearer abc123"},
            )

    def test_non_secret_literal_environment_remains_supported(self) -> None:
        step = ValidationStep(
            name="tests",
            runner="local",
            argv=("python", "-m", "unittest"),
            env={
                "LOG_LEVEL": "debug",
                "FEATURE_MODE": "strict",
                "TOKENIZERS_PARALLELISM": "false",
            },
        )
        self.assertEqual(step.env["LOG_LEVEL"], "debug")
        self.assertEqual(step.env["FEATURE_MODE"], "strict")
        self.assertEqual(step.env["TOKENIZERS_PARALLELISM"], "false")


if __name__ == "__main__":
    unittest.main()
