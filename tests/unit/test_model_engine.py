"""Model engine: block semantics, compilation rules, scheduling — the Simulink rules learners rely on."""
import pytest

from ecsim.model import LIB, ModelError, Simulator, compile_model


def M(blocks, lines, step=0.01, name="t"):
    return {"format": "ecsim-model", "version": 1, "name": name,
            "solver": {"type": "FixedStepDiscrete", "step": step},
            "blocks": blocks, "lines": [{"src": s, "dst": d} for s, d in lines]}


def siso(block_type, params=None, inputs=1, sample_time=-1):
    """Model: Inports -> block -> Outport."""
    blocks = [{"id": f"i{k}", "type": "Inport", "params": {"port": k}} for k in range(1, inputs + 1)]
    blocks += [{"id": "b", "type": block_type, "params": params or {}, "sample_time": sample_time},
               {"id": "o", "type": "Outport", "params": {"port": 1}}]
    lines = [(f"i{k}:1", f"b:{k}") for k in range(1, inputs + 1)] + [("b:1", "o:1")]
    return M(blocks, lines)


def run(model, inputs):
    sim = Simulator(compile_model(model))
    return [sim.step(u)[0] for u in inputs]


@pytest.mark.parametrize("bt,params,inputs,expected", [
    ("Gain", {"gain": -2.5}, [[2]], [-5]),
    ("Sum", {"signs": "+-+"}, [[1, 2, 10]], [9]),
    ("Product", {"ops": "*/"}, [[6, 3]], [2]),
    ("Abs", {}, [[-4]], [4]),
    ("MinMax", {"fn": "max", "inputs": 3}, [[1, 7, 3]], [7]),
    ("Saturation", {"upper": 1, "lower": -1}, [[5], [-5], [0.3]], [1, -1, 0.3]),
    ("RelationalOperator", {"op": ">="}, [[2, 2], [1, 2]], [1, 0]),
    ("LogicalOperator", {"op": "XOR", "inputs": 2}, [[1, 0], [1, 1]], [1, 0]),
    ("LogicalOperator", {"op": "NOT"}, [[0], [3]], [1, 0]),
    ("Switch", {"criteria": "u2 >= Threshold", "threshold": 0.5}, [[10, 0.5, 20], [10, 0.4, 20]], [10, 20]),
    ("Switch", {"criteria": "u2 ~= 0", "threshold": 0}, [[10, 0, 20]], [20]),
    ("Lookup1D", {"breakpoints": [0, 10, 20], "table": [0, 100, 150]}, [[5], [15], [-3], [30]], [50, 125, 0, 150]),
    ("Lookup1D", {"breakpoints": [0, 10], "table": [0, 100], "extrapolation": "linear"}, [[20], [-5]], [200, -50]),
    ("UnitDelay", {"initial": 7}, [[1], [2], [3]], [7, 1, 2]),
])
def test_block_semantics(bt, params, inputs, expected):
    n = len(inputs[0])
    assert run(siso(bt, params, inputs=n), inputs) == pytest.approx(expected)


def test_integrators_forward_and_backward_euler():
    fe = run(siso("DiscreteIntegrator", {"gain": 2}), [[5]] * 4)
    be = run(siso("DiscreteIntegrator", {"gain": 2, "method": "BackwardEuler"}), [[5]] * 4)
    assert fe == pytest.approx([0, 0.1, 0.2, 0.3])          # output lags: no feedthrough
    assert be == pytest.approx([0.1, 0.2, 0.3, 0.4])
    lim = run(siso("DiscreteIntegrator", {"gain": 100, "upper": 1.5, "lower": 0}), [[5]] * 5)
    assert max(lim) == pytest.approx(1.5)


def test_transfer_function_and_rate_limiter():
    assert run(siso("DiscreteTransferFcn", {"num": [1], "den": [1, -0.5]}), [[1]] * 4) == pytest.approx([0, 1, 1.5, 1.75])
    assert run(siso("DiscreteTransferFcn", {"num": [2, 0], "den": [1, 0]}), [[3]]) == pytest.approx([6])  # feedthrough
    # 10 units/s at 10 ms => 0.1 per step
    assert run(siso("RateLimiter", {"rising": 10, "falling": -20}), [[1]] * 3 + [[0]] * 2) == \
        pytest.approx([0.1, 0.2, 0.3, 0.1, 0.0])


def test_library_documents_simulink_equivalents():
    assert all(bt.simulink.startswith("simulink/") for bt in LIB.values())


def test_sorted_order_respects_feedthrough_only():
    # o <- g <- d(UnitDelay) <- g : loop broken by the delay, so it compiles.
    m = M([{"id": "g", "type": "Gain", "params": {"gain": 0.5}}, {"id": "d", "type": "UnitDelay", "params": {"initial": 1}},
           {"id": "o", "type": "Outport"}], [("d:1", "g:1"), ("g:1", "d:1"), ("g:1", "o:1")])
    c = compile_model(m)
    assert c.order.index("d") < c.order.index("g") < c.order.index("o")
    sim = Simulator(c)
    assert [sim.step([])[0] for _ in range(3)] == pytest.approx([0.5, 0.25, 0.125])


