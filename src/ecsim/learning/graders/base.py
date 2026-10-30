from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class GradeResult:
    passed: bool
    score: float  # 0..1
    feedback: str
    details: dict[str, Any] = field(default_factory=dict)


class Grader(Protocol):
    def grade(self, spec: dict[str, Any], submission: str) -> GradeResult: ...
