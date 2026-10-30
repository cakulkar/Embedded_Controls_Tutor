"""Guidelines, coverage, requirements/ReqIF/traceability, test-design grading."""
import json

import pytest

from ecsim.app.config import CONTENT_DIR
from ecsim.learning.content import load_catalog
from ecsim.learning.graders import TestDesignGrader
from ecsim.model import Simulator, compile_model
from ecsim.model.coverage import CoverageCollector, outcomes_for
from ecsim.model.guidelines import RULES, check_model
from ecsim.model.mil import CheckOutcome
from ecsim.verify.requirements import (Requirement, RequirementError, RequirementStore, from_reqif, import_into,
                                       to_reqif, trace_matrix)

CAT = load_catalog(CONTENT_DIR)


def M(blocks, lines, name="g"):
    return {"format": "ecsim-model", "version": 1, "name": name, "solver": {"type": "FixedStepDiscrete", "step": 0.01},
            "blocks": blocks, "lines": [{"src": s, "dst": d} for s, d in lines]}


# ---- guidelines ------------------------------------------------------------------
def rules(model):
    return {f.rule for f in check_model(model)}


def test_clean_reference_model_has_no_findings():
    assert check_model(CAT.exercise("B02-E4").spec["reference"]) == []


def test_each_rule_triggers():
    m = M([{"id": "Gain1", "type": "Gain"}, {"id": "speed", "type": "Inport", "params": {"port": 1, "name": "speed"}},
           {"id": "q", "type": "Product", "params": {"ops": "*/"}}, {"id": "eq", "type": "RelationalOperator", "params": {"op": "=="}},
           {"id": "lut", "type": "Lookup1D", "params": {"breakpoints": [0, 1], "table": [0, 1], "extrapolation": "linear"}},
           {"id": "integ", "type": "DiscreteIntegrator"}, {"id": "slow", "type": "UnitDelay", "sample_time": 0.05},
           {"id": "out_V", "type": "Outport", "params": {"port": 1, "name": "out_V"}}],
          [("speed:1", "Gain1:1"), ("Gain1:1", "q:1"), ("speed:1", "q:2"), ("q:1", "eq:1"), ("speed:1", "eq:2"),
           ("eq:1", "lut:1"), ("lut:1", "integ:1"), ("integ:1", "slow:1"), ("slow:1", "out_V:1")])
    got = rules(m)
    for r in ("ECS-NAM-01", "ECS-NAM-02", "ECS-DIV-01", "ECS-REL-01", "ECS-LUT-01", "ECS-INT-01", "ECS-RT-01"):
        assert r in got, r
    big = M([{"id": f"k{i}", "type": "Constant"} for i in range(26)] + [{"id": "t_V", "type": "Outport", "params": {"name": "t_V"}}],
            [("k0:1", "t_V:1")])
    assert {"ECS-CPX-01", "ECS-SIG-01"} <= rules(big)
    assert all(r in RULES for r in got)


def test_chart_rules():
    spec = {"inputs": ["a"], "outputs": ["y", "z"], "initial": "A",
            "states": [{"name": "A", "entry": "y = 1"}, {"name": "B", "entry": "y = 0; z = 0"}, {"name": "Dead", "entry": "y = 2; z = 2"}],
            "transitions": [{"from": "A", "to": "B", "guard": "a > 0"}]}
    m = M([{"id": "a_cmd", "type": "Inport", "params": {"name": "a_cmd"}}, {"id": "ch", "type": "Chart", "params": {"chart": spec}},
           {"id": "y_V", "type": "Outport", "params": {"port": 1, "name": "y_V"}}, {"id": "z_V", "type": "Outport", "params": {"port": 2, "name": "z_V"}}],
          [("a_cmd:1", "ch:1"), ("ch:1", "y_V:1"), ("ch:2", "z_V:1")])
    f = check_model(m)
    assert any(x.rule == "ECS-CHT-01" and x.where.endswith(".A") for x in f)
    assert any(x.rule == "ECS-CHT-02" and x.where.endswith(".Dead") for x in f)
    assert any(x.rule == "ECS-CHT-03" and x.where.endswith(".B") for x in f)


# ---- coverage --------------------------------------------------------------------
def test_coverage_outcomes_and_collection():
    m = M([{"id": "u", "type": "Inport"}, {"id": "sat", "type": "Saturation", "params": {"upper": 1, "lower": -1}},
           {"id": "cmp", "type": "RelationalOperator", "params": {"op": ">"}}, {"id": "z", "type": "Constant", "params": {"value": 0}},
           {"id": "sw", "type": "Switch", "params": {"threshold": 0.5}}, {"id": "mm", "type": "MinMax", "params": {"fn": "max", "inputs": 2}},
           {"id": "rl", "type": "RateLimiter", "params": {"rising": 10, "falling": -10}}, {"id": "ab", "type": "Abs"},
           {"id": "o", "type": "Outport"}, {"id": "o2", "type": "Outport", "params": {"port": 2}}, {"id": "o3", "type": "Outport", "params": {"port": 3}},
           {"id": "o4", "type": "Outport", "params": {"port": 4}}],
          [("u:1", "sat:1"), ("sat:1", "cmp:1"), ("z:1", "cmp:2"), ("u:1", "sw:1"), ("cmp:1", "sw:2"), ("z:1", "sw:3"),
           ("sw:1", "o:1"), ("u:1", "mm:1"), ("z:1", "mm:2"), ("mm:1", "o2:1"), ("u:1", "rl:1"), ("rl:1", "o3:1"),
           ("u:1", "ab:1"), ("ab:1", "o4:1")])
    c = compile_model(m)
    assert sum(len(v) for v in outcomes_for(c).values()) == 3 + 2 + 2 + 2 + 3 + 2
    col = CoverageCollector(c)
    sim = col.attach(Simulator(c))
    sim.step([0.0])
    assert col.report.ratio < 0.5
    for v in (5.0, -5.0, 0.0, 0.05, 0.0):
        sim.step([v])
    assert col.report.ratio == 1.0, col.report.summary()


