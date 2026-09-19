from __future__ import annotations

import asyncio
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_relay.artifacts import ArtifactManager
from agent_relay.codex_writer import CodexWriterProvider, CodexWriterResultKind, parse_codex_jsonl
from agent_relay.git_workspace import GitWorkspaceManager
from agent_relay.models import ProviderConfig
from agent_relay.store import Store
from agent_relay.supervisor import DEFAULT_MAX_OUTPUT_BYTES, SubprocessSupervisor


FAKE_CODEX = r'''#!/usr/bin/env python3
import json
import subprocess
import sys
import time
from pathlib import Path

args = sys.argv[1:]
prompt = sys.stdin.read()
root = Path(__file__).resolve().parent
(root / "fake-codex-trace.json").write_text(
    json.dumps({"args": args, "prompt": prompt}), encoding="utf-8"
)

def event(value):
    print(json.dumps(value), flush=True)

if "MODE:hang" in prompt:
    time.sleep(60)
    raise SystemExit(0)

if "MODE:unavailable" in prompt:
    event({"type": "thread.started", "thread_id": "thread-unavailable"})
    event({"type": "turn.failed", "error": {"message": "rate limit exceeded; try later"}})
    print("HTTP 429 rate limit exceeded", file=sys.stderr, flush=True)
    raise SystemExit(1)

if "MODE:malformed" in prompt:
    print("not-json", flush=True)
    raise SystemExit(0)

worktree = Path(args[args.index("--cd") + 1])
if "MODE:no-commit" not in prompt:
    (worktree / "codex-candidate.txt").write_text("candidate\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(worktree), "add", "codex-candidate.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(worktree), "commit", "-m", "fake codex candidate"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

event({"type": "thread.started", "thread_id": "thread-123"})
if "MODE:large" in prompt:
    filler = "x" * 65536
    for index in range(140):
        event({
            "type": "item.completed",
            "item": {"id": f"fill-{index}", "type": "command_execution", "text": filler},
        })
event({
    "type": "item.completed",
    "item": {"id": "msg-1", "type": "agent_message", "text": "Implemented and committed the change."},
})
event({
    "type": "turn.completed",
    "usage": {
        "input_tokens": 100,
        "cached_input_tokens": 20,
        "cache_write_input_tokens": 3,
        "output_tokens": 40,
        "reasoning_output_tokens": 11,
    },
})
'''


class CodexWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.repo = self.root / "source"
        self.repo.mkdir()
        self._git(self.repo, "init", "-b", "main")
        self._git(self.repo, "config", "user.email", "agent-relay@example.invalid")
        self._git(self.repo, "config", "user.name", "Agent Relay Codex Test")
        (self.repo / "example.txt").write_text("baseline\n", encoding="utf-8")
        self._git(self.repo, "add", "example.txt")
        self._git(self.repo, "commit", "-m", "baseline")

        self.store = Store(self.root / "state.sqlite3")
        self.store.create_task(
            task_id="task-1",
            repository=str(self.repo),
            baseline_ref="main",
            task_spec={"repository": str(self.repo), "baseline": "main"},
        )
        self.git = GitWorkspaceManager(self.root / "managed")
        self.workspaces = self.git.create_writer_worktree(
            task_id="task-1", repository=self.repo, baseline_ref="main"
        )
        self.artifacts = ArtifactManager(self.root / "artifacts", self.store)
        self.fake = self.root / "codex"
        self.fake.write_text(FAKE_CODEX, encoding="utf-8")
        self.fake.chmod(self.fake.stat().st_mode | stat.S_IXUSR)
        self.config = ProviderConfig(
            provider="codex",
            executable=str(self.fake),
            model="gpt-5.6-luna",
            reasoning_effort="max",
            options={"sandbox": "workspace-write", "approval_policy": "never"},
        )
        self.provider = CodexWriterProvider(
            store=self.store,
            artifacts=self.artifacts,
            supervisor=SubprocessSupervisor(self.store),
            git=self.git,
            config=self.config,
        )

    def tearDown(self) -> None:
        self.store.close()

    @staticmethod
    def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
        )

    def test_success_uses_current_exec_shape_stdin_and_captures_handoff_usage(self) -> None:
        async def scenario() -> None:
            prompt = "Implement the bounded task and commit it."
            result = await self.provider.run(
                task_id="task-1",
                writer_worktree=self.workspaces.writer,
                baseline_sha=self.workspaces.baseline_sha,
                prompt=prompt,
                timeout_seconds=5,
            )
            self.assertEqual(result.kind, CodexWriterResultKind.SUCCESS)
            self.assertEqual(result.thread_id, "thread-123")
            self.assertEqual(result.handoff, "Implemented and committed the change.")
            self.assertEqual(result.usage["input_tokens"], 100)
            self.assertEqual(result.usage["reasoning_output_tokens"], 11)
            self.assertIsNotNone(result.candidate_sha)

            trace = json.loads((self.root / "fake-codex-trace.json").read_text(encoding="utf-8"))
            self.assertEqual(trace["prompt"], prompt)
            args = trace["args"]
            self.assertEqual(args[:2], ["exec", "--experimental-json"])
            self.assertIn("--model", args)
            self.assertEqual(args[args.index("--model") + 1], "gpt-5.6-luna")
            self.assertEqual(args[args.index("--sandbox") + 1], "workspace-write")
            self.assertEqual(args[args.index("--cd") + 1], str(self.workspaces.writer))
            self.assertIn('model_reasoning_effort="max"', args)
            self.assertIn('approval_policy="never"', args)
            self.assertNotIn(prompt, args)

            row = self.store._conn.execute(
                "SELECT command_json FROM processes WHERE attempt_id=?",
                (result.attempt_id,),
            ).fetchone()
            self.assertNotIn(prompt, row["command_json"])
            attempt = self.store.get_attempt(result.attempt_id)
            self.assertEqual(attempt.status, "SUCCESS")
            self.assertEqual(attempt.result["thread_id"], "thread-123")
            self.assertEqual(attempt.result["usage"]["output_tokens"], 40)
        asyncio.run(scenario())

    def test_oversized_jsonl_uses_complete_protocol_artifact_not_bounded_tail(self) -> None:
        async def scenario() -> None:
            result = await self.provider.run(
                task_id="task-1",
                writer_worktree=self.workspaces.writer,
                baseline_sha=self.workspaces.baseline_sha,
                prompt="MODE:large",
                timeout_seconds=10,
            )
            self.assertEqual(result.kind, CodexWriterResultKind.SUCCESS)
            self.assertEqual(result.thread_id, "thread-123")
            self.assertEqual(result.usage["output_tokens"], 40)
            self.assertLessEqual(result.stdout_path.stat().st_size, DEFAULT_MAX_OUTPUT_BYTES)
            self.assertIn(
                b"earlier output truncated",
                result.stdout_path.read_bytes()[:128],
            )
            artifact = self.store._conn.execute(
                "SELECT path FROM artifacts WHERE attempt_id=? AND kind='codex_jsonl'",
                (result.attempt_id,),
            ).fetchone()
            self.assertIsNotNone(artifact)
            protocol = self.artifacts.root / artifact["path"]
            self.assertGreater(protocol.stat().st_size, DEFAULT_MAX_OUTPUT_BYTES)
            summary = parse_codex_jsonl(protocol)
            self.assertEqual(summary.thread_id, "thread-123")
            self.assertEqual(summary.handoff, "Implemented and committed the change.")
        asyncio.run(scenario())

    def test_rate_limit_is_provider_unavailable_not_generic_failure(self) -> None:
        async def scenario() -> None:
            result = await self.provider.run(
                task_id="task-1",
                writer_worktree=self.workspaces.writer,
                baseline_sha=self.workspaces.baseline_sha,
                prompt="MODE:unavailable",
                timeout_seconds=5,
            )
            self.assertEqual(result.kind, CodexWriterResultKind.PROVIDER_UNAVAILABLE)
            self.assertIn("rate limit", result.reason.lower())
            self.assertIsNone(result.candidate_sha)
        asyncio.run(scenario())

    def test_exit_zero_malformed_stream_never_succeeds(self) -> None:
        async def scenario() -> None:
            result = await self.provider.run(
                task_id="task-1",
                writer_worktree=self.workspaces.writer,
                baseline_sha=self.workspaces.baseline_sha,
                prompt="MODE:malformed",
                timeout_seconds=5,
            )
            self.assertEqual(result.kind, CodexWriterResultKind.MALFORMED)
            self.assertIsNone(result.candidate_sha)
        asyncio.run(scenario())

    def test_valid_stream_without_commit_is_failure(self) -> None:
        async def scenario() -> None:
            result = await self.provider.run(
                task_id="task-1",
                writer_worktree=self.workspaces.writer,
                baseline_sha=self.workspaces.baseline_sha,
                prompt="MODE:no-commit",
                timeout_seconds=5,
            )
            self.assertEqual(result.kind, CodexWriterResultKind.FAILURE)
            self.assertIsNone(result.candidate_sha)
        asyncio.run(scenario())

    def test_timeout_remains_process_failure_and_cleans_cli(self) -> None:
        async def scenario() -> None:
            result = await self.provider.run(
                task_id="task-1",
                writer_worktree=self.workspaces.writer,
                baseline_sha=self.workspaces.baseline_sha,
                prompt="MODE:hang",
                timeout_seconds=0.15,
            )
            self.assertEqual(result.kind, CodexWriterResultKind.PROCESS_FAILURE)
            self.assertEqual(result.process_result.state, "TIMED_OUT")
        asyncio.run(scenario())

    def test_parser_sums_multiple_turn_usage_and_uses_last_agent_message(self) -> None:
        path = self.root / "events.jsonl"
        path.write_text(
            "\n".join(
                [
                    json.dumps({"type": "thread.started", "thread_id": "t"}),
                    json.dumps({"type": "item.completed", "item": {"type": "agent_message", "id": "1", "text": "first"}}),
                    json.dumps({"type": "turn.completed", "usage": {"input_tokens": 2, "cached_input_tokens": 1, "output_tokens": 3}}),
                    json.dumps({"type": "item.completed", "item": {"type": "agent_message", "id": "2", "text": "final"}}),
                    json.dumps({"type": "turn.completed", "usage": {"input_tokens": 5, "cached_input_tokens": 0, "output_tokens": 7, "reasoning_output_tokens": 4}}),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        summary = parse_codex_jsonl(path)
        self.assertEqual(summary.thread_id, "t")
        self.assertEqual(summary.handoff, "final")
        self.assertEqual(summary.usage["input_tokens"], 7)
        self.assertEqual(summary.usage["output_tokens"], 10)
        self.assertEqual(summary.usage["reasoning_output_tokens"], 4)


if __name__ == "__main__":
    unittest.main()
