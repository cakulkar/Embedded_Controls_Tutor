"""Grade a controller *model* by MIL tests on either backend (ECSim engine or Simulink)."""
from __future__ import annotations

from typing import Any

from ecsim.learning.graders.base import GradeResult
from ecsim.model.backends import SimulinkBackend, matlab_engine_available, select_backend
from ecsim.model.backends import simulink_backend
from ecsim.model.mil import run_tests


class ModelMilGrader:
    def grade(self, spec: dict[str, Any], submission: str) -> GradeResult:
        backend = select_backend(submission)
        if backend is None:
            return GradeResult(False, 0.0, "Submit either an ECSim model (JSON text starting with '{') or the "
                                           "path to your Simulink model file (.slx or .mdl).")
        if isinstance(backend, SimulinkBackend) and simulink_backend._SESSION is None \
                and not matlab_engine_available():
            return GradeResult(False, 0.0,
                               "This is a Simulink model, but the MATLAB Engine for Python is not installed. "
                               "Install it for your MATLAB release (see README → MATLAB/Simulink), or submit an "
                               "ECSim model. This attempt was not recorded.",
                               {"infrastructure_error": True})
        rep = run_tests(backend, submission, spec)
        total = len(rep.outcomes)
        passed = sum(o.passed for o in rep.outcomes)
        score = passed / total if total and not rep.errors else 0.0
        return GradeResult(rep.passed, score, rep.summary(),
                           {"backend": rep.backend,
                            "traces": {k: {"t": v.t, "signals": v.signals} for k, v in rep.traces.items()}})
