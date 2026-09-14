from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from .review import StructuredReview
from .store import Store, StoreError, utc_now


@dataclass(frozen=True)
class ValidationEvidence:
    validation_id: int
    task_id: str
    generation: int
    attempt_id: int
    candidate_sha: str
    status: str
    result: Mapping[str, Any]
    created_at: str


@dataclass(frozen=True)
class ReviewEvidence:
    review_id: int
    task_id: str
    generation: int
    attempt_id: int
    candidate_sha: str
    run_id: str
    verdict: str
    findings: tuple[Mapping[str, Any], ...]
    summary: str
    raw_result_path: str
    created_at: str


def _assert_candidate(store: Store, task_id: str, generation: int, candidate_sha: str) -> None:
    row = store._conn.execute(
        "SELECT candidate_sha FROM candidate_generations WHERE task_id=? AND generation=?",
        (task_id, generation),
    ).fetchone()
    if row is None or row["candidate_sha"] != candidate_sha:
        raise StoreError("evidence candidate generation/SHA is not a frozen task candidate")


def _assert_attempt(store: Store, task_id: str, attempt_id: int, generation: int) -> None:
    row = store._conn.execute(
        "SELECT task_id,generation FROM attempts WHERE attempt_id=?", (attempt_id,)
    ).fetchone()
    if row is None:
        raise StoreError(f"unknown attempt id: {attempt_id}")
    if row["task_id"] != task_id:
        raise StoreError("attempt belongs to a different task")
    if row["generation"] != generation:
        raise StoreError("attempt generation does not match evidence generation")


def record_validation(
    store: Store,
    *,
    task_id: str,
    generation: int,
    candidate_sha: str,
    attempt_id: int,
    status: str,
    result: Mapping[str, Any],
) -> ValidationEvidence:
    now = utc_now()
    with store._transaction():
        _assert_candidate(store, task_id, generation, candidate_sha)
        _assert_attempt(store, task_id, attempt_id, generation)
        cursor = store._conn.execute(
            """
            INSERT INTO validations(task_id,generation,attempt_id,candidate_sha,status,result_json,created_at)
            VALUES (?,?,?,?,?,?,?)
            """,
            (task_id, generation, attempt_id, candidate_sha, status, json.dumps(dict(result), sort_keys=True), now),
        )
        validation_id = int(cursor.lastrowid)
        store._insert_event(
            task_id=task_id,
            event_type="validation_finished",
            stage="VALIDATE",
            generation=generation,
            payload={"validation_id": validation_id, "candidate_sha": candidate_sha, "status": status},
            created_at=now,
        )
    return ValidationEvidence(validation_id, task_id, generation, attempt_id, candidate_sha, status, dict(result), now)


def record_review(
    store: Store,
    *,
    task_id: str,
    generation: int,
    candidate_sha: str,
    attempt_id: int,
    run_id: str,
    review: StructuredReview,
    raw_result_path: str,
) -> ReviewEvidence:
    now = utc_now()
    payload = review.to_dict()
    findings = tuple(payload["findings"])
    with store._transaction():
        _assert_candidate(store, task_id, generation, candidate_sha)
        _assert_attempt(store, task_id, attempt_id, generation)
        cursor = store._conn.execute(
            """
            INSERT INTO reviews(task_id,generation,attempt_id,candidate_sha,run_id,verdict,findings_json,summary,raw_result_path,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                task_id, generation, attempt_id, candidate_sha, run_id, review.verdict.value,
                json.dumps(list(findings), sort_keys=True), review.summary, raw_result_path, now,
            ),
        )
        review_id = int(cursor.lastrowid)
        store._insert_event(
            task_id=task_id,
            event_type="review_finished",
            stage="REVIEW",
            generation=generation,
            payload={"review_id": review_id, "candidate_sha": candidate_sha, "verdict": review.verdict.value, "run_id": run_id},
            created_at=now,
        )
    return ReviewEvidence(review_id, task_id, generation, attempt_id, candidate_sha, run_id, review.verdict.value, findings, review.summary, raw_result_path, now)


def latest_validation(store: Store, task_id: str, generation: int) -> ValidationEvidence | None:
    row = store._conn.execute(
        "SELECT * FROM validations WHERE task_id=? AND generation=? ORDER BY validation_id DESC LIMIT 1",
        (task_id, generation),
    ).fetchone()
    if row is None:
        return None
    return ValidationEvidence(
        int(row["validation_id"]), row["task_id"], int(row["generation"]), int(row["attempt_id"]),
        row["candidate_sha"], row["status"], json.loads(row["result_json"]), row["created_at"],
    )


def latest_review(store: Store, task_id: str, generation: int) -> ReviewEvidence | None:
    row = store._conn.execute(
        "SELECT * FROM reviews WHERE task_id=? AND generation=? ORDER BY review_id DESC LIMIT 1",
        (task_id, generation),
    ).fetchone()
    if row is None:
        return None
    return ReviewEvidence(
        int(row["review_id"]), row["task_id"], int(row["generation"]), int(row["attempt_id"]),
        row["candidate_sha"], row["run_id"], row["verdict"], tuple(json.loads(row["findings_json"] or "[]")),
        row["summary"], row["raw_result_path"], row["created_at"],
    )