# ---- requirements ---------------------------------------------------------------
def test_requirement_store_rules(ctx, learner):
    st = RequirementStore(ctx.conn, learner)
    st.upsert(Requirement("SYS-1", "system", "Hold speed"))
    st.upsert(Requirement("SW-1", "software", "PI controller", parent_req_id="SYS-1"))
    with pytest.raises(RequirementError, match="does not exist"):
        st.upsert(Requirement("SW-2", "software", "x", parent_req_id="NOPE"))
    with pytest.raises(RequirementError, match="cycle"):
        st.upsert(Requirement("SYS-1", "system", "Hold speed", parent_req_id="SW-1"))
    with pytest.raises(RequirementError, match="letters"):
        st.upsert(Requirement("1 bad", "system", "x"))
    with pytest.raises(RequirementError, match="child"):
        st.delete("SYS-1")
    st.link("SW-1", "verifies", "B02-E4:CL1")
    st.link("SW-1", "verifies", "B02-E4:CL1")                 # idempotent
    assert st.links("SW-1") == [("SW-1", "verifies", "B02-E4:CL1")]
    st.delete("SW-1")
    assert st.links() == [] and [r.req_id for r in st.all()] == ["SYS-1"]


def test_import_requirements_from_exercise_and_trace(ctx, learner):
    st = RequirementStore(ctx.conn, learner)
    spec = CAT.exercise("B02-E4").spec
    assert st.import_exercise("B02-E4", spec) == 8
    assert ("REQ-1", "verifies", "B02-E4:CL1 closed loop, nominal") in st.links("REQ-1")
    outs = [CheckOutcome("T", "REQ-1 speed ok", True, ""), CheckOutcome("T", "REQ-2 range", False, ""), CheckOutcome("T", "REQ-10 x", True, "")]
    rows = {r.req_id: r for r in trace_matrix(st.all(), outs)}
    assert rows["REQ-1"].status == "passed" and rows["REQ-2"].status == "failed" and rows["REQ-3"].status == "not verified"
    assert len(rows["REQ-1"].checks) == 1                      # REQ-10 must not match REQ-1


def test_reqif_round_trip_and_foreign_import(ctx, learner):
    reqs = [Requirement("SYS-1", "system", "Hold speed", "The motor shall …", "±60 rpm", None, "approved"),
            Requirement("SW-1", "software", "PI <controller> & \"anti-windup\"", parent_req_id="SYS-1")]
    xml = to_reqif(reqs)
    back, warns = from_reqif(xml)
    assert back == reqs and warns == []
    st = RequirementStore(ctx.conn, learner)
    assert import_into(st, list(reversed(back))) == []         # child listed first: still imported
    foreign = xml.replace('LONG-NAME="ECSIM.Level"', 'LONG-NAME="Priority"').replace('THE-VALUE="system"', 'THE-VALUE="high"')
    _r, warns = from_reqif(foreign)
    assert any("Priority" in w for w in warns)
    with pytest.raises(RequirementError, match="not a ReqIF"):
        from_reqif("<root/>")


# ---- test design ----------------------------------------------------------------
TD = CAT.exercise("B05-E4").spec


def test_test_design_reference_passes_weak_and_starter_fail():
    g = TestDesignGrader()
    assert g.grade(TD, TD["reference"]).passed
    weak = g.grade(TD, TD["weak_suites"][0])
    assert not weak.passed and "100%" in weak.feedback and "5/6" in weak.feedback   # full coverage, one fault survives
    assert not g.grade(TD, TD["starter"]).passed


@pytest.mark.parametrize("bad,msg", [
    ("not json", "Not valid JSON"), ('{"tests": []}', "at least one test"),
    ('{"tests": [{"name": "a", "duration": 1, "inputs": {}, "checks": []}]}', "no signal given"),
    ('{"tests": [{"name": "a", "duration": 999, "inputs": {}, "checks": []}]}', "duration"),
])
def test_test_design_validation(bad, msg):
    assert msg in TestDesignGrader().grade(TD, bad).feedback


def test_test_with_wrong_expectation_is_reported():
    ref = json.loads(TD["reference"])
    ref["tests"][0]["checks"][3]["min"] = ref["tests"][0]["checks"][3]["max"] = 1
    r = TestDesignGrader().grade(TD, json.dumps(ref))
    assert not r.passed and "wrong expectation" in r.feedback
