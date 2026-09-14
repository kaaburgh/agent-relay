from __future__ import annotations

import asyncio
import json
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_relay.artifacts import ArtifactManager
from agent_relay.claude_reviewer import ClaudeReviewerProvider, ClaudeReviewerResultKind
from agent_relay.git_workspace import GitWorkspaceManager
from agent_relay.models import ProviderConfig
from agent_relay.review_package import build_review_package
from agent_relay.store import Store
from agent_relay.supervisor import SubprocessSupervisor
from agent_relay.workflow import ReviewVerdict


FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json
import sys
from pathlib import Path

args = sys.argv[1:]
stdin = sys.stdin.read()
root = Path(__file__).resolve().parent
(root / "fake-claude-trace.json").write_text(
    json.dumps({"args": args, "stdin": stdin}), encoding="utf-8"
)

if "MODE:unavailable" in stdin:
    print("HTTP 429 rate limit exceeded", file=sys.stderr, flush=True)
    raise SystemExit(1)

if "MODE:malformed" in stdin:
    print("not-json", flush=True)
    raise SystemExit(0)

if "MODE:modify" in stdin:
    Path("reviewer-wrote.txt").write_text("unexpected write\n", encoding="utf-8")

if "MODE:changes" in stdin:
    structured = {
        "verdict": "REQUEST_CHANGES",
        "findings": [{
            "severity": "HIGH",
            "title": "candidate violates acceptance",
            "file": "example.txt",
            "symbol": None,
            "problem": "the candidate value is not acceptable",
            "failure_scenario": "acceptance test observes the wrong value",
            "required_action": "correct example.txt",
        }],
        "summary": "one blocking issue",
    }
else:
    structured = {
        "verdict": "APPROVE",
        "findings": [],
        "summary": "candidate and deterministic evidence satisfy the criteria",
    }

