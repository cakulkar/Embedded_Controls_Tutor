"""Block library with Simulink semantics (discrete, fixed-step).

Every block declares:
* number of input/output ports (may depend on parameters),
* which inputs have **direct feedthrough** (output at step k depends on input at step k),
* `output(state, u)` and `update(state, u)`, called by the scheduler in Simulink order:
  all outputs in sorted order, then all state updates.

Names and parameter meanings follow the corresponding Simulink blocks so skills transfer.
Only scalar double signals are supported in Phase 3 (vectors/buses/fixed-point: Phases 4/6).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable


class ModelError(ValueError):
    pass


@dataclass
class BlockCtx:
    """Per-instance compiled info handed to block functions."""
    params: dict[str, Any]
    ts: float                     # resolved sample time [s]


@dataclass(frozen=True)
class BlockType:
    name: str
    simulink: str                 # library path of the equivalent Simulink block
    n_in: Callable[[dict], int]
    n_out: Callable[[dict], int]
    feedthrough: Callable[[dict], list[bool]]
    init: Callable[[BlockCtx], Any]
    output: Callable[[BlockCtx, Any, list[float]], list[float]]
    update: Callable[[BlockCtx, Any, list[float]], Any] | None = None
    defaults: dict[str, Any] | None = None
    doc: str = ""


def _num(p: dict, key: str) -> float:
    try:
        v = float(p[key])
    except (KeyError, TypeError, ValueError):
        raise ModelError(f"parameter '{key}' must be a number (got {p.get(key)!r})") from None
    if math.isnan(v):
        raise ModelError(f"parameter '{key}' is NaN")
    return v


def _const(n: int) -> Callable[[dict], int]:
    return lambda p: n


# ---- math -------------------------------------------------------------------
def _sum_out(c: BlockCtx, s, u):
    total = 0.0
    for sign, v in zip(c.params["signs"], u):
        total += v if sign == "+" else -v
    return [total]


def _prod_out(c: BlockCtx, s, u):
    y = 1.0
    for op, v in zip(c.params["ops"], u):
        if op == "*":
            y *= v
        else:
            y = math.copysign(math.inf, y) if v == 0.0 else y / v
    return [y]


def _sat_out(c: BlockCtx, s, u):
    lo, hi = _num(c.params, "lower"), _num(c.params, "upper")
    return [min(max(u[0], lo), hi)]


def _rel_out(c: BlockCtx, s, u):
    a, b = u
    op = c.params["op"]
    r = {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b, "==": a == b, "~=": a != b}[op]
    return [1.0 if r else 0.0]


def _logic_out(c: BlockCtx, s, u):
    op = c.params["op"]
    b = [v != 0.0 for v in u]
    r = {"AND": all(b), "OR": any(b), "NAND": not all(b), "NOR": not any(b),
         "XOR": sum(b) % 2 == 1, "NOT": not b[0]}[op]
    return [1.0 if r else 0.0]


def _switch_out(c: BlockCtx, s, u):
    crit, th = c.params["criteria"], _num(c.params, "threshold")
    u1, u2, u3 = u
    take = u2 >= th if crit == "u2 >= Threshold" else (u2 > th if crit == "u2 > Threshold" else u2 != 0.0)
    return [u1 if take else u3]


def _lut_out(c: BlockCtx, s, u):
    bp, tab = c.params["breakpoints"], c.params["table"]
    x = u[0]
    if x <= bp[0] or x >= bp[-1]:
        if c.params.get("extrapolation", "clip") == "clip":
            return [tab[0] if x <= bp[0] else tab[-1]]
        i = 0 if x <= bp[0] else len(bp) - 2
    else:
        lo, hi = 0, len(bp) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if bp[mid] <= x:
                lo = mid
            else:
                hi = mid
        i = lo
    f = (x - bp[i]) / (bp[i + 1] - bp[i])
    return [tab[i] + f * (tab[i + 1] - tab[i])]


def _minmax_out(c: BlockCtx, s, u):
    return [min(u) if c.params["fn"] == "min" else max(u)]


# ---- discrete dynamics ----------------------------------------------------------
def _di_init(c: BlockCtx):
    return {"x": _num(c.params, "initial")}


def _di_clamp(c: BlockCtx, x: float) -> float:
    if "upper" in c.params:
        x = min(x, _num(c.params, "upper"))
    if "lower" in c.params:
        x = max(x, _num(c.params, "lower"))
    return x


def _di_out(c: BlockCtx, s, u):
    if c.params["method"] == "ForwardEuler":
        return [s["x"]]
    # Backward Euler: y(k) = x(k-1) + K*T*u(k)
    return [_di_clamp(c, s["x"] + _num(c.params, "gain") * c.ts * u[0])]


def _di_update(c: BlockCtx, s, u):
    s["x"] = _di_clamp(c, s["x"] + _num(c.params, "gain") * c.ts * u[0])
    return s


def _tf_init(c: BlockCtx):
    num, den = [float(v) for v in c.params["num"]], [float(v) for v in c.params["den"]]
    if not den or den[0] == 0.0:
        raise ModelError("denominator leading coefficient must be non-zero")
    if len(num) > len(den):
        raise ModelError("improper transfer function (numerator order > denominator order)")
    n = len(den) - 1
    num = [0.0] * (len(den) - len(num)) + num
    a0 = den[0]
    return {"b": [v / a0 for v in num], "a": [v / a0 for v in den], "z": [0.0] * n}


def _tf_out(c: BlockCtx, s, u):
    # Direct form II transposed: y = b0*u + z0
    z = s["z"]
    return [s["b"][0] * u[0] + (z[0] if z else 0.0)]


def _tf_update(c: BlockCtx, s, u):
    b, a, z = s["b"], s["a"], s["z"]
    n = len(z)
    y = b[0] * u[0] + (z[0] if z else 0.0)
    for i in range(n):
        nxt = z[i + 1] if i + 1 < n else 0.0
        z[i] = nxt + b[i + 1] * u[0] - a[i + 1] * y
    return s


def _rl_init(c: BlockCtx):
    return {"y": _num(c.params, "initial")}


def _rl_out(c: BlockCtx, s, u):
    """Slew from the previous output ('initial' before the first step) toward the input."""
    rise, fall = _num(c.params, "rising") * c.ts, _num(c.params, "falling") * c.ts
    d = u[0] - s["y"]
    return [s["y"] + min(max(d, fall), rise)]


def _rl_update(c: BlockCtx, s, u):
    s["y"] = _rl_out(c, s, u)[0]
    return s


def _ud_out(c, s, u):
    return [s["x"]]


def _ud_update(c, s, u):
    s["x"] = u[0]
    return s


LIB: dict[str, BlockType] = {}


def _reg(bt: BlockType) -> None:
    LIB[bt.name] = bt


_reg(BlockType("Inport", "simulink/Sources/In1", _const(0), _const(1), lambda p: [],
               lambda c: None, lambda c, s, u: [0.0], defaults={"port": 1}, doc="Model input."))
_reg(BlockType("Outport", "simulink/Sinks/Out1", _const(1), _const(0), lambda p: [True],
               lambda c: None, lambda c, s, u: [], defaults={"port": 1}, doc="Model output."))
_reg(BlockType("Constant", "simulink/Sources/Constant", _const(0), _const(1), lambda p: [],
               lambda c: None, lambda c, s, u: [_num(c.params, "value")], defaults={"value": 0.0}))
_reg(BlockType("Ground", "simulink/Sources/Ground", _const(0), _const(1), lambda p: [],
               lambda c: None, lambda c, s, u: [0.0]))
_reg(BlockType("Terminator", "simulink/Sinks/Terminator", _const(1), _const(0), lambda p: [False],
               lambda c: None, lambda c, s, u: []))
_reg(BlockType("Gain", "simulink/Math Operations/Gain", _const(1), _const(1), lambda p: [True],
               lambda c: None, lambda c, s, u: [_num(c.params, "gain") * u[0]], defaults={"gain": 1.0}))
_reg(BlockType("Sum", "simulink/Math Operations/Sum", lambda p: len(p["signs"]), _const(1),
               lambda p: [True] * len(p["signs"]), lambda c: None, _sum_out, defaults={"signs": "++"},
               doc="signs: string of '+'/'-', one per input (Simulink 'List of signs')."))
_reg(BlockType("Product", "simulink/Math Operations/Product", lambda p: len(p["ops"]), _const(1),
               lambda p: [True] * len(p["ops"]), lambda c: None, _prod_out, defaults={"ops": "**"}))
_reg(BlockType("Abs", "simulink/Math Operations/Abs", _const(1), _const(1), lambda p: [True],
               lambda c: None, lambda c, s, u: [abs(u[0])]))
_reg(BlockType("MinMax", "simulink/Math Operations/MinMax", lambda p: int(p["inputs"]), _const(1),
               lambda p: [True] * int(p["inputs"]), lambda c: None, _minmax_out,
               defaults={"fn": "min", "inputs": 2}))
_reg(BlockType("Saturation", "simulink/Discontinuities/Saturation", _const(1), _const(1), lambda p: [True],
               lambda c: None, _sat_out, defaults={"upper": 0.5, "lower": -0.5}))
_reg(BlockType("RelationalOperator", "simulink/Logic and Bit Operations/Relational Operator", _const(2),
               _const(1), lambda p: [True, True], lambda c: None, _rel_out, defaults={"op": "<="}))
_reg(BlockType("LogicalOperator", "simulink/Logic and Bit Operations/Logical Operator",
               lambda p: 1 if p["op"] == "NOT" else int(p.get("inputs", 2)), _const(1),
               lambda p: [True] * (1 if p["op"] == "NOT" else int(p.get("inputs", 2))),
               lambda c: None, _logic_out, defaults={"op": "AND", "inputs": 2}))
_reg(BlockType("Switch", "simulink/Signal Routing/Switch", _const(3), _const(1), lambda p: [True] * 3,
               lambda c: None, _switch_out, defaults={"criteria": "u2 >= Threshold", "threshold": 0.0},
               doc="Passes input 1 when the criterion on input 2 holds, else input 3."))
_reg(BlockType("Lookup1D", "simulink/Lookup Tables/1-D Lookup Table", _const(1), _const(1), lambda p: [True],
               lambda c: None, _lut_out, defaults={"extrapolation": "clip"},
               doc="Linear interpolation; extrapolation 'clip' or 'linear'."))
_reg(BlockType("UnitDelay", "simulink/Discrete/Unit Delay", _const(1), _const(1), lambda p: [False],
               lambda c: {"x": _num(c.params, "initial")}, _ud_out, _ud_update, defaults={"initial": 0.0}))
_reg(BlockType("DiscreteIntegrator", "simulink/Discrete/Discrete-Time Integrator", _const(1), _const(1),
               lambda p: [p["method"] != "ForwardEuler"], _di_init, _di_out, _di_update,
               defaults={"gain": 1.0, "initial": 0.0, "method": "ForwardEuler"},
               doc="method ForwardEuler (no feedthrough) or BackwardEuler; optional upper/lower state limits."))
_reg(BlockType("DiscreteTransferFcn", "simulink/Discrete/Discrete Transfer Fcn", _const(1), _const(1),
               lambda p: [len(p["num"]) == len(p["den"]) and float(p["num"][0]) != 0.0],
               _tf_init, _tf_out, _tf_update, defaults={"num": [1.0], "den": [1.0, -0.5]},
               doc="Coefficients in descending powers of z."))
_reg(BlockType("RateLimiter", "simulink/Discontinuities/Rate Limiter", _const(1), _const(1), lambda p: [True],
               _rl_init, _rl_out, _rl_update, defaults={"rising": 1.0, "falling": -1.0, "initial": 0.0},
               doc="rising/falling are slew rates per second."))
_reg(BlockType("Subsystem", "simulink/Ports & Subsystems/Subsystem", lambda p: 0, lambda p: 0, lambda p: [],
               lambda c: None, lambda c, s, u: [], doc="Virtual subsystem; flattened at compile time."))

STATEFUL = {"UnitDelay", "DiscreteIntegrator", "DiscreteTransferFcn", "RateLimiter"}
SOURCES = {"Inport", "Constant", "Ground"}


def block_type(name: str) -> BlockType:
    try:
        return LIB[name]
    except KeyError:
        raise ModelError(f"unknown block type {name!r}; supported: {', '.join(sorted(LIB))}") from None


def validate_params(type_name: str, p: dict) -> None:
    """Static parameter checks with messages a learner can act on."""
    if type_name == "Sum":
        if not p["signs"] or any(ch not in "+-" for ch in p["signs"]):
            raise ModelError("Sum signs must be a non-empty string of '+' and '-'")
    elif type_name == "Product":
        if not p["ops"] or any(ch not in "*/" for ch in p["ops"]):
            raise ModelError("Product ops must be a non-empty string of '*' and '/'")
    elif type_name == "Saturation":
        if _num(p, "lower") > _num(p, "upper"):
            raise ModelError("Saturation lower limit is greater than upper limit")
    elif type_name == "RelationalOperator":
        if p["op"] not in ("<", "<=", ">", ">=", "==", "~="):
            raise ModelError(f"unsupported relational operator {p['op']!r}")
    elif type_name == "LogicalOperator":
        if p["op"] not in ("AND", "OR", "NAND", "NOR", "XOR", "NOT"):
            raise ModelError(f"unsupported logical operator {p['op']!r}")
    elif type_name == "Switch":
        if p["criteria"] not in ("u2 >= Threshold", "u2 > Threshold", "u2 ~= 0"):
            raise ModelError(f"unsupported switch criteria {p['criteria']!r}")
    elif type_name == "Lookup1D":
        bp, tab = p.get("breakpoints"), p.get("table")
        if not bp or not tab or len(bp) != len(tab) or len(bp) < 2:
            raise ModelError("Lookup1D needs breakpoints and table of equal length (>= 2)")
        if any(b2 <= b1 for b1, b2 in zip(bp, bp[1:])):
            raise ModelError("Lookup1D breakpoints must be strictly increasing")
    elif type_name == "DiscreteIntegrator":
        if p["method"] not in ("ForwardEuler", "BackwardEuler"):
            raise ModelError("DiscreteIntegrator method must be ForwardEuler or BackwardEuler")
        if "upper" in p and "lower" in p and _num(p, "lower") > _num(p, "upper"):
            raise ModelError("DiscreteIntegrator lower limit is greater than upper limit")
    elif type_name == "RateLimiter":
        if _num(p, "rising") < 0 or _num(p, "falling") > 0:
            raise ModelError("RateLimiter rising must be >= 0 and falling <= 0")
    elif type_name == "MinMax":
        if p["fn"] not in ("min", "max") or int(p["inputs"]) < 1:
            raise ModelError("MinMax fn must be 'min' or 'max' with >= 1 input")


# ---- Stateflow-style chart -----------------------------------------------------------
def _chart_init(c: BlockCtx):
    from ecsim.model.chart import Chart
    chart = Chart(c.params["chart"])
    return {"chart": chart, "rt": chart.init_state()}


def _chart_out(c: BlockCtx, s, u):
    return s["chart"].step(s["rt"], u, c.ts)


def _chart_n(p: dict, which: int) -> int:
    from ecsim.model.chart import chart_ports
    return chart_ports(p)[which]


_reg(BlockType("Chart", "sflib/Chart", lambda p: _chart_n(p, 0), lambda p: _chart_n(p, 1),
               lambda p: [True] * _chart_n(p, 0), _chart_init, _chart_out,
               defaults={"chart": {"inputs": ["u"], "outputs": ["y"], "initial": "A",
                                   "states": [{"name": "A", "entry": "y = 0;"}, {"name": "B", "entry": "y = 1;"}],
                                   "transitions": [{"from": "A", "to": "B", "guard": "u > 0"},
                                                   {"from": "B", "to": "A", "guard": "u <= 0"}]}},
               doc="Stateflow-style state machine: hierarchy, entry/during/exit, guards, after(n, tick|sec)."))

_orig_validate = validate_params


def validate_params(type_name: str, p: dict) -> None:  # noqa: F811 - extends the checks above
    if type_name == "Chart":
        from ecsim.model.chart import Chart
        Chart(p["chart"])
        return
    _orig_validate(type_name, p)