def test_algebraic_loop_reports_path():
    m = M([{"id": "i", "type": "Inport"}, {"id": "s", "type": "Sum", "params": {"signs": "++"}},
           {"id": "g", "type": "Gain"}, {"id": "o", "type": "Outport"}],
          [("i:1", "s:1"), ("g:1", "s:2"), ("s:1", "g:1"), ("s:1", "o:1")])
    with pytest.raises(ModelError, match=r"algebraic loop.*(g -> s -> g|s -> g -> s).*Unit Delay"):
        compile_model(m)


@pytest.mark.parametrize("mutate,msg", [
    (lambda m: m["lines"].pop(), "not connected"),
    (lambda m: m["lines"].append({"src": "i1:1", "dst": "o:1"}), "more than one line"),
    (lambda m: m["blocks"].append({"id": "x", "type": "Integrator"}), "unknown block type"),
    (lambda m: m["lines"].append({"src": "i1:3", "dst": "o:1"}), "output port"),
    (lambda m: m["lines"].append({"src": "i1:1", "dst": "nope:1"}), "unknown destination"),
    (lambda m: m["lines"].append({"src": "i", "dst": "o:1"}), "block:1"),
    (lambda m: m["blocks"].append(dict(m["blocks"][0])), "duplicate block id"),
    (lambda m: m["blocks"][1]["params"].update(lower=5), "lower limit is greater"),
    (lambda m: m["blocks"][1].update(sample_time=0.015), "not a multiple"),
    (lambda m: m["solver"].update(type="ode45"), "FixedStepDiscrete"),
    (lambda m: m.update(format="simulink"), "not an ECSim model"),
])
def test_compile_errors_are_actionable(mutate, msg):
    m = siso("Saturation", {"upper": 1, "lower": -1})
    mutate(m)
    with pytest.raises(ModelError, match=msg):
        compile_model(m)


def test_port_numbering_must_be_contiguous():
    m = siso("Gain")
    m["blocks"][0]["params"]["port"] = 2
    with pytest.raises(ModelError, match="without gaps"):
        compile_model(m)


def test_multirate_holds_and_warns_about_rate_transition():
    m = M([{"id": "i", "type": "Inport"},
           {"id": "slow", "type": "UnitDelay", "sample_time": 0.03},
           {"id": "g", "type": "Gain", "params": {"gain": 1}, "sample_time": 0.01},
           {"id": "o", "type": "Outport"}], [("i:1", "slow:1"), ("slow:1", "g:1"), ("g:1", "o:1")])
    c = compile_model(m)
    assert any("rate transition slow" in w for w in c.warnings)
    assert c.blocks["slow"].every == 3 and c.blocks["g"].every == 1
    sim = Simulator(c)
    out = [sim.step([float(k + 1)])[0] for k in range(7)]
    # slow samples at k = 0, 3, 6 and holds in between
    assert out == pytest.approx([0, 0, 0, 1, 1, 1, 4])


def test_inherited_sample_time_takes_fastest_input():
    m = M([{"id": "a", "type": "Constant", "params": {"value": 1}, "sample_time": 0.1},
           {"id": "b", "type": "Constant", "params": {"value": 2}, "sample_time": 0.02},
           {"id": "s", "type": "Sum", "params": {"signs": "++"}}, {"id": "o", "type": "Outport"}],
          [("a:1", "s:1"), ("b:1", "s:2"), ("s:1", "o:1")])
    assert compile_model(m).blocks["s"].ts == pytest.approx(0.02)


def test_nested_subsystems_flatten():
    inner = M([{"id": "x", "type": "Inport", "params": {"port": 1}}, {"id": "y", "type": "Inport", "params": {"port": 2}},
               {"id": "p", "type": "Product", "params": {"ops": "**"}}, {"id": "z", "type": "Outport", "params": {"port": 1}}],
              [("x:1", "p:1"), ("y:1", "p:2"), ("p:1", "z:1")])
    mid = M([{"id": "a", "type": "Inport"}, {"id": "k", "type": "Constant", "params": {"value": 3}},
             {"id": "S", "type": "Subsystem", "params": {"model": inner}}, {"id": "b", "type": "Outport"}],
            [("a:1", "S:1"), ("k:1", "S:2"), ("S:1", "b:1")])
    top = M([{"id": "u", "type": "Inport"}, {"id": "Mid", "type": "Subsystem", "params": {"model": mid}},
             {"id": "y", "type": "Outport"}], [("u:1", "Mid:1"), ("Mid:1", "y:1")])
    c = compile_model(top)
    assert "Mid/S/p" in c.blocks and c.inport_names == ["u"]
    assert Simulator(c).step([4])[0] == pytest.approx(12)


def test_simulator_input_validation():
    sim = Simulator(compile_model(siso("Gain")))
    with pytest.raises(ModelError, match="1 inport"):
        sim.step([1, 2])
    with pytest.raises(ModelError, match="non-finite"):
        sim.step([float("nan")])
    with pytest.raises(ModelError, match="missing input"):
        sim.step({"wrong": 1})
    assert sim.step({"i1": 3}) == [3]
