from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_relay.artifacts import ArtifactManager
from agent_relay.git_workspace import GitWorkspaceManager
from agent_relay.simulated_writer import SimulatedWriterProvider, WriterResultKind
from agent_relay.store import Store
from agent_relay.supervisor import SubprocessSupervisor


class SimulatedWriterTests(unittest.TestCase):
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
            task_spec={"repository": str(self.repo)},
        )
        self.git = GitWorkspaceManager(self.root / "managed")
        self.workspaces = self.git.create_writer_worktree(
            task_id="task-1", repository=self.repo, baseline_ref="main"
        )
        self.artifacts = ArtifactManager(self.root / "state", self.store)
        self.supervisor = SubprocessSupervisor(self.store)
        self.provider = SimulatedWriterProvider(
            store=self.store,
            artifacts=self.artifacts,
            supervisor=self.supervisor,
            git=self.git,
        )

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

    def _run(self, behavior: list[dict], *, timeout: float | None = 2.0):
        return asyncio.run(
            self.provider.run(
                task_id="task-1",
                writer_worktree=self.workspaces.writer,
                baseline_sha=self.baseline,
                behavior=behavior,
                timeout_seconds=timeout,
            )
        )

    def test_sleep_modify_commit_success_produces_real_candidate(self) -> None:
        result = self._run(
            [
                {"sleep": 0.02},
                {"modify_file": {"path": "src/example.py", "content": "VALUE = 1\n"}},
                {"commit": {"message": "candidate"}},
                {"result": {"status": "success", "handoff": "implemented"}},
            ]
        )
        self.assertEqual(result.kind, WriterResultKind.SUCCESS)
        self.assertIsNotNone(result.candidate_sha)
        self.assertEqual(result.candidate_sha, self.git.head_sha(self.workspaces.writer))
        self.assertNotEqual(result.candidate_sha, self.baseline)
        self.assertEqual(result.payload["handoff"], "implemented")
        self.assertEqual(self.store.get_attempt(result.attempt_id).status, "SUCCESS")

    def test_success_without_commit_is_explicit_and_does_not_invent_candidate(self) -> None:
        result = self._run([{"result": {"status": "success"}}])
        self.assertEqual(result.kind, WriterResultKind.SUCCESS)
        self.assertIsNone(result.candidate_sha)
        self.assertEqual(self.git.head_sha(self.workspaces.writer), self.baseline)

    def test_declared_failure_is_distinct_from_process_crash(self) -> None:
        result = self._run([{"result": {"status": "failure", "reason": "simulated rejection"}}])
        self.assertEqual(result.kind, WriterResultKind.FAILURE)
        self.assertIn("simulated rejection", result.reason)
        self.assertEqual(result.process_result.returncode, 0)

    def test_crash_after_partial_work_preserves_dirty_work_and_is_process_failure(self) -> None:
        result = self._run(
            [
                {"modify_file": {"path": "partial.txt", "content": "partial\n"}},
                {"crash": 37},
            ]
        )
        self.assertEqual(result.kind, WriterResultKind.PROCESS_FAILURE)
        self.assertEqual(result.process_result.returncode, 37)
        self.assertEqual((self.workspaces.writer / "partial.txt").read_text(), "partial\n")
        self.assertIn("partial.txt", self._git(self.workspaces.writer, "status", "--porcelain").stdout)

    def test_malformed_successful_result_never_becomes_success(self) -> None:
        result = self._run([{"malformed_result": "{definitely-not-json"}])
        self.assertEqual(result.kind, WriterResultKind.MALFORMED)
        self.assertEqual(result.process_result.returncode, 0)
        self.assertIsNone(result.candidate_sha)

    def test_provider_unavailable_and_rate_limit_have_normalized_classification(self) -> None:
        for operation in ("provider_unavailable", "rate_limit"):
            with self.subTest(operation=operation):
                result = self._run([{operation: f"{operation}-reason"}])
                self.assertEqual(result.kind, WriterResultKind.PROVIDER_UNAVAILABLE)
                self.assertIn(operation, result.reason)
                self.assertEqual(result.payload["signal"], operation)

    def test_hang_is_cleaned_by_supervisor_timeout(self) -> None:
        result = self._run([{"hang": True}], timeout=0.15)
        self.assertEqual(result.kind, WriterResultKind.PROCESS_FAILURE)
        self.assertEqual(result.process_result.state, "TIMED_OUT")
        self.assertTrue(result.process_result.timed_out)

    def test_worker_can_finish_commit_and_checkpoint_without_provider_callback(self) -> None:
        script = self.root / "standalone-behavior.json"
        result_path = self.root / "standalone-provider-result.json"
        script.write_text(
            json.dumps(
                [
                    {"sleep": 0.05},
                    {"modify_file": {"path": "standalone.txt", "content": "survives callback loss\n"}},
                    {"commit": {"message": "standalone candidate"}},
                    {"result": {"status": "success", "handoff": "durable"}},
                ]
            ),
            encoding="utf-8",
        )
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "agent_relay.simulated_writer_worker",
                "--script",
                str(script),
                "--worktree",
                str(self.workspaces.writer),
                "--result",
                str(result_path),
            ],
            cwd=self.workspaces.writer,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.assertEqual(process.wait(timeout=5), 0)
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["handoff"], "durable")
        candidate = self.git.detect_candidate(self.workspaces.writer, self.baseline)
        self.assertIsNotNone(candidate)
        self.assertTrue((self.workspaces.writer / "standalone.txt").exists())


if __name__ == "__main__":
    unittest.main()
