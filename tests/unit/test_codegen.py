"""Code generation: structure, warning-free compile, and bit-exact MIL/SIL equivalence (double)."""
import json
import math

import pytest

from ecsim.model import ModelError, Simulator, compile_model
from ecsim.model.codegen import generate_c
from ecsim.model.sil import SilBackend, build_sil, SilModel

from conftest import needs_cc


def M(blocks, lines, step=0.01, name="cg"):
    return {"format": "ecsim-model", "version": 1, "name": name, "solver": {"type": "FixedStepDiscrete", "step": step},
            "blocks": blocks, "lines": [{"src": s, "dst": d} for s, d in lines]}


def siso(bt, params=None, n_in=1, st=-1):
    blocks = [{"id": f"i{k}", "type": "Inport", "params": {"port": k}} for k in range(1, n_in + 1)]
    blocks += [{"id": "b", "type": bt, "params": params or {}, "sample_time": st}, {"id": "o", "type": "Outport", "params": {"port": 1}}]
    return M(blocks, [(f"i{k}:1", f"b:{k}") for k in range(1, n_in + 1)] + [("b:1", "o:1")])


def stimulus(n_in, n=40):
    vals = [-3.0, -0.5, 0.0, 0.25, 1.0, 2.5, 7.0, 0.0, -1.0, 4.0]
    return [[vals[(k + 3 * j) % len(vals)] for j in range(n_in)] for k in range(n)]


def mil_vs_sil(model, n_in, tmp_path, dtype="double"):
    c = compile_model(model)
    b = build_sil(generate_c(c, dtype), tmp_path)
    assert b.ok, b.log
    sim, sil = Simulator(c), SilModel(b)
    try:
        out = []
        for u in stimulus(n_in):
            out.append((sim.step(u), sil.step(u)))
        return out
    finally:
        sil.close()


CASES = [
    ("Gain", {"gain": -2.5}, 1), ("Sum", {"signs": "+-+"}, 3), ("Product", {"ops": "*/"}, 2), ("Product", {"ops": "/"}, 1),
    ("Abs", {}, 1), ("MinMax", {"fn": "min", "inputs": 3}, 3), ("MinMax", {"fn": "max", "inputs": 1}, 1),
    ("Saturation", {"upper": 1.5, "lower": -0.75}, 1), ("RelationalOperator", {"op": "~="}, 2),
    ("LogicalOperator", {"op": "XOR", "inputs": 3}, 3), ("LogicalOperator", {"op": "NOT"}, 1),
    ("LogicalOperator", {"op": "NAND", "inputs": 2}, 2),
    ("Switch", {"criteria": "u2 > Threshold", "threshold": 0.25}, 3), ("Switch", {"criteria": "u2 ~= 0", "threshold": 0}, 3),
    ("Lookup1D", {"breakpoints": [-1, 0, 2, 5], "table": [3, 0, 1, -2]}, 1),
    ("Lookup1D", {"breakpoints": [-1, 0, 2, 5], "table": [3, 0, 1, -2], "extrapolation": "linear"}, 1),
    ("UnitDelay", {"initial": 4}, 1), ("DiscreteIntegrator", {"gain": 3, "initial": 1, "upper": 2, "lower": -1}, 1),
    ("DiscreteIntegrator", {"gain": 0.5, "method": "BackwardEuler"}, 1),
    ("DiscreteTransferFcn", {"num": [0.2, 0.1], "den": [1, -0.7, 0.1]}, 1), ("DiscreteTransferFcn", {"num": [2], "den": [4]}, 1),
    ("RateLimiter", {"rising": 50, "falling": -120, "initial": 1}, 1),
]


@needs_cc
@pytest.mark.parametrize("bt,params,n", CASES, ids=[f"{c[0]}-{i}" for i, c in enumerate(CASES)])
def test_every_block_is_bit_exact_in_double(bt, params, n, tmp_path):
    for a, b in mil_vs_sil(siso(bt, params, n), n, tmp_path):
        assert a == b or (all(math.isinf(x) for x in a) and a == b)


