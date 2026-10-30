"""End-to-end virtual ECU tests: isolation, scenarios, grader strength, lab, CLI."""
import shutil

import pytest

from ecsim.app.config import CONTENT_DIR
from ecsim.cli import main
from ecsim.ecu.build import build_app
from ecsim.ecu.runner import run_isolated
from ecsim.learning.content import load_catalog
from ecsim.learning.graders import EcuAppGrader

from conftest import needs_cc

CAT = load_catalog(CONTENT_DIR)
ECU_EXERCISES = [e for e in CAT.all_exercises() if e.kind == "ecu_app"]
REF = CAT.exercise("A05-E1").spec["reference"]


def test_ecu_exercises_exist_for_both_targets():
    assert {e.spec["profile"] for e in ECU_EXERCISES} == {"CM4_GENERIC", "TC3XX_LIKE"}


@needs_cc
@pytest.mark.parametrize("ex", ECU_EXERCISES, ids=lambda e: e.id)
def test_reference_firmware_passes(ex):
    r = EcuAppGrader().grade(ex.spec, ex.spec["reference"])
    assert r.passed, r.feedback
    if shutil.which("size"):                      # resource report needs GNU binutils
        assert "Host-compiled size" in r.feedback


@needs_cc
@pytest.mark.parametrize("ex", ECU_EXERCISES, ids=lambda e: e.id)
def test_starter_firmware_fails(ex):
    assert not EcuAppGrader().grade(ex.spec, ex.spec["starter"]).passed


@needs_cc
@pytest.mark.parametrize("ex,i", [(e, i) for e in ECU_EXERCISES for i in range(len(e.spec["mutants"]))],
                         ids=lambda v: str(v.id if hasattr(v, "id") else v))
def test_mutant_firmware_builds_but_fails(ex, i):
    r = EcuAppGrader().grade(ex.spec, ex.spec["mutants"][i])
    assert r.details.get("stage") != "compile", r.feedback
    assert not r.passed


@needs_cc
def test_double_kick_is_only_wrong_on_windowed_watchdog():
    """The porting lesson of A05-E2: same code, different target behaviour."""
    tc = CAT.exercise("A05-E2")
    double_kick = tc.spec["mutants"][1]
    assert not EcuAppGrader().grade(tc.spec, double_kick).passed
    assert EcuAppGrader().grade(CAT.exercise("A05-E1").spec, double_kick).passed


@needs_cc
def test_crashing_application_is_isolated(tmp_path):
    src = "void app_init(void) {}\nvoid task_10ms(void) { volatile int *p = NULL; *p = 1; }"
    b = build_app(src, tmp_path)
    assert b.ok, b.log
    out = run_isolated(b.lib_path, "CM4_GENERIC", "motor_nominal")
    assert not out.ok and "crash" in out.error


@needs_cc
def test_hanging_application_times_out(tmp_path):
    src = "void app_init(void) {}\nvoid task_10ms(void) { volatile int k = 1; while (k) { } }"
    b = build_app(src, tmp_path)
    out = run_isolated(b.lib_path, "CM4_GENERIC", "motor_nominal", timeout_s=3)
    assert not out.ok and "never returns" in out.error


@needs_cc
def test_scenarios_are_deterministic(tmp_path):
    b = build_app(REF, tmp_path)
    a = run_isolated(b.lib_path, "CM4_GENERIC", "motor_load_step").result
    c = run_isolated(b.lib_path, "CM4_GENERIC", "motor_load_step").result
    assert a.trace.speed_rpm == c.trace.speed_rpm


@needs_cc
def test_engine_records_ecu_submission(ctx, learner):
    rec = ctx.learning.submit(learner, "A05-E1", REF)
    assert rec.passed and rec.independent and "15/15 checks" in rec.feedback


@needs_cc
def test_lab_run_is_logged_not_graded(ctx, learner):
    run = ctx.ecu_lab.run(REF, "TC3XX_LIKE", "motor_task_hang", 1, learner)
    assert run.outcome.ok and run.outcome.result.passed and run.run_id
    rows = ctx.ecu_lab.history(learner)
    assert rows[0]["scenario"] == "motor_task_hang" and rows[0]["resets"] == 1
    assert ctx.learning.attempts(learner) == []          # lab runs never become evidence


@needs_cc
def test_cli_ecu(tmp_path, capsys):
    src = tmp_path / "fw.c"
    src.write_text(REF, encoding="utf-8")
    home = str(tmp_path / "home")
    main(["--home", home, "init", "--name", "Ana"])
    csv = tmp_path / "trace.csv"
    code = main(["--home", home, "ecu", "run", str(src), "--profile", "TC3XX_LIKE",
                 "--scenario", "motor_speed_sensor_open", "--csv", str(csv)])
    out = capsys.readouterr().out
    assert code == 0 and "PASS" in out and "DEM event 1 -> FAILED" in out
    lines = csv.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("t_ms,speed_rpm") and len(lines) == 3001
    assert main(["--home", home, "ecu", "profiles"]) == 0
