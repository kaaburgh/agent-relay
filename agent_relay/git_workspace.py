from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .store import Store, StoreError, TaskNotFound, utc_now


_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_SHA = re.compile(r"^[0-9a-f]{40}$")


class GitWorkspaceError(RuntimeError):
    pass


@dataclass(frozen=True)
class CandidateGeneration:
    task_id: str
    generation: int
    candidate_sha: str
    writer_attempt_id: int | None
    created_at: str


@dataclass(frozen=True)
class ManagedWorkspaces:
    repository: Path
    baseline_sha: str
    writer: Path


class GitWorkspaceManager:
    """Non-destructive Git worktree primitives for writer/reviewer isolation."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _git(
        self,
        repository: str | Path,
        *args: str,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        command = ["git", "-C", str(repository), *args]
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if check and result.returncode != 0:
            raise GitWorkspaceError(
                f"git command failed ({result.returncode}): {command!r}: {result.stderr.strip()}"
            )
        return result

    @staticmethod
    def _safe_segment(value: str, label: str) -> str:
        if not _SAFE_SEGMENT.fullmatch(value) or value in {".", ".."}:
            raise GitWorkspaceError(f"unsafe {label}: {value!r}")
        return value

    def repository_root(self, repository: str | Path) -> Path:
        path = Path(repository).expanduser()
        if not path.is_dir():
            raise GitWorkspaceError(f"repository path is not a directory: {path}")
        resolved = self._git(path, "rev-parse", "--show-toplevel").stdout.strip()
        root = Path(resolved).resolve()
        if not root.is_dir():
            raise GitWorkspaceError(f"Git returned invalid repository root: {root}")
        return root

    def resolve_ref(self, repository: str | Path, ref: str) -> str:
        if not ref.strip():
            raise GitWorkspaceError("baseline/ref must not be empty")
        sha = self._git(repository, "rev-parse", "--verify", f"{ref}^{{commit}}").stdout.strip()
        if not _SHA.fullmatch(sha):
            raise GitWorkspaceError(f"Git returned invalid commit SHA for {ref!r}: {sha!r}")
        return sha

    def create_writer_worktree(
        self,
        *,
        task_id: str,
        repository: str | Path,
        baseline_ref: str,
    ) -> ManagedWorkspaces:
        task_id = self._safe_segment(task_id, "task id")
        repo = self.repository_root(repository)
        baseline_sha = self.resolve_ref(repo, baseline_ref)
        writer = self.root / "workspaces" / task_id / "writer"
        if writer.exists():
            raise GitWorkspaceError(f"managed writer worktree already exists: {writer}")
        writer.parent.mkdir(parents=True, exist_ok=True)
        branch = f"agent-relay/{task_id}/writer"
        self._git(repo, "worktree", "add", "-b", branch, str(writer), baseline_sha)
        if self.head_sha(writer) != baseline_sha:
            raise GitWorkspaceError("writer worktree did not land on the requested baseline")
        return ManagedWorkspaces(repository=repo, baseline_sha=baseline_sha, writer=writer)

    def create_reviewer_worktree(
        self,
        *,
        task_id: str,
        repository: str | Path,
        generation: int,
        candidate_sha: str,
    ) -> Path:
        task_id = self._safe_segment(task_id, "task id")
        if generation <= 0:
            raise GitWorkspaceError("review generation must be positive")
        repo = self.repository_root(repository)
        resolved = self.resolve_ref(repo, candidate_sha)
        if resolved != candidate_sha:
            raise GitWorkspaceError("candidate SHA did not resolve exactly")
        reviewer = self.root / "workspaces" / task_id / f"reviewer-g{generation:03d}"
        if reviewer.exists():
            raise GitWorkspaceError(f"managed reviewer worktree already exists: {reviewer}")
        reviewer.parent.mkdir(parents=True, exist_ok=True)
        self._git(repo, "worktree", "add", "--detach", str(reviewer), candidate_sha)
        if self.head_sha(reviewer) != candidate_sha:
            raise GitWorkspaceError("reviewer worktree did not land on exact candidate SHA")
        return reviewer

    def head_sha(self, worktree: str | Path) -> str:
        sha = self._git(worktree, "rev-parse", "HEAD").stdout.strip()
        if not _SHA.fullmatch(sha):
            raise GitWorkspaceError(f"invalid HEAD SHA: {sha!r}")
        return sha

    def detect_candidate(self, writer: str | Path, baseline_sha: str) -> str | None:
        baseline_sha = self.resolve_ref(writer, baseline_sha)
        head = self.head_sha(writer)
        status = self._git(writer, "status", "--porcelain=v1", "--untracked-files=all").stdout
        if status.strip():
            raise GitWorkspaceError("writer worktree has uncommitted or untracked changes")
        if head == baseline_sha:
            return None
        ancestor = self._git(writer, "merge-base", "--is-ancestor", baseline_sha, head, check=False)
        if ancestor.returncode == 1:
            raise GitWorkspaceError("candidate HEAD is not a descendant of the frozen baseline")
        if ancestor.returncode != 0:
            raise GitWorkspaceError(f"cannot verify candidate ancestry: {ancestor.stderr.strip()}")
        return head


def record_candidate_generation(
    store: Store,
    *,
    task_id: str,
    candidate_sha: str,
    writer_attempt_id: int | None = None,
    expected_previous_generation: int | None = None,
) -> CandidateGeneration:
    """Freeze a committed candidate and atomically make it current for the task."""
    if not _SHA.fullmatch(candidate_sha):
        raise StoreError(f"invalid candidate SHA: {candidate_sha!r}")
    now = utc_now()
    with store._transaction():
        task = store._conn.execute(
            "SELECT stage,current_generation,current_candidate_sha FROM tasks WHERE task_id=?",
            (task_id,),
        ).fetchone()
        if task is None:
            raise TaskNotFound(task_id)
        current_generation = int(task["current_generation"])

        existing = store._conn.execute(
            "SELECT * FROM candidate_generations WHERE task_id=? AND candidate_sha=?",
            (task_id, candidate_sha),
        ).fetchone()
        if existing is not None:
            frozen = _candidate_row(existing)
            if writer_attempt_id is not None and frozen.writer_attempt_id != writer_attempt_id:
                raise StoreError(
                    "idempotent candidate freeze writer attempt does not match durable ownership"
                )
            if (
                expected_previous_generation is not None
                and frozen.generation != expected_previous_generation + 1
            ):
                raise StoreError(
                    f"stale candidate generation: expected predecessor {expected_previous_generation}, "
                    f"candidate is generation {frozen.generation}"
                )
            if (
                current_generation != frozen.generation
                or task["current_candidate_sha"] != candidate_sha
            ):
                raise StoreError(
                    "idempotent candidate freeze refers to a stale non-current candidate"
                )
            return frozen

        if expected_previous_generation is not None and current_generation != expected_previous_generation:
            raise StoreError(
                f"stale candidate generation: expected {expected_previous_generation}, found {current_generation}"
            )
        latest = store._conn.execute(
            "SELECT COALESCE(MAX(generation), 0) AS latest FROM candidate_generations WHERE task_id=?",
            (task_id,),
        ).fetchone()
        if int(latest["latest"]) != current_generation:
            raise StoreError("task candidate pointer disagrees with candidate generation history")
        if writer_attempt_id is not None:
            attempt = store._conn.execute(
                "SELECT task_id FROM attempts WHERE attempt_id=?", (writer_attempt_id,)
            ).fetchone()
            if attempt is None:
                raise StoreError(f"unknown writer attempt id: {writer_attempt_id}")
            if attempt["task_id"] != task_id:
                raise StoreError("writer attempt belongs to a different task")

        generation = current_generation + 1
        store._conn.execute(
            """
            INSERT INTO candidate_generations(task_id, generation, candidate_sha, writer_attempt_id, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (task_id, generation, candidate_sha, writer_attempt_id, now),
        )
        store._conn.execute(
            """
            UPDATE tasks SET current_candidate_sha=?, current_generation=?, updated_at=?
            WHERE task_id=?
            """,
            (candidate_sha, generation, now, task_id),
        )
        store._insert_event(
            task_id=task_id,
            event_type="candidate_commit_detected",
            stage=task["stage"],
            generation=generation,
            payload={"candidate_sha": candidate_sha, "writer_attempt_id": writer_attempt_id},
            created_at=now,
        )
    return CandidateGeneration(
        task_id=task_id,
        generation=generation,
        candidate_sha=candidate_sha,
        writer_attempt_id=writer_attempt_id,
        created_at=now,
    )


def candidate_generations(store: Store, task_id: str) -> tuple[CandidateGeneration, ...]:
    rows = store._conn.execute(
        "SELECT * FROM candidate_generations WHERE task_id=? ORDER BY generation", (task_id,)
    ).fetchall()
    return tuple(_candidate_row(row) for row in rows)


def _candidate_row(row: object) -> CandidateGeneration:
    return CandidateGeneration(
        task_id=row["task_id"],  # type: ignore[index]
        generation=int(row["generation"]),  # type: ignore[index]
        candidate_sha=row["candidate_sha"],  # type: ignore[index]
        writer_attempt_id=row["writer_attempt_id"],  # type: ignore[index]
        created_at=row["created_at"],  # type: ignore[index]
    )
