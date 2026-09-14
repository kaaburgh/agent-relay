from __future__ import annotations

import unittest

from agent_relay.artifacts import redact


class UsageRedactionTests(unittest.TestCase):
    def test_known_numeric_usage_counters_are_preserved_but_credentials_are_not(self) -> None:
        value = redact(
            {
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 3,
                    "cache_creation_input_tokens": 4,
                    "cache_read_input_tokens": 5,
                },
                "token": "credential-a",
                "api_token": "credential-b",
                "access_token": "credential-c",
            }
        )
        self.assertEqual(value["usage"]["input_tokens"], 10)
        self.assertEqual(value["usage"]["cache_creation_input_tokens"], 4)
        self.assertEqual(value["usage"]["cache_read_input_tokens"], 5)
        self.assertEqual(value["token"], "<redacted>")
        self.assertEqual(value["api_token"], "<redacted>")
        self.assertEqual(value["access_token"], "<redacted>")


if __name__ == "__main__":
    unittest.main()
