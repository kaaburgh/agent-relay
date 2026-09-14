from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from .store import Store, StoreError


class LeaseUnavailable(StoreError):
    pass


@dataclass(frozen=True)
class ResourceLease:
    lease_id: int
    resource_name: str
    holder_id: str
    task_id: str
    attempt_id: int | None
    acquired_at: str
    heartbeat_at: str
    released_at: str | None


def _iso(value: datetime | None = None) -> str:
    instant = value or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _parse(value: str) -> datetime:
    instant = datetime.fromisoformat(value)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(timezone.utc)


def configure_resource(store: Store, resource_name: str, capacity: int) -> None:
    if not resource_name.strip():
        raise ValueError("resource name must not be empty")
    if capacity <= 0:
        raise ValueError("resource capacity must be positive")
    with store._transaction():
        current = store._conn.execute(
            "SELECT capacity FROM resources WHERE resource_name=?", (resource_name,)
        ).fetchone()
        active = int(
            store._conn.execute(
                "SELECT COUNT(*) FROM leases WHERE resource_name=? AND released_at IS NULL",
                (resource_name,),
            ).fetchone()[0]
        )
        if capacity < active:
            raise StoreError(
                f"cannot reduce {resource_name!r} capacity to {capacity} with {active} active leases"
            )
        if current is None:
            store._conn.execute(
                "INSERT INTO resources(resource_name,capacity) VALUES (?,?)",
                (resource_name, capacity),
            )
        else:
            store._conn.execute(
                "UPDATE resources SET capacity=? WHERE resource_name=?",
                (capacity, resource_name),
            )


def _lease(row: object) -> ResourceLease:
    return ResourceLease(
        lease_id=int(row["lease_id"]),  # type: ignore[index]
        resource_name=row["resource_name"],  # type: ignore[index]
        holder_id=row["holder_id"],  # type: ignore[index]
        task_id=row["task_id"],  # type: ignore[index]
        attempt_id=row["attempt_id"],  # type: ignore[index]
        acquired_at=row["acquired_at"],  # type: ignore[index]
        heartbeat_at=row["heartbeat_at"],  # type: ignore[index]
        released_at=row["released_at"],  # type: ignore[index]
    )


def active_leases(store: Store, resource_name: str | None = None) -> tuple[ResourceLease, ...]:
    if resource_name is None:
        rows = store._conn.execute(
            "SELECT * FROM leases WHERE released_at IS NULL ORDER BY lease_id"
        ).fetchall()
    else:
        rows = store._conn.execute(
            "SELECT * FROM leases WHERE resource_name=? AND released_at IS NULL ORDER BY lease_id",
            (resource_name,),
        ).fetchall()
    return tuple(_lease(row) for row in rows)


