from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping


SCHEMA_VERSION = 2


class StoreError(RuntimeError):
    """Base durable-store error."""


class UnsupportedSchemaVersion(StoreError):
    pass


class TaskNotFound(StoreError):
    pass


@dataclass(frozen=True)
class TaskRow:
    task_id: str
    repository: str
    baseline_ref: str
    stage: str
    stage_attempt: int
    current_candidate_sha: str | None
    current_generation: int
    task_json: Mapping[str, Any]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class EventRow:
    sequence: int
    task_id: str
    event_type: str
    stage: str | None
    generation: int | None
    payload: Mapping[str, Any]
    created_at: str


@dataclass(frozen=True)
class AttemptRow:
    attempt_id: int
    task_id: str
    kind: str
    attempt_no: int
    generation: int | None
    status: str
    command: Any
    pid: int | None
    artifact_dir: str
    result: Any
    started_at: str | None
    ended_at: str | None
    exit_status: int | None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, sort_keys=True, separators=(",", ":"))


def _loads(value: str | None) -> Any:
    if value is None:
        return None
    return json.loads(value)


class Store:
    """SQLite-backed durable state and append-only semantic event history."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, isolation_level=None, timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA busy_timeout = 30000")
        self._migrate()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    @property
    def schema_version(self) -> int:
        return int(self._conn.execute("PRAGMA user_version").fetchone()[0])

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        if self._conn.in_transaction:
            raise StoreError("nested transactions are not supported")
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()

    def _run_migration_script(self, script: str) -> None:
        try:
            self._conn.executescript(script)
        except BaseException:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise

    def _migrate(self) -> None:
        version = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise UnsupportedSchemaVersion(
                f"database schema {version} is newer than supported {SCHEMA_VERSION}"
            )
        if version == 0:
            self._migrate_0_to_1()
            version = 1
        if version == 1:
            self._migrate_1_to_2()
            version = 2
        if version != SCHEMA_VERSION:
            raise UnsupportedSchemaVersion(
                f"database schema {version} cannot be migrated to {SCHEMA_VERSION}"
            )

    def _migrate_0_to_1(self) -> None:
        self._run_migration_script(
            """
            BEGIN IMMEDIATE;

            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                repository TEXT NOT NULL,
                baseline_ref TEXT NOT NULL,
                stage TEXT NOT NULL,
                stage_attempt INTEGER NOT NULL DEFAULT 0 CHECK(stage_attempt >= 0),
                current_candidate_sha TEXT,
                current_generation INTEGER NOT NULL DEFAULT 0 CHECK(current_generation >= 0),
                task_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS attempts (
                attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
                kind TEXT NOT NULL,
                attempt_no INTEGER NOT NULL CHECK(attempt_no > 0),
                generation INTEGER CHECK(generation IS NULL OR generation > 0),
                status TEXT NOT NULL,
                command_json TEXT,
                pid INTEGER,
                artifact_dir TEXT,
                result_json TEXT,
                started_at TEXT,
                ended_at TEXT,
                exit_status INTEGER,
                UNIQUE(task_id, kind, attempt_no)
            );

            CREATE TABLE IF NOT EXISTS candidate_generations (
                task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
                generation INTEGER NOT NULL CHECK(generation > 0),
                candidate_sha TEXT NOT NULL,
                writer_attempt_id INTEGER REFERENCES attempts(attempt_id) ON DELETE RESTRICT,
                created_at TEXT NOT NULL,
                PRIMARY KEY(task_id, generation),
                UNIQUE(task_id, candidate_sha)
            );

            CREATE TABLE IF NOT EXISTS processes (
                process_id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
                attempt_id INTEGER REFERENCES attempts(attempt_id) ON DELETE RESTRICT,
                pid INTEGER NOT NULL,
                process_group_id INTEGER,
                state TEXT NOT NULL,
                command_json TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                exit_status INTEGER,
                last_liveness_at TEXT
            );

            CREATE TABLE IF NOT EXISTS artifacts (
                artifact_id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
                attempt_id INTEGER REFERENCES attempts(attempt_id) ON DELETE RESTRICT,
                kind TEXT NOT NULL,
                path TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                UNIQUE(task_id, attempt_id, kind, path)
            );

            CREATE TABLE IF NOT EXISTS provider_waits (
                task_id TEXT PRIMARY KEY REFERENCES tasks(task_id) ON DELETE RESTRICT,
                provider TEXT NOT NULL,
                reason TEXT NOT NULL,
                first_seen TEXT NOT NULL,
                last_attempt TEXT NOT NULL,
                next_retry TEXT,
                attempt_count INTEGER NOT NULL CHECK(attempt_count > 0)
            );

            CREATE TABLE IF NOT EXISTS validations (
                validation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
                generation INTEGER NOT NULL CHECK(generation > 0),
                attempt_id INTEGER REFERENCES attempts(attempt_id) ON DELETE RESTRICT,
                candidate_sha TEXT NOT NULL,
                status TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(task_id, generation, attempt_id)
            );

            CREATE TABLE IF NOT EXISTS reviews (
                review_id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
                generation INTEGER NOT NULL CHECK(generation > 0),
                attempt_id INTEGER REFERENCES attempts(attempt_id) ON DELETE RESTRICT,
                candidate_sha TEXT NOT NULL,
                run_id TEXT,
                verdict TEXT,
                findings_json TEXT,
                summary TEXT,
                raw_result_path TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(task_id, generation, attempt_id)
            );

            CREATE TABLE IF NOT EXISTS resources (
                resource_name TEXT PRIMARY KEY,
                capacity INTEGER NOT NULL CHECK(capacity > 0)
            );

            CREATE TABLE IF NOT EXISTS leases (
                lease_id INTEGER PRIMARY KEY AUTOINCREMENT,
                resource_name TEXT NOT NULL REFERENCES resources(resource_name) ON DELETE RESTRICT,
                holder_id TEXT NOT NULL,
                task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
                attempt_id INTEGER REFERENCES attempts(attempt_id) ON DELETE RESTRICT,
                acquired_at TEXT NOT NULL,
                heartbeat_at TEXT NOT NULL,
                released_at TEXT,
                UNIQUE(resource_name, holder_id)
            );

            CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
                event_type TEXT NOT NULL CHECK(length(event_type) > 0),
                stage TEXT,
                generation INTEGER,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_events_task_sequence
                ON events(task_id, sequence);
            CREATE INDEX IF NOT EXISTS idx_attempts_task_kind
                ON attempts(task_id, kind, attempt_no);
            CREATE INDEX IF NOT EXISTS idx_processes_task_state
                ON processes(task_id, state);
            CREATE INDEX IF NOT EXISTS idx_leases_active_resource
                ON leases(resource_name, released_at);

            CREATE TRIGGER IF NOT EXISTS events_are_append_only_update
            BEFORE UPDATE ON events
            BEGIN
                SELECT RAISE(ABORT, 'events are append-only');
            END;

            CREATE TRIGGER IF NOT EXISTS events_are_append_only_delete
            BEFORE DELETE ON events
            BEGIN
                SELECT RAISE(ABORT, 'events are append-only');
            END;

            PRAGMA user_version = 1;
            COMMIT;
            """
        )

    def _migrate_1_to_2(self) -> None:
        self._run_migration_script(
            """
            BEGIN IMMEDIATE;

            CREATE TRIGGER IF NOT EXISTS attempts_identity_is_immutable
            BEFORE UPDATE OF task_id, kind, attempt_no, generation, command_json, artifact_dir ON attempts
            BEGIN
                SELECT RAISE(ABORT, 'attempt identity is immutable');
            END;

            CREATE TRIGGER IF NOT EXISTS attempts_cannot_be_deleted
            BEFORE DELETE ON attempts
            BEGIN
                SELECT RAISE(ABORT, 'attempt history is immutable');
            END;

            CREATE TRIGGER IF NOT EXISTS artifacts_are_append_only_update
            BEFORE UPDATE ON artifacts
            BEGIN
                SELECT RAISE(ABORT, 'artifacts are append-only');
            END;

            CREATE TRIGGER IF NOT EXISTS artifacts_are_append_only_delete
            BEFORE DELETE ON artifacts
            BEGIN
                SELECT RAISE(ABORT, 'artifacts are append-only');
            END;

            PRAGMA user_version = 2;
            COMMIT;
            """
        )

    def create_task(
        self,
        *,
        task_id: str,
        repository: str,
        baseline_ref: str,
        task_spec: Mapping[str, Any],
    ) -> TaskRow:
        now = utc_now()
        with self._transaction():
            self._conn.execute(
                """
                INSERT INTO tasks(
                    task_id, repository, baseline_ref, stage, stage_attempt,
                    current_candidate_sha, current_generation, task_json, created_at, updated_at
                ) VALUES (?, ?, ?, 'READY', 0, NULL, 0, ?, ?, ?)
                """,
                (task_id, repository, baseline_ref, _json(task_spec), now, now),
            )
            self._insert_event(
                task_id=task_id,
                event_type="task_created",
                stage="READY",
                generation=None,
                payload={"repository": repository, "baseline_ref": baseline_ref},
                created_at=now,
            )
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> TaskRow:
        row = self._conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            raise TaskNotFound(task_id)
        return self._task_row(row)

    def update_task_with_event(
        self,
        *,
        task_id: str,
        stage: str,
        stage_attempt: int,
        event_type: str | None,
        event_payload: Mapping[str, Any] | None = None,
        candidate_sha: str | None = None,
        generation: int | None = None,
        expected_stage: str | None = None,
        expected_candidate_sha: str | None = None,
        expected_generation: int | None = None,
        enforce_expected_candidate: bool = False,
    ) -> TaskRow:
        now = utc_now()
        with self._transaction():
            row = self._conn.execute(
                "SELECT stage, current_candidate_sha, current_generation FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise TaskNotFound(task_id)
            if expected_stage is not None and row["stage"] != expected_stage:
                raise StoreError(
                    f"stale task state: expected stage {expected_stage}, found {row['stage']}"
                )
            if expected_generation is not None and int(row["current_generation"]) != expected_generation:
                raise StoreError(
                    "stale task state: candidate generation changed before transition commit"
                )
            if enforce_expected_candidate and row["current_candidate_sha"] != expected_candidate_sha:
                raise StoreError("stale task state: candidate SHA changed before transition commit")
            next_generation = int(row["current_generation"]) if generation is None else generation
            next_candidate = row["current_candidate_sha"] if candidate_sha is None else candidate_sha
            updated = self._conn.execute(
                """
                UPDATE tasks
                SET stage = ?, stage_attempt = ?, current_candidate_sha = ?,
                    current_generation = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (stage, stage_attempt, next_candidate, next_generation, now, task_id),
            )
            if updated.rowcount != 1:
                raise TaskNotFound(task_id)
            self._insert_event(
                task_id=task_id,
                event_type=event_type,  # type: ignore[arg-type]
                stage=stage,
                generation=next_generation or None,
                payload=event_payload,
                created_at=now,
            )
        return self.get_task(task_id)

    def allocate_attempt(
        self,
        *,
        task_id: str,
        kind: str,
        generation: int | None = None,
        command: Any = None,
    ) -> AttemptRow:
        if not kind:
            raise StoreError("attempt kind must not be empty")
        now = utc_now()
        with self._transaction():
            exists = self._conn.execute(
                "SELECT 1 FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if exists is None:
                raise TaskNotFound(task_id)
            row = self._conn.execute(
                "SELECT COALESCE(MAX(attempt_no), 0) + 1 AS next_no FROM attempts WHERE task_id = ? AND kind = ?",
                (task_id, kind),
            ).fetchone()
            attempt_no = int(row["next_no"])
            artifact_dir = f"attempts/{kind}-{attempt_no:03d}"
            cursor = self._conn.execute(
                """
                INSERT INTO attempts(
                    task_id, kind, attempt_no, generation, status, command_json,
                    artifact_dir, started_at
                ) VALUES (?, ?, ?, ?, 'CREATED', ?, ?, ?)
                """,
                (task_id, kind, attempt_no, generation, _json(command), artifact_dir, now),
            )
            attempt_id = int(cursor.lastrowid)
        return self.get_attempt(attempt_id)

    def get_attempt(self, attempt_id: int) -> AttemptRow:
        row = self._conn.execute(
            "SELECT * FROM attempts WHERE attempt_id = ?", (attempt_id,)
        ).fetchone()
        if row is None:
            raise StoreError(f"unknown attempt id: {attempt_id}")
        return self._attempt_row(row)

    def attempts(self, task_id: str, kind: str | None = None) -> tuple[AttemptRow, ...]:
        if kind is None:
            rows = self._conn.execute(
                "SELECT * FROM attempts WHERE task_id = ? ORDER BY attempt_id", (task_id,)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM attempts WHERE task_id = ? AND kind = ? ORDER BY attempt_no",
                (task_id, kind),
            ).fetchall()
        return tuple(self._attempt_row(row) for row in rows)

    def finish_attempt(
        self,
        *,
        attempt_id: int,
        status: str,
        result: Any,
        exit_status: int | None = None,
    ) -> AttemptRow:
        now = utc_now()
        with self._transaction():
            cursor = self._conn.execute(
                """
                UPDATE attempts
                SET status = ?, result_json = ?, ended_at = ?, exit_status = ?
                WHERE attempt_id = ? AND ended_at IS NULL
                """,
                (status, _json(result), now, exit_status, attempt_id),
            )
            if cursor.rowcount != 1:
                row = self._conn.execute(
                    "SELECT 1 FROM attempts WHERE attempt_id = ?", (attempt_id,)
                ).fetchone()
                if row is None:
                    raise StoreError(f"unknown attempt id: {attempt_id}")
                raise StoreError(f"attempt {attempt_id} is already finalized")
        return self.get_attempt(attempt_id)

    def register_artifact(
        self,
        *,
        task_id: str,
        attempt_id: int,
        kind: str,
        path: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> int:
        with self._transaction():
            cursor = self._conn.execute(
                """
                INSERT INTO artifacts(task_id, attempt_id, kind, path, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (task_id, attempt_id, kind, path, _json(metadata), utc_now()),
            )
            return int(cursor.lastrowid)

    def append_event(
        self,
        *,
        task_id: str,
        event_type: str,
        stage: str | None = None,
        generation: int | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> EventRow:
        with self._transaction():
            sequence = self._insert_event(
                task_id=task_id,
                event_type=event_type,
                stage=stage,
                generation=generation,
                payload=payload,
                created_at=utc_now(),
            )
        return self._event_row(
            self._conn.execute("SELECT * FROM events WHERE sequence = ?", (sequence,)).fetchone()
        )

    def events(self, task_id: str) -> tuple[EventRow, ...]:
        rows = self._conn.execute(
            "SELECT * FROM events WHERE task_id = ? ORDER BY sequence ASC", (task_id,)
        ).fetchall()
        return tuple(self._event_row(row) for row in rows)

    def _insert_event(
        self,
        *,
        task_id: str,
        event_type: str,
        stage: str | None,
        generation: int | None,
        payload: Mapping[str, Any] | None,
        created_at: str,
    ) -> int:
        cursor = self._conn.execute(
            """
            INSERT INTO events(task_id, event_type, stage, generation, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (task_id, event_type, stage, generation, _json(payload), created_at),
        )
        return int(cursor.lastrowid)

    @staticmethod
    def _task_row(row: sqlite3.Row) -> TaskRow:
        return TaskRow(
            task_id=row["task_id"],
            repository=row["repository"],
            baseline_ref=row["baseline_ref"],
            stage=row["stage"],
            stage_attempt=int(row["stage_attempt"]),
            current_candidate_sha=row["current_candidate_sha"],
            current_generation=int(row["current_generation"]),
            task_json=json.loads(row["task_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _event_row(row: sqlite3.Row) -> EventRow:
        return EventRow(
            sequence=int(row["sequence"]),
            task_id=row["task_id"],
            event_type=row["event_type"],
            stage=row["stage"],
            generation=row["generation"],
            payload=json.loads(row["payload_json"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _attempt_row(row: sqlite3.Row) -> AttemptRow:
        return AttemptRow(
            attempt_id=int(row["attempt_id"]),
            task_id=row["task_id"],
            kind=row["kind"],
            attempt_no=int(row["attempt_no"]),
            generation=row["generation"],
            status=row["status"],
            command=_loads(row["command_json"]),
            pid=row["pid"],
            artifact_dir=row["artifact_dir"],
            result=_loads(row["result_json"]),
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            exit_status=row["exit_status"],
        )
