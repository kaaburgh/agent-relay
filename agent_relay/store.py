from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping


SCHEMA_VERSION = 1


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


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _json(value: Mapping[str, Any] | None) -> str:
    return json.dumps(value or {}, sort_keys=True, separators=(",", ":"))


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

    def _migrate(self) -> None:
        version = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise UnsupportedSchemaVersion(
                f"database schema {version} is newer than supported {SCHEMA_VERSION}"
            )
        if version == SCHEMA_VERSION:
            return
        try:
            self._conn.executescript(
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
        except BaseException:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise

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
    ) -> TaskRow:
        now = utc_now()
        with self._transaction():
            row = self._conn.execute(
                "SELECT current_candidate_sha, current_generation FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise TaskNotFound(task_id)
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
