from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

from .workflow import ReviewVerdict


class ReviewParseError(ValueError):
    pass


class Severity(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass(frozen=True)
class ReviewFinding:
    severity: Severity
    title: str
    problem: str
    required_action: str
    file: str | None = None
    symbol: str | None = None
    failure_scenario: str | None = None


@dataclass(frozen=True)
class StructuredReview:
    verdict: ReviewVerdict
    findings: tuple[ReviewFinding, ...]
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "findings": [
                {
                    "severity": finding.severity.value,
                    "title": finding.title,
                    "file": finding.file,
                    "symbol": finding.symbol,
                    "problem": finding.problem,
                    "failure_scenario": finding.failure_scenario,
                    "required_action": finding.required_action,
                }
                for finding in self.findings
            ],
            "summary": self.summary,
        }


def _required_string(value: Mapping[str, Any], key: str, path: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ReviewParseError(f"{path}.{key} must be a non-empty string")
    return item


def _optional_string(value: Mapping[str, Any], key: str, path: str) -> str | None:
    item = value.get(key)
    if item is None:
        return None
    if not isinstance(item, str) or not item.strip():
        raise ReviewParseError(f"{path}.{key} must be null or a non-empty string")
    return item


def validate_review(value: Any) -> StructuredReview:
    if not isinstance(value, Mapping):
        raise ReviewParseError("review root must be an object")
    raw_verdict = value.get("verdict")
    try:
        verdict = ReviewVerdict(raw_verdict)
    except (ValueError, TypeError) as exc:
        raise ReviewParseError(f"unsupported review verdict: {raw_verdict!r}") from exc
    summary = _required_string(value, "summary", "review")
    raw_findings = value.get("findings")
    if not isinstance(raw_findings, list):
        raise ReviewParseError("review.findings must be a list")
    findings: list[ReviewFinding] = []
    for index, raw in enumerate(raw_findings):
        path = f"review.findings[{index}]"
        if not isinstance(raw, Mapping):
            raise ReviewParseError(f"{path} must be an object")
        raw_severity = raw.get("severity")
        try:
            severity = Severity(raw_severity)
        except (ValueError, TypeError) as exc:
            raise ReviewParseError(f"unsupported finding severity: {raw_severity!r}") from exc
        findings.append(
            ReviewFinding(
                severity=severity,
                title=_required_string(raw, "title", path),
                file=_optional_string(raw, "file", path),
                symbol=_optional_string(raw, "symbol", path),
                problem=_required_string(raw, "problem", path),
                failure_scenario=_optional_string(raw, "failure_scenario", path),
                required_action=_required_string(raw, "required_action", path),
            )
        )
    if verdict == ReviewVerdict.REQUEST_CHANGES and not findings:
        raise ReviewParseError("REQUEST_CHANGES must contain at least one finding")
    if verdict == ReviewVerdict.BLOCKED_BY_MISSING_EVIDENCE and not findings:
        raise ReviewParseError("BLOCKED_BY_MISSING_EVIDENCE must explain missing evidence in findings")
    return StructuredReview(verdict=verdict, findings=tuple(findings), summary=summary)


def parse_review_output(text: str) -> StructuredReview:
    """Find and validate one structured review object inside potentially noisy output."""
    decoder = json.JSONDecoder()
    candidates: list[StructuredReview] = []
    errors: list[str] = []
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        try:
            candidates.append(validate_review(value))
        except ReviewParseError as exc:
            errors.append(str(exc))
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise ReviewParseError("review output contains multiple valid review objects")
    detail = f": {errors[-1]}" if errors else ""
    raise ReviewParseError(f"no valid structured review object found{detail}")
