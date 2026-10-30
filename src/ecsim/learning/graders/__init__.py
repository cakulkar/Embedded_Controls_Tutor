"""Deterministic graders. Each returns a GradeResult; none call the network."""
from ecsim.learning.graders.base import GradeResult, Grader
from ecsim.learning.graders.c_function import CFunctionGrader
from ecsim.learning.graders.choice import ChoiceGrader
from ecsim.learning.graders.numeric import NumericGrader


def default_graders(c_compiler: str = "gcc", c_timeout_s: float = 5.0) -> dict[str, Grader]:
    return {
        "numeric": NumericGrader(),
        "choice": ChoiceGrader(),
        "c_function": CFunctionGrader(compiler=c_compiler, timeout_s=c_timeout_s),
    }


__all__ = ["GradeResult", "Grader", "default_graders", "NumericGrader", "ChoiceGrader", "CFunctionGrader"]
