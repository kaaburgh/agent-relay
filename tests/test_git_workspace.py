from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_relay.git_workspace import (
    GitWorkspaceError,
    GitWorkspaceManager,
    candidate_generations,
    record_candidate_generation,
)
from agent_relay.store import Store


class GitWorkspaceTests(unittest.TestCase):
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
        self.baseline = self._git(self.repo, "rev-parse", "HEAD").stdout.strip()

        self.store = Store(self.root / "state.sqlite3")
        self.store.create_task(
            task_id="task-1",
            repository=str(self.repo),
            baseline_ref="main",
            task_spec={"repository": str(self.repo), "baseline": "main"},
        )
        self.manager = GitWorkspaceManager(self.root / "managed")

    def tearDown(self) -> None:
        self.store.close()

    @staticmethod
    def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def _commit_writer(self, writer: Path, content: str, message: str) -> str:
        (writer / "example.txt").write_text(content, encoding="utf-8")
        self._git(writer, "add", "example.txt")
        self._git(writer, "commit", "-m", message)
        return self._git(writer, "rev-parse", "HEAD").stdout.strip()

    def test_writer_worktree_starts_at_frozen_baseline_without_cleaning_source(self) -> None:
        (self.repo / "local-untracked.txt").write_text("keep me\n", encoding="utf-8")
        workspaces = self.manager.create_writer_worktree(
            task_id="task-1", repository=self.repo, baseline_ref="main"
        )
        self.assertEqual(workspaces.baseline_sha, self.baseline)
        self.assertEqual(self.manager.head_sha(workspaces.writer), self.baseline)
        source_status = self._git(self.repo, "status", "--porcelain").stdout
        self.assertIn("local-untracked.txt", source_status)
        self.assertFalse((workspaces.writer / "local-untracked.txt").exists())

    def test_detect_candidate_requires_committed_clean_descendant(self) -> None:
        workspaces = self.manager.create_writer_worktree(
            task_id="task-1", repository=self.repo, baseline_ref="main"
        )
        self.assertIsNone(self.manager.detect_candidate(workspaces.writer, self.baseline))
        (workspaces.writer / "partial.txt").write_text("partial\n", encoding="utf-8")
        with self.assertRaisesRegex(GitWorkspaceError, "uncommitted or untracked"):
            self.manager.detect_candidate(workspaces.writer, self.baseline)
        (workspaces.writer / "partial.txt").unlink()
        candidate = self._commit_writer(workspaces.writer, "candidate\n", "candidate")
        self.assertEqual(self.manager.detect_candidate(workspaces.writer, self.baseline), candidate)

    def test_freeze_candidate_and_reviewer_worktree_preserve_exact_generation(self) -> None:
        workspaces = self.manager.create_writer_worktree(
            task_id="task-1", repository=self.repo, baseline_ref="main"
        )
        writer_attempt = self.store.allocate_attempt(task_id="task-1", kind="writer")
        sha1 = self._commit_writer(workspaces.writer, "candidate one\n", "candidate one")
        generation1 = record_candidate_generation(
            self.store,
            task_id="task-1",
            candidate_sha=sha1,
            writer_attempt_id=writer_attempt.attempt_id,
            expected_previous_generation=0,
        )
        self.assertEqual(generation1.generation, 1)
        reviewer1 = self.manager.create_reviewer_worktree(
            task_id="task-1",
            repository=self.repo,
            generation=1,
            candidate_sha=sha1,
        )
        self.assertEqual(self.manager.head_sha(reviewer1), sha1)
        self.assertEqual(self._git(reviewer1, "symbolic-ref", "-q", "HEAD").returncode, 1)

        rework_attempt = self.store.allocate_attempt(task_id="task-1", kind="writer", generation=1)
        sha2 = self._commit_writer(workspaces.writer, "candidate two\n", "candidate two")
        generation2 = record_candidate_generation(
            self.store,
            task_id="task-1",
            candidate_sha=sha2,
            writer_attempt_id=rework_attempt.attempt_id,
            expected_previous_generation=1,
        )
        reviewer2 = self.manager.create_reviewer_worktree(
            task_id="task-1",
            repository=self.repo,
            generation=2,
            candidate_sha=sha2,
        )
        self.assertEqual(generation2.generation, 2)
        self.assertNotEqual(reviewer1, reviewer2)
        self.assertEqual(self.manager.head_sha(reviewer1), sha1)
        self.assertEqual(self.manager.head_sha(reviewer2), sha2)
        self.assertEqual(
            [(item.generation, item.candidate_sha) for item in candidate_generations(self.store, "task-1")],
            [(1, sha1), (2, sha2)],
        )
        current = self.store.get_task("task-1")
        self.assertEqual(current.current_generation, 2)
        self.assertEqual(current.current_candidate_sha, sha2)

    def test_record_candidate_is_idempotent_for_same_sha(self) -> None:
        workspaces = self.manager.create_writer_worktree(
            task_id="task-1", repository=self.repo, baseline_ref="main"
        )
        sha = self._commit_writer(workspaces.writer, "candidate\n", "candidate")
        first = record_candidate_generation(
            self.store,
            task_id="task-1",
            candidate_sha=sha,
            expected_previous_generation=0,
        )
        event_count = len(self.store.events("task-1"))
        second = record_candidate_generation(
            self.store,
            task_id="task-1",
            candidate_sha=sha,
            expected_previous_generation=0,
        )
        self.assertEqual(first, second)
        self.assertEqual(len(self.store.events("task-1")), event_count)
        self.assertEqual(len(candidate_generations(self.store, "task-1")), 1)

    def test_managed_worktrees_fail_closed_instead_of_overwriting_existing_paths(self) -> None:
        workspaces = self.manager.create_writer_worktree(
            task_id="task-1", repository=self.repo, baseline_ref="main"
        )
        with self.assertRaisesRegex(GitWorkspaceError, "already exists"):
            self.manager.create_writer_worktree(
                task_id="task-1", repository=self.repo, baseline_ref="main"
            )
        sha = self._commit_writer(workspaces.writer, "candidate\n", "candidate")
        self.manager.create_reviewer_worktree(
            task_id="task-1", repository=self.repo, generation=1, candidate_sha=sha
        )
        with self.assertRaisesRegex(GitWorkspaceError, "already exists"):
            self.manager.create_reviewer_worktree(
                task_id="task-1", repository=self.repo, generation=1, candidate_sha=sha
            )


if __name__ == "__main__":
    unittest.main()
