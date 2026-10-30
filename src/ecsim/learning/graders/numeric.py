from __future__ import annotations

import math
import re
from typing import Any

from ecsim.learning.graders.base import GradeResult

_UNIT_TAIL = re.compile(r"^\s*([-+]?[0-9a-fA-FxXoObB._eE+-]+)\s*([A-Za-z/%°·]*)\s*$")


def parse_number(text: str, integer: bool = False) -> float:
    """Accept decimal, scientific, and (for integers) 0x/0b/0o literals; optional unit suffix."""
    text = text.strip().replace("−", "-").replace(",", "")
    m = _UNIT_TAIL.match(text)
    if not m:
        raise ValueError(f"not a number: {text!r}")
    token = m.group(1)
    if re.match(r"^[-+]?0[xXbBoO]", token):
        return float(int(token, 0))
    return float(token)


class NumericGrader:
    def grade(self, spec: dict[str, Any], submission: str) -> GradeResult:
        try:
            value = parse_number(submission, bool(spec.get("integer")))
        except ValueError as exc:
            return GradeResult(False, 0.0, f"Could not read a number: {exc}")
        if not math.isfinite(value):
            return GradeResult(False, 0.0, "Answer must be finite.")
        answer = float(spec["answer"])
        abs_tol = float(spec.get("abs_tol", 0.0))
        rel_tol = float(spec.get("rel_tol", 0.0))
        if spec.get("integer") and value != int(value):
            return GradeResult(False, 0.0, "An integer answer is expected.")
        ok = math.isclose(value, answer, rel_tol=rel_tol, abs_tol=abs_tol) or value == answer
        unit = spec.get("unit", "")
        if ok:
            return GradeResult(True, 1.0, "Correct.", {"value": value})
        # Directional feedback without revealing the answer.
        direction = "too high" if value > answer else "too low"
        tol = f" (tolerance ±{abs_tol:g}{(' ' + unit) if unit else ''})" if abs_tol else ""
        msg = f"Not within tolerance{tol}: your value is {direction}."
        if answer != 0 and abs(value / answer) in (1000.0, 0.001):
            msg += " Check your unit conversion (factor of 1000)."
        elif answer != 0 and math.isclose(value, -answer, rel_tol=1e-6, abs_tol=abs_tol):
            msg += " Check the sign."
        return GradeResult(False, 0.0, msg, {"value": value})
