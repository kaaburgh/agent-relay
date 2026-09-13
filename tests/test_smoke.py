from __future__ import annotations

import unittest

import agent_relay
from agent_relay.cli import main


class SmokeTests(unittest.TestCase):
    def test_version_is_defined(self) -> None:
        self.assertEqual(agent_relay.__version__, "0.1.0")

    def test_cli_help_shell_returns_success(self) -> None:
        self.assertEqual(main([]), 0)


if __name__ == "__main__":
    unittest.main()
