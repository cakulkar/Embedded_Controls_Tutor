"""Deterministic graders. Each returns a GradeResult; none call the network."""
from ecsim.learning.graders.base import GradeResult, Grader
from ecsim.learning.graders.c_function import CFunctionGrader
from ecsim.learning.graders.choice import ChoiceGrader
from ecsim.learning.graders.ecu_app import EcuAppGrader
from ecsim.learning.graders.model_mil import ModelMilGrader
from ecsim.learning.graders.model_sil import ModelSilGrader
from ecsim.learning.graders.numeric import NumericGrader
from ecsim.learning.graders.test_design import TestDesignGrader
from ecsim.learning.graders.uds_script import UdsScriptGrader


def default_graders(c_compiler: str = "gcc", c_timeout_s: float = 5.0) -> dict[str, Grader]:
    return {
        "numeric": NumericGrader(),
        "choice": ChoiceGrader(),
        "c_function": CFunctionGrader(compiler=c_compiler, timeout_s=c_timeout_s),
        "ecu_app": EcuAppGrader(compiler=c_compiler),
        "model_mil": ModelMilGrader(),
        "model_sil": ModelSilGrader(),
        "test_design": TestDesignGrader(),
        "uds_script": UdsScriptGrader(),
    }


__all__ = ["GradeResult", "Grader", "default_graders", "NumericGrader", "ChoiceGrader", "CFunctionGrader", "EcuAppGrader", "ModelMilGrader", "ModelSilGrader", "TestDesignGrader", "UdsScriptGrader"]
