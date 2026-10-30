"""Grade a whole ECU application on the virtual ECU against closed-loop scenarios."""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from ecsim.ecu.build import build_app
from ecsim.ecu.profiles import get_profile
from ecsim.ecu.runner import run_isolated
from ecsim.learning.graders.base import GradeResult


class EcuAppGrader:
    def __init__(self, compiler: str = "gcc", timeout_s: float = 60.0) -> None:
        self.compiler = compiler
        self.timeout_s = timeout_s

    def grade(self, spec: dict[str, Any], submission: str) -> GradeResult:
        if shutil.which(self.compiler) is None:
            return GradeResult(False, 0.0, f"C compiler '{self.compiler}' not found. This attempt was not recorded.",
                               {"infrastructure_error": True})
        profile = get_profile(spec["profile"])
        with tempfile.TemporaryDirectory(prefix="ecsim_ecu_", ignore_cleanup_errors=True) as tmp:
            b = build_app(submission, Path(tmp), self.compiler)
            if not b.ok:
                return GradeResult(False, 0.0, b.log, {"stage": "compile"})
            results, lines, passed, total = [], [], 0, 0
            for sc in spec["scenarios"]:
                out = run_isolated(b.lib_path, profile.code, sc, seed=int(spec.get("seed", 1)),
                                   timeout_s=self.timeout_s)
                if not out.ok:
                    lines.append(f"✗ {sc}: {out.error}")
                    total += 1
                    results.append({"scenario": sc, "error": out.error})
                    continue
                r = out.result
                results.append(r.to_dict())
                lines.append(f"{'✓' if r.passed else '✗'} {sc}  (CPU load {r.cpu_load:.1%}, "
                             f"{len(r.resets)} reset(s))")
                for c in r.checks:
                    total += 1
                    passed += int(c.passed)
                    lines.append(f"    {'ok ' if c.passed else 'FAIL'} {c.name}: {c.detail}")
            res = b.resources
            if res.available:
                lines.append(f"\nHost-compiled size (indicative only): code {res.text} B, const {res.rodata} B, "
                             f"static RAM {res.static_ram} B"
                             + (f", deepest stack frame {res.max_stack_bytes} B in {res.max_stack_function}()"
                                if res.max_stack_bytes is not None else ""))
        ok = total > 0 and passed == total
        head = f"{passed}/{total} checks passed on {profile.title}."
        return GradeResult(ok, passed / total if total else 0.0, head + "\n" + "\n".join(lines),
                           {"scenarios": results, "profile": profile.code})
