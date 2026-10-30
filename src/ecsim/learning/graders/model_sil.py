"""Grade a model by its *generated code*: SIL requirement checks + MIL/SIL back-to-back equivalence.

Accepts ECSim model JSON, or a Simulink .slx path (imported with ecsim.model.slx, so the learner's
Simulink model is code-generated and SIL-tested by ECSim).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ecsim.learning.graders.base import GradeResult
from ecsim.model.blocks import ModelError
from ecsim.model.sil import back_to_back


class ModelSilGrader:
    def grade(self, spec: dict[str, Any], submission: str) -> GradeResult:
        text = submission.strip()
        source = "ECSim model"
        if text.lower().endswith(".slx"):
            from ecsim.model.slx import import_slx
            try:
                model, _ = import_slx(Path(text).expanduser())
            except ModelError as exc:
                return GradeResult(False, 0.0, f"Could not import the Simulink model for code generation:\n{exc}")
            text, source = json.dumps(model), "Simulink model (imported)"
        elif not text.startswith("{"):
            return GradeResult(False, 0.0, "Submit an ECSim model (JSON) or the path to a .slx file.")
        b2b = spec.get("b2b", {})
        try:
            rep = back_to_back(text, spec, spec.get("dtype", "double"), float(b2b.get("abs", 1e-9)),
                               float(b2b.get("rel", 0.0)))
        except ModelError as exc:
            return GradeResult(False, 0.0, str(exc))
        if rep.sil.errors or rep.mil.errors:
            errs = rep.mil.errors or rep.sil.errors
            return GradeResult(False, 0.0, "\n".join(f"✗ {e}" for e in errs))
        lines = [f"Source: {source}. Code generated in {spec.get('dtype', 'double')} precision, compiled with -Werror.",
                 "", "Requirement checks on the generated code (SIL):"]
        total = passed = 0
        for o in rep.sil.outcomes:
            total += 1
            passed += o.passed
            lines.append(f"    {'ok ' if o.passed else 'FAIL'} [{o.test}] {o.name}: {o.detail}")
        lines.append(f"\nBack-to-back MIL (model, double) vs SIL (code) — tolerance {b2b.get('abs', 1e-9):g} abs:")
        out_names = set(spec["interface"]["outports"])
        for cmp in rep.comparisons:
            if cmp.signal not in out_names:
                continue
            total += 1
            passed += cmp.passed
            lines.append(f"    {'ok ' if cmp.passed else 'FAIL'} [{cmp.test}] {cmp.signal}: max |MIL − SIL| = "
                         f"{cmp.max_abs:.4g}" + ("" if cmp.passed else f" at t = {cmp.at_t:.2f} s"))
        mil_ok = sum(o.passed for o in rep.mil.outcomes)
        lines.append(f"\n(For reference, the model itself passes {mil_ok}/{len(rep.mil.outcomes)} checks in MIL.)")
        ok = total > 0 and passed == total
        head = f"{passed}/{total} checks passed."
        return GradeResult(ok, passed / total if total else 0.0, head + "\n" + "\n".join(lines),
                           {"code": rep.sil and None})
