"""Stateflow-style chart semantics, expression language, validation, SCXML."""
import pytest

from ecsim.model import ModelError, Simulator, compile_model
from ecsim.model.chart import Chart, from_scxml, to_scxml


def run(spec, inputs, ts=0.01):
    c = Chart(spec)
    st = c.init_state()
    return [c.step(st, u, ts) for u in inputs], st


# Each action appends a digit to `log`, so the final number encodes execution order.
ORDER = {
    "inputs": ["go"], "outputs": ["log"], "initial": "A",
    "states": [{"name": "A", "initial": "A1", "entry": "log = log*10 + 1", "exit": "log = log*10 + 2"},
               {"name": "A1", "parent": "A", "entry": "log = log*10 + 3", "exit": "log = log*10 + 4",
                "during": "log = log*10 + 9"},
               {"name": "B", "entry": "log = log*10 + 6"}],
    "transitions": [{"from": "A", "to": "B", "guard": "go == 1", "action": "log = log*10 + 5"}]}


def test_entry_outer_to_inner_then_during():
    out, st = run(ORDER, [[0], [0]])
    assert out[0] == [13] and out[1] == [139] and st["active"] == ["A", "A1"]


def test_firing_order_exit_inner_outer_action_entry():
    out, st = run(ORDER, [[0], [1]])
    assert out[1] == [134256] and st["active"] == ["B"]


def test_outer_transitions_checked_first_and_priority():
    spec = {"inputs": ["a", "b"], "outputs": ["y"], "initial": "P",
            "states": [{"name": "P", "initial": "C"}, {"name": "C", "parent": "P", "entry": "y = 1"},
                       {"name": "Inner", "parent": "P", "entry": "y = 2"}, {"name": "Outer", "entry": "y = 3"},
                       {"name": "Low", "entry": "y = 4"}],
            "transitions": [{"from": "C", "to": "Inner", "guard": "b == 1"},
                            {"from": "P", "to": "Low", "guard": "a == 1", "priority": 2},
                            {"from": "P", "to": "Outer", "guard": "a == 1", "priority": 1}]}
    out, _ = run(spec, [[0, 0], [1, 1]])
    assert out[1] == [3]                         # parent beats child; priority 1 beats 2


def test_temporal_logic_counts_from_entry_and_resets_on_reentry():
    spec = {"inputs": ["r"], "outputs": ["y"], "initial": "W",
            "states": [{"name": "W", "entry": "y = 0"}, {"name": "D", "entry": "y = 1"}],
            "transitions": [{"from": "W", "to": "W", "guard": "r == 1"},          # self-transition re-enters
                            {"from": "W", "to": "D", "guard": "after(3, tick)"}]}
    out, _ = run(spec, [[0], [0], [0], [0]])
    assert [o[0] for o in out] == [0, 0, 0, 1]
    out, _ = run(spec, [[0], [0], [1], [0], [0], [0]])
    assert [o[0] for o in out] == [0, 0, 0, 0, 0, 1]
    sec = dict(spec, transitions=[{"from": "W", "to": "D", "guard": "after(0.05, sec)"}])
    out, _ = run(sec, [[0]] * 7)
    assert [o[0] for o in out].index(1) == 5      # 5 executions × 10 ms after entry


def test_transition_into_child_and_to_ancestor():
    spec = {"inputs": ["k"], "outputs": ["y"], "initial": "P",
            "states": [{"name": "P", "initial": "X", "entry": "y = y + 100"}, {"name": "X", "parent": "P", "entry": "y = y + 1"},
                       {"name": "Z", "parent": "P", "entry": "y = y + 10"}],
            "transitions": [{"from": "X", "to": "Z", "guard": "k == 1"}, {"from": "Z", "to": "P", "guard": "k == 2"}]}
    out, st = run(spec, [[0], [1], [2]])
    assert out[1] == [111] and st["active"] == ["P", "X"]       # P not re-entered when going to sibling
    assert out[2] == [112]                                      # to ancestor: default child re-entered


def test_expression_language():
    spec = {"inputs": ["a", "b"], "outputs": ["y"], "locals": {"n": 5}, "initial": "S",
            "states": [{"name": "S", "during": "y = (a >= 1 && !(b ~= 2)) || false; n -= 1; n += 0.5"}]}
    out, st = run(spec, [[0, 0], [1, 2], [1, 3]])
    assert [o[0] for o in out] == [0, 1, 0] and st["env"]["n"] == 4.0
    div = dict(spec, states=[{"name": "S", "during": "y = a / b"}])
    assert run(div, [[0, 0], [1, 0]])[0][1] == [float("inf")]


@pytest.mark.parametrize("change,msg", [
    (lambda s: s["states"][0].update(entry="a = 1"), "only outputs and locals are writable"),
    (lambda s: s["states"][0].update(entry="y = q"), "unknown name 'q'"),
    (lambda s: s["states"][0].update(entry="y = __import__('os')"), "unsupported syntax"),
    (lambda s: s["states"][0].update(entry="y == 1"), "must look like"),
    (lambda s: s["transitions"].append({"from": "S", "to": "Nope"}), "unknown state"),
    (lambda s: s["transitions"].append({"from": "S", "to": "S", "guard": "after(2)"}), "after\\(n, tick\\)"),
    (lambda s: s.update(initial="Nope"), "top-level state"),
    (lambda s: s["states"].append({"name": "S"}), "duplicate state"),
    (lambda s: s["states"].append({"name": "K", "parent": "S"}), "needs 'initial'"),
    (lambda s: s.update(outputs=[]), "at least one output"),
    (lambda s: s.update(parallel=True), "not supported yet"),
    (lambda s: s.update(inputs=["a", "a"]), "unique"),
])
def test_validation_messages(change, msg):
    spec = {"inputs": ["a"], "outputs": ["y"], "initial": "S", "states": [{"name": "S"}], "transitions": []}
    change(spec)
    with pytest.raises(ModelError, match=msg):
        Chart(spec)


def test_scxml_round_trip_preserves_behaviour():
    from ecsim.app.config import CONTENT_DIR
    from ecsim.learning.content import load_catalog
    import json
    m = json.loads(load_catalog(CONTENT_DIR).exercise("B03-E4").spec["reference"])
    spec = next(b for b in m["blocks"] if b["type"] == "Chart")["params"]["chart"]
    xml = to_scxml(spec)
    assert "<scxml" in xml and 'cond="after(2, sec)"' in xml
    back = from_scxml(xml)
    stim = [[1, 0, 0]] * 10 + [[1, 800, 0]] * 5 + [[1, 800, 1]] * 3 + [[0, 0, 0]] * 3
    assert run(back, stim)[0] == run(spec, stim)[0]
    with pytest.raises(ModelError, match="invalid SCXML"):
        from_scxml("<scxml")


def test_chart_block_in_model_has_feedthrough_and_ports():
    m = {"format": "ecsim-model", "version": 1, "name": "c", "solver": {"type": "FixedStepDiscrete", "step": 0.01},
         "blocks": [{"id": "go", "type": "Inport"}, {"id": "ch", "type": "Chart", "params": {"chart": ORDER}},
                    {"id": "o", "type": "Outport"}],
         "lines": [{"src": "go:1", "dst": "ch:1"}, {"src": "ch:1", "dst": "o:1"}]}
    sim = Simulator(compile_model(m))
    assert [sim.step([v])[0] for v in (0, 1)] == [13, 134256]
    m["blocks"][1]["params"]["chart"] = {"inputs": [], "outputs": ["y"], "initial": "Q", "states": []}
    with pytest.raises(ModelError, match="at least one state"):
        compile_model(m)
