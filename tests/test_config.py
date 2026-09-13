from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_relay.config import load_global_config, load_task_spec
from agent_relay.models import ConfigError


VALID_CONFIG = """
writer:
  provider: simulated
  model: fake-writer
reviewer:
  provider: simulated
  model: fake-reviewer
runners:
  local:
    kind: local
  gpu:
    kind: ssh
    host: gpu.example.invalid
resources:
  bloodborne-runtime:
    capacity: 1
state_dir: .agent-relay-test
"""

VALID_TASK = """
repository: /tmp/example-repo
baseline: main
writer_instructions: Implement the requested change.
validation:
  - name: tests
    runner: local
    argv: [python, -m, unittest]
    timeout_seconds: 120
  - name: runtime
    runner: gpu
    argv: [python, tools/fake_runtime.py]
    resources: [bloodborne-runtime]
review_instructions: Review independently.
acceptance_criteria:
  - tests pass
  - review approves
runtime_resources: [bloodborne-runtime]
max_correction_rounds: 2
workspace:
  mode: managed
"""


class ConfigTests(unittest.TestCase):
    def _write(self, content: str, suffix: str = ".yaml") -> Path:
        directory = Path(tempfile.mkdtemp())
        path = directory / f"input{suffix}"
        path.write_text(content, encoding="utf-8")
        return path

    def test_load_global_config_preserves_provider_runner_resource_shape(self) -> None:
        config = load_global_config(self._write(VALID_CONFIG))
        self.assertEqual(config.writer.provider, "simulated")
        self.assertEqual(config.reviewer.provider, "simulated")
        self.assertEqual(config.runners["gpu"].kind, "ssh")
        self.assertEqual(config.resources["bloodborne-runtime"].capacity, 1)
        self.assertEqual(str(config.state_dir), ".agent-relay-test")

    def test_load_task_spec(self) -> None:
        task = load_task_spec(self._write(VALID_TASK))
        self.assertEqual(task.repository, "/tmp/example-repo")
        self.assertEqual(len(task.validation), 2)
        self.assertEqual(task.validation[1].resources, ("bloodborne-runtime",))
        self.assertEqual(task.max_correction_rounds, 2)
        self.assertEqual(task.workspace.mode, "managed")

    def test_missing_required_task_field_fails_clearly(self) -> None:
        with self.assertRaisesRegex(ConfigError, r"task\.repository"):
            load_task_spec(self._write(VALID_TASK.replace("repository: /tmp/example-repo\n", "")))

    def test_empty_acceptance_criteria_fails(self) -> None:
        invalid = VALID_TASK.replace("acceptance_criteria:\n  - tests pass\n  - review approves\n", "acceptance_criteria: []\n")
        with self.assertRaises(ConfigError):
            load_task_spec(self._write(invalid))

    def test_ssh_runner_requires_host(self) -> None:
        invalid = VALID_CONFIG.replace("    host: gpu.example.invalid\n", "")
        with self.assertRaisesRegex(ConfigError, "host is required"):
            load_global_config(self._write(invalid))

    def test_resource_capacity_must_be_positive(self) -> None:
        invalid = VALID_CONFIG.replace("capacity: 1", "capacity: 0")
        with self.assertRaisesRegex(ConfigError, "positive integer"):
            load_global_config(self._write(invalid))


if __name__ == "__main__":
    unittest.main()
