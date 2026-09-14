from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .store import Store, StoreError
from .workflow import WorkflowStage


_ACTIVE = {"WORK", "VALIDATE", "REVIEW", "REWORK"}


@dataclass(frozen=True)
class ProviderWait:
    task_id: str
    provider: str
    reason: str
    first_seen: str
    last_attempt: str
    next_retry: str
    attempt_count: int
    resume_stage: str


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _resume_stage_from_history(store: Store, task_id: str) -> str:
    row = store._conn.execute(
        """
        SELECT payload_json FROM events
        WHERE task_id=? AND event_type='provider_unavailable'
        ORDER BY sequence DESC LIMIT 1
        """,
        (task_id,),
    ).fetchone()
    if row is None:
        raise StoreError("WAITING_PROVIDER task has no provider_unavailable history")
    payload = json.loads(row["payload_json"])
    source = payload.get("from")
    if source not in _ACTIVE:
        raise StoreError(f"provider wait history has invalid resume stage: {source!r}")
    return str(source)


def get_provider_wait(store: Store, task_id: str) -> ProviderWait | None:
    row = store._conn.execute("SELECT * FROM provider_waits WHERE task_id=?", (task_id,)).fetchone()
    if row is None:
        return None
    return ProviderWait(
        task_id=task_id,
        provider=row["provider"],
        reason=row["reason"],
        first_seen=row["first_seen"],
        last_attempt=row["last_attempt"],
        next_retry=row["next_retry"],
        attempt_count=int(row["attempt_count"]),
        resume_stage=_resume_stage_from_history(store, task_id),
    )


def record_provider_unavailable(
    store: Store,
    *,
    task_id: str,
    provider: str,
    reason: str,
    now: datetime | None = None,
    base_delay_seconds: float = 30.0,
    max_delay_seconds: float = 900.0,
) -> ProviderWait:
    if not provider.strip():
        raise ValueError("provider must not be empty")
    if base_delay_seconds <= 0 or max_delay_seconds <= 0 or base_delay_seconds > max_delay_seconds:
        raise ValueError("retry delays must be positive and base <= max")
    instant = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    now_text = _iso(instant)
    with store._transaction():
        task = store._conn.execute(
            "SELECT stage,stage_attempt,current_generation,current_candidate_sha FROM tasks WHERE task_id=?",
            (task_id,),
        ).fetchone()
        if task is None:
            raise StoreError(f"unknown task: {task_id}")
        source = task["stage"]
        if source not in _ACTIVE:
            raise StoreError(f"provider unavailability can only interrupt an active stage, found {source}")
        existing = store._conn.execute(
            "SELECT * FROM provider_waits WHERE task_id=?", (task_id,)
        ).fetchone()
        count = 1 if existing is None else int(existing["attempt_count"]) + 1
        first_seen = now_text if existing is None else existing["first_seen"]
        delay = min(max_delay_seconds, base_delay_seconds * (2 ** (count - 1)))
        next_retry = _iso(instant + timedelta(seconds=delay))
        store._conn.execute(
            """
            INSERT INTO provider_waits(task_id,provider,reason,first_seen,last_attempt,next_retry,attempt_count)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(task_id) DO UPDATE SET
                provider=excluded.provider,
                reason=excluded.reason,
                last_attempt=excluded.last_attempt,
                next_retry=excluded.next_retry,
                attempt_count=excluded.attempt_count
            """,
            (task_id, provider, reason, first_seen, now_text, next_retry, count),
        )
        cursor = store._conn.execute(
            "UPDATE tasks SET stage='WAITING_PROVIDER', updated_at=? WHERE task_id=? AND stage=?",
            (now_text, task_id, source),
        )
        if cursor.rowcount != 1:
            raise StoreError("task stage changed before provider wait could be committed")
        store._insert_event(
            task_id=task_id,
            event_type="provider_unavailable",
            stage="WAITING_PROVIDER",
            generation=int(task["current_generation"]) or None,
            payload={"from": source, "to": "WAITING_PROVIDER", "provider": provider, "reason": reason, "attempt_count": count},
            created_at=now_text,
        )
        store._insert_event(
            task_id=task_id,
            event_type="retry_scheduled",
            stage="WAITING_PROVIDER",
            generation=int(task["current_generation"]) or None,
            payload={"provider": provider, "next_retry": next_retry, "attempt_count": count, "delay_seconds": delay},
            created_at=now_text,
        )
    return ProviderWait(task_id, provider, reason, first_seen, now_text, next_retry, count, source)


def resume_provider_if_due(
    store: Store,
    *,
    task_id: str,
    now: datetime | None = None,
) -> bool:
    instant = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    with store._transaction():
        task = store._conn.execute(
            "SELECT stage,stage_attempt,current_generation FROM tasks WHERE task_id=?", (task_id,)
        ).fetchone()
        if task is None:
            raise StoreError(f"unknown task: {task_id}")
        if task["stage"] != WorkflowStage.WAITING_PROVIDER.value:
            return False
        wait = store._conn.execute("SELECT * FROM provider_waits WHERE task_id=?", (task_id,)).fetchone()
        if wait is None:
            raise StoreError("WAITING_PROVIDER task has no provider wait metadata")
        if wait["next_retry"] is None or instant < _parse(wait["next_retry"]):
            return False
        resume_stage = _resume_stage_from_history(store, task_id)
        now_text = _iso(instant)
        cursor = store._conn.execute(
            "UPDATE tasks SET stage=?, stage_attempt=?, updated_at=? WHERE task_id=? AND stage='WAITING_PROVIDER'",
            (resume_stage, int(task["stage_attempt"]) + 1, now_text, task_id),
        )
        if cursor.rowcount != 1:
            raise StoreError("task stage changed before provider retry could be committed")
        store._insert_event(
            task_id=task_id,
            event_type="provider_retry_started",
            stage=resume_stage,
            generation=int(task["current_generation"]) or None,
            payload={"from": "WAITING_PROVIDER", "to": resume_stage, "provider": wait["provider"], "attempt_count": int(wait["attempt_count"])},
            created_at=now_text,
        )
    return True


def clear_provider_wait(
    store: Store,
    *,
    task_id: str,
    provider: str,
    now: datetime | None = None,
) -> bool:
    instant = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    with store._transaction():
        row = store._conn.execute(
            "SELECT provider,attempt_count FROM provider_waits WHERE task_id=?", (task_id,)
        ).fetchone()
        if row is None:
            return False
        if row["provider"] != provider:
            raise StoreError(
                f"provider wait belongs to {row['provider']!r}, cannot clear it as {provider!r}"
            )
        store._conn.execute("DELETE FROM provider_waits WHERE task_id=?", (task_id,))
        task = store._conn.execute(
            "SELECT stage,current_generation FROM tasks WHERE task_id=?", (task_id,)
        ).fetchone()
        store._insert_event(
            task_id=task_id,
            event_type="provider_available",
            stage=task["stage"] if task is not None else None,
            generation=(int(task["current_generation"]) or None) if task is not None else None,
            payload={"provider": provider, "attempt_count": int(row["attempt_count"])},
            created_at=_iso(instant),
        )
    return True