@needs_cc
def test_multirate_subsystem_and_constants(tmp_path):
    inner = M([{"id": "x", "type": "Inport", "params": {"port": 1}}, {"id": "d", "type": "UnitDelay", "sample_time": 0.03},
               {"id": "y", "type": "Outport", "params": {"port": 1}}], [("x:1", "d:1"), ("d:1", "y:1")])
    m = M([{"id": "u", "type": "Inport"}, {"id": "S", "type": "Subsystem", "params": {"model": inner}},
           {"id": "k", "type": "Constant", "params": {"value": 0.5}}, {"id": "s", "type": "Sum", "params": {"signs": "++"}},
           {"id": "o", "type": "Outport"}], [("u:1", "S:1"), ("S:1", "s:1"), ("k:1", "s:2"), ("s:1", "o:1")])
    for a, b in mil_vs_sil(m, 1, tmp_path):
        assert a == b


ORDER = {"inputs": ["go"], "outputs": ["log"], "initial": "A",
         "states": [{"name": "A", "initial": "A1", "entry": "log = log*10 + 1", "exit": "log = log*10 + 2"},
                    {"name": "A1", "parent": "A", "entry": "log = log*10 + 3", "exit": "log = log*10 + 4", "during": "log = log*10 + 9"},
                    {"name": "B", "entry": "log = log*10 + 6"},
                    {"name": "C", "entry": "log = 0"}],
         "transitions": [{"from": "A", "to": "B", "guard": "go == 1", "action": "log = log*10 + 5"},
                         {"from": "B", "to": "C", "guard": "after(2, tick) && go ~= 1"},
                         {"from": "C", "to": "A1", "guard": "after(0.03, sec)"}]}


@needs_cc
def test_chart_semantics_survive_code_generation(tmp_path):
    m = M([{"id": "go", "type": "Inport"}, {"id": "ch", "type": "Chart", "params": {"chart": ORDER}},
           {"id": "o", "type": "Outport"}], [("go:1", "ch:1"), ("ch:1", "o:1")])
    c = compile_model(m)
    b = build_sil(generate_c(c), tmp_path)
    assert b.ok, b.log
    sim, sil = Simulator(c), SilModel(b)
    seq = [0, 0, 1, 0, 0, 0, 0, 0, 0, 1, 1, 0, 0, 0, 0, 0, 0, 0]
    got = [(sim.step([v]), sil.step([v])) for v in seq]
    sil.close()
    assert all(a == b for a, b in got), got
    assert got[2][0] == [1394256] and got[-1][0][0] > 0


def test_generated_structure_and_single_precision():
    m = siso("Gain", {"gain": 0.1})
    d, s = generate_c(m, "double"), generate_c(m, "single")
    assert "typedef double real_T;" in d.header and "typedef float real_T;" in s.header
    assert "0.1f" in s.source and "0.1f" not in d.source
    for frag in ("ExtU_cg cg_U;", "ExtY_cg cg_Y;", "DW_cg cg_DW;", "void cg_initialize(void)", "void cg_step(void)"):
        assert frag in d.source or frag in d.header
    assert "malloc" not in d.source
    with pytest.raises(ModelError, match="double' or 'single"):
        generate_c(m, "half")


@needs_cc
def test_model_exercises_bit_exact_in_double():
    from ecsim.app.config import CONTENT_DIR
    from ecsim.learning.content import load_catalog
    from ecsim.model.sil import back_to_back
    for e in load_catalog(CONTENT_DIR).all_exercises():
        if e.kind in ("model_mil", "model_sil"):
            rep = back_to_back(e.spec["reference"], e.spec, "double", 0.0)
            assert rep.equivalent, (e.id, [c for c in rep.comparisons if not c.passed][:2])


@needs_cc
def test_deploy_generated_controller_to_virtual_ecu():
    from ecsim.app.config import CONTENT_DIR
    from ecsim.learning.content import load_catalog
    from ecsim.model.deploy import deploy_and_run, motor_firmware
    ref = load_catalog(CONTENT_DIR).exercise("B02-E4").spec["reference"]
    b, res = deploy_and_run(ref, "TC3XX_LIKE", ["motor_load_step"])
    assert b.ok and res["motor_load_step"].ok and res["motor_load_step"].result.passed
    bad = json.loads(ref)
    bad["solver"]["step"] = 0.02
    with pytest.raises(ModelError, match="task_10ms"):
        motor_firmware(bad)
