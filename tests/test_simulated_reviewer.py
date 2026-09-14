from __future__ import annotations

import asyncio
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_relay.artifacts import ArtifactManager
from agent_relay.git_workspace import GitWorkspaceManager
from agent_relay.review import ReviewParseError, parse_review_output
from agent_relay.simulated_reviewer import ReviewerResultKind, SimulatedReviewerProvider
from agent_relay.store import Store
from agent_relay.supervisor import SubprocessSupervisor
from agent_relay.workflow import ReviewVerdict


APPROVE = {
    "verdict": "APPROVE",
    "findings": [],
    "summary": "candidate is acceptable",
}
REQUEST_CHANGES = {
    "verdict": "REQUEST_CHANGES",
    "findings": [
        {
            "severity": "HIGH",
            "title": "broken invariant",
            "problem": "candidate loses evidence",
            "required_action": "preserve evidence",
        }
    ],
    "summary": "changes required",
}


class ReviewContractTests(unittest.TestCase):
    def test_noisy_single_object_is_accepted(self) -> None:
        review = parse_review_output("prefix\n" + __import__("json").dumps(APPROVE) + "\nsuffix")
        self.assertEqual(review.verdict, ReviewVerdict.APPROVE)

    def test_request_changes_requires_findings(self) -> None:
        with self.assertRaises(ReviewParseError):
            parse_review_output('{"verdict":"REQUEST_CHANGES","findings":[],"summary":"no"}')

    def test_unknown_severity_and_multiple_valid_objects_fail_closed(self) -> None:
        bad = dict(REQUEST_CHANGES)
        bad["findings"] = [dict(REQUEST_CHANGES["findings"][0], severity="UNKNOWN")]
        with self.assertRaises(ReviewParseError):
            parse_review_output(__import__("json").dumps(bad))
        text = __import__("json").dumps(APPROVE) + "\n" + __import__("json").dumps(APPROVE)
        with self.assertRaises(ReviewParseError):
            parse_review_output(text)


class SimulatedReviewerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.repo = self.root / "source"
        self.repo.mkdir()
        self._git(self.repo, "init", "-b", "main")
        self._git(self.repo, "config", "user.email", "agent-relay@example.invalid")
        self._git(self.repo, "config", "user.name", "Agent Relay Test")
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
        writer = self.git.create_writer_worktree(task_id="task-1", repository=self.repo, baseline_ref="main")
        (writer.writer / "example.txt").write_text("candidate\n", encoding="utf-8")
        self._git(writer.writer, "add", "example.txt")
        self._git(writer.writer, "commit", "-m", "candidate")
        self.candidate_sha = self._git(writer.writer, "rev-parse", "HEAD").stdout.strip()
        self.reviewer = self.git.create_reviewer_worktree(
            task_id="task-1", repository=self.repo, generation=1, candidate_sha=self.candidate_sha
        )
        self.artifacts = ArtifactManager(self.root / "artifacts", self.store)
        self.provider = SimulatedReviewerProvider(
            store=self.store,
            artifacts=self.artifacts,
            supervisor=SubprocessSupervisor(self.store),
            git=self.git,
        )

    def tearDown(self) -> None:
        self.store.close()

    @staticmethod
    def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)

    def test_fresh_invocations_parse_approve_and_request_changes(self) -> None:
        async def scenario() -> None:
            one = await self.provider.run(
                task_id="task-1", reviewer_worktree=self.reviewer, generation=1,
                candidate_sha=self.candidate_sha, behavior=[{"action": "review", "value": APPROVE}],
            )
            two = await self.provider.run(
                task_id="task-1", reviewer_worktree=self.reviewer, generation=1,
                candidate_sha=self.candidate_sha, behavior=[{"action": "review", "value": REQUEST_CHANGES}],
            )
            self.assertEqual(one.kind, ReviewerResultKind.SUCCESS)
            self.assertEqual(two.kind, ReviewerResultKind.SUCCESS)
            self.assertEqual(one.review.verdict, ReviewVerdict.APPROVE)
            self.assertEqual(two.review.verdict, ReviewVerdict.REQUEST_CHANGES)
            self.assertNotEqual(one.invocation_id, two.invocation_id)
            self.assertNotEqual(one.attempt_id, two.attempt_id)
        asyncio.run(scenario())

    def test_malformed_never_becomes_approval(self) -> None:
        async def scenario() -> None:
            result = await self.provider.run(
                task_id="task-1", reviewer_worktree=self.reviewer, generation=1,
                candidate_sha=self.candidate_sha,
                behavior=[{"action": "raw", "text": "not-json APPROVE"}],
            )
            self.assertEqual(result.kind, ReviewerResultKind.MALFORMED)
            self.assertIsNone(result.review)
        asyncio.run(scenario())

    def test_provider_unavailable_failure_crash_and_hang_are_distinct(self) -> None:
        async def scenario() -> None:
            unavailable = await self.provider.run(
                task_id="task-1", reviewer_worktree=self.reviewer, generation=1,
                candidate_sha=self.candidate_sha,
                behavior=[{"action": "provider_unavailable", "reason": "rate limit"}],
            )
            failure = await self.provider.run(
                task_id="task-1", reviewer_worktree=self.reviewer, generation=1,
                candidate_sha=self.candidate_sha,
                behavior=[{"action": "failure", "reason": "review failed"}],
            )
            crash = await self.provider.run(
                task_id="task-1", reviewer_worktree=self.reviewer, generation=1,
                candidate_sha=self.candidate_sha, behavior=[{"action": "crash"}],
            )
            hang = await self.provider.run(
                task_id="task-1", reviewer_worktree=self.reviewer, generation=1,
                candidate_sha=self.candidate_sha, behavior=[{"action": "hang"}], timeout_seconds=0.12,
            )
            self.assertEqual(unavailable.kind, ReviewerResultKind.PROVIDER_UNAVAILABLE)
            self.assertEqual(failure.kind, ReviewerResultKind.PROCESS_FAILURE)
            self.assertEqual(crash.kind, ReviewerResultKind.PROCESS_FAILURE)
            self.assertEqual(hang.kind, ReviewerResultKind.PROCESS_FAILURE)
            self.assertEqual(hang.process_result.state, "TIMED_OUT")
        asyncio.run(scenario())

    def test_exact_candidate_sha_is_checked_before_launch(self) -> None:
        async def scenario() -> None:
            with self.assertRaisesRegex(ValueError, "SHA mismatch"):
                await self.provider.start(
                    task_id="task-1", reviewer_worktree=self.reviewer, generation=1,
                    candidate_sha="0" * 40, behavior=[{"action": "review", "value": APPROVE}],
                )
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