def acquire_lease(
    store: Store,
    *,
    resource_name: str,
    holder_id: str,
    task_id: str,
    attempt_id: int | None = None,
    now: datetime | None = None,
) -> ResourceLease:
    if not holder_id.strip():
        raise ValueError("holder id must not be empty")
    timestamp = _iso(now)
    with store._transaction():
        resource = store._conn.execute(
            "SELECT capacity FROM resources WHERE resource_name=?", (resource_name,)
        ).fetchone()
        if resource is None:
            raise StoreError(f"unknown resource: {resource_name}")
        task = store._conn.execute("SELECT 1 FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if task is None:
            raise StoreError(f"unknown task: {task_id}")
        if attempt_id is not None:
            attempt = store._conn.execute(
                "SELECT task_id FROM attempts WHERE attempt_id=?", (attempt_id,)
            ).fetchone()
            if attempt is None or attempt["task_id"] != task_id:
                raise StoreError("lease attempt must exist and belong to the task")
        existing = store._conn.execute(
            "SELECT * FROM leases WHERE resource_name=? AND holder_id=?",
            (resource_name, holder_id),
        ).fetchone()
        if existing is not None:
            if existing["released_at"] is None:
                if existing["task_id"] != task_id or existing["attempt_id"] != attempt_id:
                    raise StoreError("active holder id belongs to different lease ownership")
                return _lease(existing)
            raise StoreError("holder ids are single-use durable lease identities")
        active = int(
            store._conn.execute(
                "SELECT COUNT(*) FROM leases WHERE resource_name=? AND released_at IS NULL",
                (resource_name,),
            ).fetchone()[0]
        )
        capacity = int(resource["capacity"])
        if active >= capacity:
            raise LeaseUnavailable(
                f"resource {resource_name!r} is at capacity {capacity}"
            )
        cursor = store._conn.execute(
            """
            INSERT INTO leases(resource_name,holder_id,task_id,attempt_id,acquired_at,heartbeat_at,released_at)
            VALUES (?,?,?,?,?,?,NULL)
            """,
            (resource_name, holder_id, task_id, attempt_id, timestamp, timestamp),
        )
        lease_id = int(cursor.lastrowid)
        store._insert_event(
            task_id=task_id,
            event_type="resource_acquired",
            stage=None,
            generation=None,
            payload={"lease_id": lease_id, "resource": resource_name, "holder_id": holder_id, "attempt_id": attempt_id},
            created_at=timestamp,
        )
        row = store._conn.execute("SELECT * FROM leases WHERE lease_id=?", (lease_id,)).fetchone()
    return _lease(row)


def heartbeat_lease(
    store: Store,
    *,
    lease_id: int,
    holder_id: str,
    now: datetime | None = None,
) -> ResourceLease:
    timestamp = _iso(now)
    with store._transaction():
        cursor = store._conn.execute(
            """
            UPDATE leases SET heartbeat_at=?
            WHERE lease_id=? AND holder_id=? AND released_at IS NULL
            """,
            (timestamp, lease_id, holder_id),
        )
        if cursor.rowcount != 1:
            raise StoreError("cannot heartbeat inactive or differently-owned lease")
        row = store._conn.execute("SELECT * FROM leases WHERE lease_id=?", (lease_id,)).fetchone()
    return _lease(row)


def release_lease(
    store: Store,
    *,
    lease_id: int,
    holder_id: str,
    now: datetime | None = None,
    recovered: bool = False,
) -> ResourceLease:
    timestamp = _iso(now)
    with store._transaction():
        row = store._conn.execute("SELECT * FROM leases WHERE lease_id=?", (lease_id,)).fetchone()
        if row is None:
            raise StoreError(f"unknown lease id: {lease_id}")
        if row["holder_id"] != holder_id:
            raise StoreError("lease holder mismatch")
        if row["released_at"] is not None:
            return _lease(row)
        cursor = store._conn.execute(
            "UPDATE leases SET released_at=? WHERE lease_id=? AND released_at IS NULL",
            (timestamp, lease_id),
        )
        if cursor.rowcount != 1:
            raise StoreError("lease changed before release commit")
        store._insert_event(
            task_id=row["task_id"],
            event_type="resource_released",
            stage=None,
            generation=None,
            payload={"lease_id": lease_id, "resource": row["resource_name"], "holder_id": holder_id, "recovered": recovered},
            created_at=timestamp,
        )
        released = store._conn.execute("SELECT * FROM leases WHERE lease_id=?", (lease_id,)).fetchone()
    return _lease(released)


def reclaim_stale_leases(
    store: Store,
    *,
    stale_after: timedelta,
    now: datetime | None = None,
) -> tuple[ResourceLease, ...]:
    if stale_after.total_seconds() <= 0:
        raise ValueError("stale_after must be positive")
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    instant = instant.astimezone(timezone.utc)
    cutoff = instant - stale_after
    timestamp = _iso(instant)

    candidates = active_leases(store)
    released: list[ResourceLease] = []
    for candidate in candidates:
        if _parse(candidate.heartbeat_at) > cutoff:
            continue
        if candidate.attempt_id is None:
            continue

        reclaimed: ResourceLease | None = None
        with store._transaction():
            row = store._conn.execute(
                "SELECT * FROM leases WHERE lease_id=?", (candidate.lease_id,)
            ).fetchone()
            if row is None or row["released_at"] is not None:
                continue
            if _parse(row["heartbeat_at"]) > cutoff:
                continue
            attempt_id = row["attempt_id"]
            if attempt_id is None:
                continue
            running = store._conn.execute(
                """
                SELECT 1 FROM processes
                WHERE attempt_id=? AND state='RUNNING'
                LIMIT 1
                """,
                (attempt_id,),
            ).fetchone()
            if running is not None:
                continue
            cursor = store._conn.execute(
                "UPDATE leases SET released_at=? WHERE lease_id=? AND released_at IS NULL",
                (timestamp, candidate.lease_id),
            )
            if cursor.rowcount != 1:
                raise StoreError("lease changed before stale reclaim commit")
            store._insert_event(
                task_id=row["task_id"],
                event_type="resource_released",
                stage=None,
                generation=None,
                payload={
                    "lease_id": candidate.lease_id,
                    "resource": row["resource_name"],
                    "holder_id": row["holder_id"],
                    "recovered": True,
                },
                created_at=timestamp,
            )
            released_row = store._conn.execute(
                "SELECT * FROM leases WHERE lease_id=?", (candidate.lease_id,)
            ).fetchone()
            reclaimed = _lease(released_row)
        if reclaimed is not None:
            released.append(reclaimed)
    return tuple(released)
