"""Quality bar for every model exercise in the catalogue, on both backends."""
import json

import pytest

from ecsim.app.config import CONTENT_DIR
from ecsim.learning.content import load_catalog
from ecsim.learning.graders import ModelMilGrader
from ecsim.model.backends import EcsimBackend, SimulinkBackend
from ecsim.model.backends import simulink_backend as sb
from ecsim.model.mil import run_tests

from fakes import EcsimStandInSession

EXS = [e for e in load_catalog(CONTENT_DIR).all_exercises() if e.kind == "model_mil"]


def test_there_are_model_exercises_at_several_levels():
    assert len(EXS) >= 2 and {"intermediate", "advanced"} <= {e.level for e in EXS}


@pytest.mark.parametrize("ex", EXS, ids=lambda e: e.id)
def test_reference_passes_starter_fails(ex):
    g = ModelMilGrader()
    assert g.grade(ex.spec, ex.spec["reference"]).passed
    assert not g.grade(ex.spec, ex.spec["starter"]).passed


@pytest.mark.parametrize("ex,i", [(e, i) for e in EXS for i in range(len(e.spec["mutants"]))],
                         ids=lambda v: str(v.id if hasattr(v, "id") else v))
def test_every_mutant_is_caught(ex, i):
    r = ModelMilGrader().grade(ex.spec, ex.spec["mutants"][i])
    assert not r.passed and "interface mismatch" not in r.feedback, r.feedback


@pytest.mark.parametrize("ex", EXS, ids=lambda e: e.id)
def test_backend_parity(ex, tmp_path):
    sb.set_session(EcsimStandInSession())
    try:
        p = tmp_path / f"{json.loads(ex.spec['reference'])['name']}.slx"
        p.write_text(ex.spec["reference"], encoding="utf-8")
        a = run_tests(EcsimBackend(), ex.spec["reference"], ex.spec)
        b = run_tests(SimulinkBackend(), str(p), ex.spec)
        assert [(o.name, o.passed, o.detail) for o in a.outcomes] == [(o.name, o.passed, o.detail) for o in b.outcomes]
    finally:
        sb.set_session(None)
