from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UNIT_RE = re.compile(
    r"^## (R\d{2}) — .+ — (DONE|READY|IN PROGRESS|BLOCKED|FUTURE)$",
    re.MULTILINE,
)


def _completed_from_status(text: str) -> set[str]:
    line = next((line for line in text.splitlines() if line.startswith("Completed:")), None)
    if line is None:
        raise AssertionError("implementation status must contain a Completed: line")
    ids = re.findall(r"`(R\d{2})`", line)
    if "–" in line and len(ids) == 2:
        start = int(ids[0][1:])
        end = int(ids[1][1:])
        if end < start:
            raise AssertionError("Completed range is reversed")
        return {f"R{value:02d}" for value in range(start, end + 1)}
    return set(ids)


def _single_unit_line(text: str, prefix: str) -> str | None:
    line = next((line for line in text.splitlines() if line.startswith(prefix)), None)
    if line is None:
        raise AssertionError(f"implementation status must contain {prefix!r}")
    match = re.search(r"`(R\d{2})`", line)
    return match.group(1) if match else None


class ProjectStatusConsistencyTests(unittest.TestCase):
    def test_roadmap_and_durable_handoff_agree(self) -> None:
        roadmap = (ROOT / "ROADMAP.md").read_text(encoding="utf-8")
        status = (ROOT / "docs" / "implementation-status.md").read_text(encoding="utf-8")

        units = UNIT_RE.findall(roadmap)
        self.assertGreater(len(units), 0, "ROADMAP.md must expose parseable unit statuses")
        roadmap_status = dict(units)
        done = {unit for unit, value in units if value == "DONE"}
        in_progress = [unit for unit, value in units if value == "IN PROGRESS"]
        ready = [unit for unit, value in units if value == "READY"]

        self.assertLessEqual(len(in_progress), 1, "at most one roadmap unit may be IN PROGRESS")
        self.assertEqual(_completed_from_status(status), done)

        status_in_progress = _single_unit_line(status, "In progress:")
        if in_progress:
            self.assertEqual(status_in_progress, in_progress[0])
            self.assertIn("Acceptance pending:", status)
        else:
            self.assertIsNone(status_in_progress)

        next_unit = _single_unit_line(status, "Next bounded unit:")
        expected_next = in_progress[0] if in_progress else (ready[0] if ready else None)
        self.assertEqual(next_unit, expected_next)

        if next_unit is not None:
            self.assertIn(roadmap_status[next_unit], {"READY", "IN PROGRESS"})

    def test_protocol_requires_unit_acceptance_and_closeout(self) -> None:
        instructions = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("unit-specific acceptance tests", instructions)
        self.assertIn("### Partial-work rule", instructions)
        self.assertIn("### Closeout checklist", instructions)
        self.assertIn("tests/test_project_status.py", instructions)


if __name__ == "__main__":
    unittest.main()
