from __future__ import annotations

from typing import Any

from ecsim.learning.graders.base import GradeResult


class ChoiceGrader:
    """Submission: comma-separated zero-based option indices, e.g. "1" or "0,2"."""

    def grade(self, spec: dict[str, Any], submission: str) -> GradeResult:
        try:
            picked = sorted({int(p) for p in submission.replace(" ", "").split(",") if p != ""})
        except ValueError:
            return GradeResult(False, 0.0, "Select an option.")
        if not picked:
            return GradeResult(False, 0.0, "Select an option.")
        n = len(spec["options"])
        if any(not (0 <= p < n) for p in picked):
            return GradeResult(False, 0.0, "Invalid option index.")
        correct = sorted(set(spec["correct"]))
        if picked == correct:
            return GradeResult(True, 1.0, "Correct.")
        return GradeResult(False, 0.0, "Not correct. Re-read the prompt and reason about each option.")
