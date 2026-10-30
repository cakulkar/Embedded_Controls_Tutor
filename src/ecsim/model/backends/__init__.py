"""Model execution backends. Same exercise, same checks, two engines.

* ``EcsimBackend``     : ECSim model JSON, executed by ecsim.model.core (always available).
* ``SimulinkBackend``  : a learner's .slx/.mdl, executed by MATLAB/Simulink through the
                         MATLAB Engine API for Python (optional, needs a MATLAB licence).
"""
from __future__ import annotations

from ecsim.model.backends.ecsim_backend import EcsimBackend
from ecsim.model.backends.simulink_backend import SimulinkBackend, matlab_engine_available


def select_backend(submission: str):
    """Pick the backend from the submission: model JSON text, or a path to an .slx/.mdl file."""
    text = submission.strip()
    if text.startswith("{"):
        return EcsimBackend()
    if text.lower().endswith((".slx", ".mdl")):
        return SimulinkBackend()
    return None


__all__ = ["EcsimBackend", "SimulinkBackend", "matlab_engine_available", "select_backend"]
