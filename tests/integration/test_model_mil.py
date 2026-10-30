"""MIL grading on both backends. The Simulink path is exercised with a stand-in session
(ECSim engine behind the Simulink Session protocol) and a command-recording fake engine."""
import json
import math

import pytest

from ecsim.app.config import CONTENT_DIR
from ecsim.cli import main
from ecsim.learning.content import load_catalog
from ecsim.learning.graders import ModelMilGrader
from ecsim.model.backends import EcsimBackend, SimulinkBackend, select_backend
from ecsim.model.backends import simulink_backend as sb
from ecsim.model.blocks import ModelError
from ecsim.model.mil import DiscreteMotor, TestTrace, evaluate_check, run_tests, signal_value
from ecsim.plants.dc_motor import DcMotor

from fakes import EcsimStandInSession, RecordingEngine, fake_double

CAT = load_catalog(CONTENT_DIR)
EX = CAT.exercise("B02-E4")
SPEC = EX.spec


@pytest.fixture
def standin(tmp_path):
    s = EcsimStandInSession()
    sb.set_session(s)
    yield s
    sb.set_session(None)


def as_slx(tmp_path, model_json: str, name="speed_pi") -> str:
    p = tmp_path / f"{name}.slx"
    p.write_text(model_json, encoding="utf-8")
    return str(p)


# ---- harness pieces --------------------------------------------------------------
def test_signal_definitions():
    assert signal_value({"const": 2}, 5) == 2
    assert [signal_value({"step": {"t": 1, "before": 0, "after": 3}}, t) for t in (0.99, 1.0)] == [0, 3]
    assert signal_value({"ramp": {"t0": 1, "slope": 2, "start": 1}}, 2.5) == 4
    assert signal_value({"points": [[0, 0], [1, 10], [2, 10]]}, 0.5) == 5


def test_discrete_motor_is_exact_zoh_of_plant():
    ts = 0.01
    dm, m = DiscreteMotor.at(ts), DcMotor()
    m.duty = 10 / 24
    w = 0.0
    for _ in range(50):
        w = dm.a * w + dm.bv * 10.0
        for _ in range(10):
            m.step(ts / 10)
    assert w == pytest.approx(m.omega, rel=1e-9)


def test_check_kinds():
    tr = TestTrace([0, 0.1, 0.2, 0.3], {"y": [0, 5, 9, 8]})
    assert evaluate_check("T", {"kind": "within", "signal": "y", "max": 9}, tr).passed
    r = evaluate_check("T", {"kind": "within", "signal": "y", "max": 8.5}, tr)
    assert not r.passed and "t = 0.200" in r.detail
    assert evaluate_check("T", {"kind": "near", "signal": "y", "target": 8.5, "tol": 0.5, "from": 0.2}, tr).passed
    assert evaluate_check("T", {"kind": "below_by", "signal": "y", "value": 8.5, "by": 0.3, "from": 0.2}, tr).passed
    assert evaluate_check("T", {"kind": "at", "signal": "y", "t": 0.1, "min": 5, "max": 5}, tr).passed
    assert not evaluate_check("T", {"kind": "near", "signal": "zz", "target": 0, "tol": 1}, tr).passed


# ---- ECSim backend --------------------------------------------------------------
def test_reference_passes_and_starter_fails():
    g = ModelMilGrader()
    r = g.grade(SPEC, SPEC["reference"])
    assert r.passed, r.feedback
    assert "8/8 checks passed (ECSim engine backend)" in r.feedback
    assert not g.grade(SPEC, SPEC["starter"]).passed


@pytest.mark.parametrize("i", range(len(SPEC["mutants"])))
def test_mutant_models_fail(i):
    assert not ModelMilGrader().grade(SPEC, SPEC["mutants"][i]).passed


def test_interface_and_solver_contract():
    m = json.loads(SPEC["reference"])
    m["blocks"][0]["params"]["name"] = "speed"
    r = ModelMilGrader().grade(SPEC, json.dumps(m))
    assert not r.passed and "interface mismatch" in r.feedback
    m = json.loads(SPEC["reference"])
    m["solver"]["step"] = 0.02
    r = ModelMilGrader().grade(SPEC, json.dumps(m))
    assert "required fixed step 0.01" in r.feedback


def test_unparseable_and_unknown_submissions():
    assert "not an ECSim model" in ModelMilGrader().grade(SPEC, '{"format": "x"}').feedback
    r = ModelMilGrader().grade(SPEC, '{"format": "ecsim-model", "version": 1, "name": "m", "blocks": [], "lines": []}')
    assert not r.passed and "interface mismatch" in r.feedback
    assert "Submit either" in ModelMilGrader().grade(SPEC, "hello").feedback


def test_select_backend():
    assert isinstance(select_backend(" {..."), EcsimBackend)
    assert isinstance(select_backend("C:\\work\\speed_pi.SLX"), SimulinkBackend)
    assert select_backend("model.txt") is None


# ---- Simulink backend: data path parity via stand-in session --------------------
@pytest.mark.parametrize("which", ["reference", "starter", "m0", "m1", "m2", "m3"])
def test_simulink_backend_matches_ecsim_backend(tmp_path, standin, which):
    src = SPEC["mutants"][int(which[1])] if which.startswith("m") else SPEC[which]
    a = run_tests(EcsimBackend(), src, SPEC)
    b = run_tests(SimulinkBackend(), as_slx(tmp_path, src), SPEC)
    assert b.backend == "Simulink (MATLAB)"
    assert [(o.name, o.passed, o.detail) for o in a.outcomes] == [(o.name, o.passed, o.detail) for o in b.outcomes]
    for name in a.traces:
        assert a.traces[name].signals == b.traces[name].signals
    assert standin.closed == ["speed_pi"]