print(json.dumps({
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "session_id": "claude-session-123",
    "result": "structured review returned",
    "structured_output": structured,
    "usage": {
        "input_tokens": 321,
        "output_tokens": 45,
        "cache_read_input_tokens": 12,
    },
}), flush=True)
'''


class ClaudeReviewerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.repo = self.root / "source"
        self.repo.mkdir()
        self._git(self.repo, "init", "-b", "main")
        self._git(self.repo, "config", "user.email", "agent-relay@example.invalid")
        self._git(self.repo, "config", "user.name", "Agent Relay Claude Test")
        (self.repo / "example.txt").write_text("baseline\n", encoding="utf-8")
        self._git(self.repo, "add", "example.txt")
        self._git(self.repo, "commit", "-m", "baseline")
        self.baseline_sha = self._git(self.repo, "rev-parse", "HEAD").stdout.strip()

        self.store = Store(self.root / "state.sqlite3")
        self.store.create_task(
            task_id="task-1",
            repository=str(self.repo),
            baseline_ref="main",
            task_spec={"repository": str(self.repo), "baseline": "main"},
        )
        self.git = GitWorkspaceManager(self.root / "managed")
        writer = self.git.create_writer_worktree(
            task_id="task-1", repository=self.repo, baseline_ref="main"
        )
        (writer.writer / "example.txt").write_text("candidate\n", encoding="utf-8")
        self._git(writer.writer, "add", "example.txt")
        self._git(writer.writer, "commit", "-m", "candidate")
        self.candidate_sha = self._git(writer.writer, "rev-parse", "HEAD").stdout.strip()
        self.reviewer = self.git.create_reviewer_worktree(
            task_id="task-1",
            repository=self.repo,
            generation=1,
            candidate_sha=self.candidate_sha,
        )

        self.artifacts = ArtifactManager(self.root / "artifacts", self.store)
        self.fake = self.root / "claude"
        self.fake.write_text(FAKE_CLAUDE, encoding="utf-8")
        self.fake.chmod(self.fake.stat().st_mode | stat.S_IXUSR)
        self.config = ProviderConfig(
            provider="claude",
            executable=str(self.fake),
            model="claude-opus-5",
            options={"permission_mode": "plan", "tools": "Read,Grep,Glob", "max_turns": 4},
        )
        self.provider = ClaudeReviewerProvider(
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

    def _package(self, *claims: str, max_diff_bytes: int = 200_000):
        return build_review_package(
            task_id="task-1",
            generation=1,
            reviewer_worktree=self.reviewer,
            baseline_sha=self.baseline_sha,
            candidate_sha=self.candidate_sha,
            task_spec={
                "repository": str(self.repo),
                "writer_instructions": "change the example value",
            },
            acceptance_criteria=["example.txt contains the intended candidate value"],
            review_instructions="Review adversarially and require deterministic evidence.",
            deterministic_evidence=[
                {"kind": "tests", "status": "SUCCESS", "path": "attempts/validation-001/result.json"}
            ],
            commands=[["python", "-m", "unittest"]],
            writer_claims=list(claims) or ["Writer says the candidate is correct."],
            max_diff_bytes=max_diff_bytes,
        )

    def test_package_is_bounded_and_labels_writer_claims_as_untrusted(self) -> None:
        package = self._package()
        payload = package.to_dict()
        self.assertEqual(payload["candidate_sha"], self.candidate_sha)
        self.assertEqual(payload["source_paths"], ["example.txt"])
        self.assertIn("-baseline", payload["git_diff"])
        self.assertIn("+candidate", payload["git_diff"])
        self.assertEqual(payload["UNTRUSTED_WRITER_CLAIMS"], ["Writer says the candidate is correct."])
        self.assertNotIn("reasoning", json.dumps(payload).lower())
        self.assertFalse(payload["git_diff_truncated"])

        tiny = self._package(max_diff_bytes=20)
        self.assertTrue(tiny.diff_truncated)
        self.assertIn("DIFF TRUNCATED", tiny.diff)

    def test_success_is_fresh_plan_mode_read_only_and_structured(self) -> None:
        async def scenario() -> None:
            result = await self.provider.run(
                task_id="task-1",
                reviewer_worktree=self.reviewer,
                generation=1,
                candidate_sha=self.candidate_sha,
                package=self._package(),
                timeout_seconds=5,
            )
            self.assertEqual(result.kind, ClaudeReviewerResultKind.SUCCESS)
            self.assertEqual(result.session_id, "claude-session-123")
            self.assertEqual(result.review.verdict, ReviewVerdict.APPROVE)
            self.assertEqual(result.usage["input_tokens"], 321)
            self.assertEqual(self.git.head_sha(self.reviewer), self.candidate_sha)
            self.assertEqual(
                self._git(self.reviewer, "status", "--porcelain=v1", "--untracked-files=all").stdout,
                "",
            )

            trace = json.loads((self.root / "fake-claude-trace.json").read_text(encoding="utf-8"))
            args = trace["args"]
            self.assertIn("-p", args)
            self.assertEqual(args[args.index("--output-format") + 1], "json")
            self.assertIn("--json-schema", args)
            self.assertEqual(args[args.index("--permission-mode") + 1], "plan")
            self.assertEqual(args[args.index("--tools") + 1], "Read,Grep,Glob")
            self.assertIn("--no-session-persistence", args)
            self.assertIn("--bare", args)
            self.assertEqual(args[args.index("--model") + 1], "claude-opus-5")
            self.assertNotIn("Writer says the candidate is correct.", " ".join(args))
            piped = json.loads(trace["stdin"])
            self.assertIn("UNTRUSTED_WRITER_CLAIMS", piped["review_package"])

            attempt = self.store.get_attempt(result.attempt_id)
            self.assertEqual(attempt.status, "SUCCESS")
            self.assertEqual(attempt.result["session_id"], "claude-session-123")
            self.assertEqual(attempt.result["review"]["verdict"], "APPROVE")
        asyncio.run(scenario())

    def test_request_changes_is_parsed_with_finding(self) -> None:
        async def scenario() -> None:
            result = await self.provider.run(
                task_id="task-1",
                reviewer_worktree=self.reviewer,
                generation=1,
                candidate_sha=self.candidate_sha,
                package=self._package("MODE:changes"),
                timeout_seconds=5,
            )
            self.assertEqual(result.kind, ClaudeReviewerResultKind.SUCCESS)
            self.assertEqual(result.review.verdict, ReviewVerdict.REQUEST_CHANGES)
            self.assertEqual(result.review.findings[0].severity.value, "HIGH")
        asyncio.run(scenario())

    def test_malformed_output_and_provider_unavailable_fail_closed(self) -> None:
        async def scenario() -> None:
            malformed = await self.provider.run(
                task_id="task-1",
                reviewer_worktree=self.reviewer,
                generation=1,
                candidate_sha=self.candidate_sha,
                package=self._package("MODE:malformed"),
                timeout_seconds=5,
            )
            self.assertEqual(malformed.kind, ClaudeReviewerResultKind.MALFORMED)
            self.assertIsNone(malformed.review)
        asyncio.run(scenario())

        # A fresh reviewer worktree is required for every independent invocation.
        reviewer2 = self.git.create_reviewer_worktree(
            task_id="task-1b",
            repository=self.repo,
            generation=1,
            candidate_sha=self.candidate_sha,
        )
        self.store.create_task(
            task_id="task-1b",
            repository=str(self.repo),
            baseline_ref=self.baseline_sha,
            task_spec={"repository": str(self.repo), "baseline": self.baseline_sha},
        )
        package2 = build_review_package(
            task_id="task-1b",
            generation=1,
            reviewer_worktree=reviewer2,
            baseline_sha=self.baseline_sha,
            candidate_sha=self.candidate_sha,
            task_spec={"repository": str(self.repo)},
            acceptance_criteria=["candidate is reviewed"],
            review_instructions="Review independently.",
            writer_claims=["MODE:unavailable"],
        )
        async def unavailable_scenario() -> None:
            result = await self.provider.run(
                task_id="task-1b",
                reviewer_worktree=reviewer2,
                generation=1,
                candidate_sha=self.candidate_sha,
                package=package2,
                timeout_seconds=5,
            )
            self.assertEqual(result.kind, ClaudeReviewerResultKind.PROVIDER_UNAVAILABLE)
            self.assertIsNone(result.review)
        asyncio.run(unavailable_scenario())

    def test_any_reviewer_worktree_write_invalidates_review(self) -> None:
        async def scenario() -> None:
            result = await self.provider.run(
                task_id="task-1",
                reviewer_worktree=self.reviewer,
                generation=1,
                candidate_sha=self.candidate_sha,
                package=self._package("MODE:modify"),
                timeout_seconds=5,
            )
            self.assertEqual(result.kind, ClaudeReviewerResultKind.FAILURE)
            self.assertIn("modified", result.reason)
            self.assertIsNone(result.review)
        asyncio.run(scenario())

    def test_adapter_rejects_write_tools_and_wrong_candidate(self) -> None:
        bad = ClaudeReviewerProvider(
            store=self.store,
            artifacts=self.artifacts,
            supervisor=SubprocessSupervisor(self.store),
            git=self.git,
            config=ProviderConfig(
                provider="claude",
                executable=str(self.fake),
                model="claude-opus-5",
                options={"permission_mode": "plan", "tools": "Read,Edit"},
            ),
        )
        with self.assertRaisesRegex(ValueError, "write tools"):
            bad.build_argv()

        async def scenario() -> None:
            with self.assertRaisesRegex(ValueError, "SHA mismatch"):
                await self.provider.start(
                    task_id="task-1",
                    reviewer_worktree=self.reviewer,
                    generation=1,
                    candidate_sha="0" * 40,
                    package=self._package(),
                )
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
