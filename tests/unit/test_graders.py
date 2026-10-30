import pytest

from ecsim.app.config import CONTENT_DIR
from ecsim.learning.content import load_catalog
from ecsim.learning.graders import CFunctionGrader, ChoiceGrader, NumericGrader
from ecsim.learning.graders.numeric import parse_number

from conftest import needs_cc

CAT = load_catalog(CONTENT_DIR)
C_EXERCISES = [e for e in CAT.all_exercises() if e.kind == "c_function"]


@pytest.mark.parametrize("text,val", [("4056", 4056), ("0x0FD8", 4056), (" -12288 ", -12288),
                                      ("156.3 kPa", 156.3), ("1e-3", 0.001), ("−0.5", -0.5), ("0b101", 5)])
def test_parse_number(text, val):
    assert parse_number(text) == pytest.approx(val)


def test_numeric_tolerance_and_feedback():
    g, spec = NumericGrader(), {"answer": 156.28, "abs_tol": 0.1, "unit": "kPa"}
    assert g.grade(spec, "156.3").passed
    r = g.grade(spec, "160")
    assert not r.passed and "too high" in r.feedback and "156" not in r.feedback  # never leaks answer
    assert not g.grade(spec, "abc").passed
    assert not g.grade(spec, "nan").passed


def test_numeric_integer_and_sign_hint():
    g = NumericGrader()
    assert not g.grade({"answer": 4, "integer": True}, "4.5").passed
    assert "sign" in g.grade({"answer": -12288, "abs_tol": 0}, "12288").feedback


def test_numeric_unit_factor_hint():
    assert "unit" in NumericGrader().grade({"answer": 0.1, "abs_tol": 1e-4}, "100").feedback


def test_all_numeric_content_answers_self_consistent():
    for e in CAT.all_exercises():
        if e.kind == "numeric":
            assert NumericGrader().grade(e.spec, str(e.spec["answer"])).passed, e.id


def test_choice():
    g, spec = ChoiceGrader(), {"options": ["a", "b", "c"], "correct": [1]}
    assert g.grade(spec, "1").passed
    assert not g.grade(spec, "0").passed
    assert not g.grade(spec, "").passed
    assert not g.grade(spec, "7").passed
    assert ChoiceGrader().grade({"options": ["a", "b", "c"], "correct": [0, 2]}, "2,0").passed


@needs_cc
@pytest.mark.parametrize("ex", C_EXERCISES, ids=lambda e: e.id)
def test_c_reference_passes(ex):
    r = CFunctionGrader().grade(ex.spec, ex.spec["reference"])
    assert r.passed, r.feedback


@needs_cc
@pytest.mark.parametrize("ex", C_EXERCISES, ids=lambda e: e.id)
def test_c_starter_fails(ex):
    assert not CFunctionGrader().grade(ex.spec, ex.spec["starter"]).passed


@needs_cc
@pytest.mark.parametrize("ex,i", [(e, i) for e in C_EXERCISES for i in range(len(e.spec.get("mutants", [])))],
                         ids=lambda v: str(v.id if hasattr(v, "id") else v))
def test_c_mutants_compile_but_fail(ex, i):
    """Harness strength: each known-wrong implementation must compile and be caught by a test."""
    r = CFunctionGrader().grade(ex.spec, ex.spec["mutants"][i])
    assert r.details.get("stage") != "compile", r.feedback
    assert not r.passed


@needs_cc
def test_c_compile_error_reported():
    ex = CAT.exercise("A01-E3")
    r = CFunctionGrader().grade(ex.spec, "uint32_t reg_set_field(uint32_t r, uint8_t p, uint8_t w, uint32_t v) { int x; return r; }")
    assert not r.passed and r.details["stage"] == "compile" and "your_code.c" in r.feedback


@needs_cc
def test_c_main_rejected():
    ex = CAT.exercise("A01-E3")
    assert "main" in CFunctionGrader().grade(ex.spec, "int main(void){return 0;}").feedback


@needs_cc
def test_c_infinite_loop_times_out():
    ex = CAT.exercise("A01-E3")
    code = "uint32_t reg_set_field(uint32_t reg, uint8_t pos, uint8_t width, uint32_t value)\n{ volatile int k = 1; (void)pos; (void)width; (void)value; while (k) { } return reg; }"
    r = CFunctionGrader(timeout_s=1.0).grade(ex.spec, code)
    assert not r.passed and "exceeded" in r.feedback


@needs_cc
def test_c_crash_reported():
    ex = CAT.exercise("A01-E4")
    code = ("uint32_t can_get_signal_le(const uint8_t data[8], uint8_t start_bit, uint8_t length)\n"
            "{ (void)start_bit; (void)length; volatile const uint8_t *p = data; return p[0] + *(volatile int *)0; }")
    r = CFunctionGrader().grade(ex.spec, code)
    assert not r.passed and "crashed" in r.feedback


def test_missing_compiler_is_infrastructure_error():
    ex = CAT.exercise("A01-E3")
    r = CFunctionGrader(compiler="definitely-not-a-compiler").grade(ex.spec, ex.spec["reference"])
    assert r.details.get("infrastructure_error")