def test_grader_uses_simulink_for_slx(tmp_path, standin):
    r = ModelMilGrader().grade(SPEC, as_slx(tmp_path, SPEC["reference"]))
    assert r.passed and "(Simulink (MATLAB) backend)" in r.feedback


def test_missing_matlab_is_infrastructure_error(tmp_path, monkeypatch):
    sb.set_session(None)
    monkeypatch.setattr("ecsim.learning.graders.model_mil.matlab_engine_available", lambda: False)
    r = ModelMilGrader().grade(SPEC, as_slx(tmp_path, SPEC["reference"]))
    assert r.details.get("infrastructure_error") and "MATLAB Engine" in r.feedback


def test_missing_slx_file(standin):
    r = ModelMilGrader().grade(SPEC, "/nope/speed_pi.slx")
    assert not r.passed and "not found" in r.feedback


def test_engine_submission_through_learning_engine(ctx, learner, tmp_path, standin):
    rec = ctx.learning.submit(learner, "B02-E4", as_slx(tmp_path, SPEC["reference"]))
    assert rec.passed and rec.independent


# ---- Simulink backend: MATLAB command sequence (recording fake engine) -----------
def test_engine_session_commands(tmp_path):
    eng = RecordingEngine()
    s = sb.EngineSession(eng, fake_double)
    name = s.load(tmp_path / "speed_pi.slx")
    assert name == "speed_pi"
    assert ("load_system", str(tmp_path / "speed_pi.slx")) in eng.calls
    assert s.root_ports(name, "Inport") == ["speed_meas_rpm"]
    assert s.fixed_step(name) == 0.01
    assert any("eval(get_param('speed_pi', 'FixedStep'))" in c[1] for c in eng.calls if c[0] == "eval")

    u = [[k * 0.01, 0.0] for k in range(3)]
    s.sim_open_loop(name, u, 0.02)
    sim_cmd = eng.calls[-1][1]
    for frag in ("sim('speed_pi'", "'StopTime', '0.02'", "'ExternalInput', 'ecsim_u'", "'SaveFormat', 'Array'",
                 "'ReturnWorkspaceOutputs', 'on'", "ecsim_out.get('yout')"):
        assert frag in sim_cmd

    dm = DiscreteMotor.at(0.01)
    s.sim_closed_loop(name, dm, u, 0.02, 0.01)
    script = eng.calls[-1][1]
    for frag in ("new_system(ecsim_h)", "'ModelName', 'speed_pi'", "'Solver', 'FixedStepDiscrete'",
                 f"'A', '{dm.a:.17g}'", f"'B', '[{dm.bv:.17g} {-dm.bt:.17g}]'", "add_line(ecsim_h, 'motor/1', 'ctrl/1')",
                 "add_line(ecsim_h, 'T_load/1', 'mux/2')", "close_system(ecsim_h, 0)"):
        assert frag in script, frag
    # Every block added is connected by name, and the three outputs are in the order the backend reads.
    assert script.index("/speed_rpm']") < script.index("/cmd']") < script.index("/motor_voltage_V']")


def test_engine_session_errors(tmp_path):
    s = sb.EngineSession(RecordingEngine(solver="Variable-step"), fake_double)
    with pytest.raises(ModelError, match="Fixed-step"):
        s.fixed_step("m")
    s = sb.EngineSession(RecordingEngine(fail_on="load_system"), fake_double)
    with pytest.raises(ModelError, match="MATLAB error while loading the model"):
        s.load(tmp_path / "m.slx")
    with pytest.raises(ModelError, match="not a valid Simulink model name"):
        s.load(tmp_path / "1bad-name.slx")


def test_simulink_sample_count_mismatch_is_reported(tmp_path, standin, monkeypatch):
    monkeypatch.setattr(standin, "sim_open_loop", lambda name, u, stop: ([0.0], [[0.0]]))
    rep = run_tests(SimulinkBackend(), as_slx(tmp_path, SPEC["reference"]), SPEC)
    assert any("returned 1 samples" in e for e in rep.errors)


# ---- CLI -----------------------------------------------------------------------
def test_cli_model_check_and_slx_submit(tmp_path, capsys, standin):
    f = tmp_path / "pi.json"
    f.write_text(SPEC["reference"], encoding="utf-8")
    assert main(["--home", str(tmp_path / "h"), "model", "check", str(f)]) == 0
    out = capsys.readouterr().out
    assert "sorted execution order" in out and "speed_meas_rpm" in out
    main(["--home", str(tmp_path / "h"), "init", "--name", "Ana"])
    slx = as_slx(tmp_path, SPEC["reference"])
    assert main(["--home", str(tmp_path / "h"), "submit", "B02-E4", "--file", slx]) == 0
    assert "Simulink (MATLAB)" in capsys.readouterr().out
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"format": "ecsim-model", "version": 1, "name": "b",
                               "blocks": [{"id": "g", "type": "Gain"}], "lines": []}), encoding="utf-8")
    assert main(["--home", str(tmp_path / "h"), "model", "check", str(bad)]) == 2
    assert "not connected" in capsys.readouterr().out


def test_cli_codegen_and_deploy(tmp_path, capsys):
    f = tmp_path / "pi.json"
    f.write_text(SPEC["reference"], encoding="utf-8")
    assert main(["--home", str(tmp_path / "h"), "model", "codegen", str(f), "--dtype", "single", "--out", str(tmp_path / "gen")]) == 0
    assert (tmp_path / "gen" / "speed_pi.c").exists() and "typedef float" in (tmp_path / "gen" / "speed_pi.h").read_text(encoding="utf-8")
    assert main(["--home", str(tmp_path / "h"), "model", "deploy", str(f), "--scenario", "motor_load_step"]) == 0
    assert "PASS  generated code" in capsys.readouterr().out
