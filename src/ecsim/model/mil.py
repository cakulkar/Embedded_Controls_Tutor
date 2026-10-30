"""Model-in-the-loop (MIL) test harness, shared by both backends.

Two test kinds, as in industry MIL practice:
* **open_loop**: requirements-based test vectors drive the model's inports; checks are
  evaluated on the outport signals (what Simulink Test harnesses typically do at unit level).
* **closed_loop**: the model is a controller in the loop with a plant (here the DC motor).
  The plant is advanced with its *exact* zero-order-hold solution over each controller
  step, so the ECSim engine and the Simulink harness simulate the identical discrete system.

Signal definitions (piecewise in time, seconds):
    {"const": v} | {"step": {"t": 1.0, "before": 0, "after": 1}} |
    {"ramp": {"t0": 0, "slope": 1, "start": 0}} | {"points": [[t, v], ...]}  (linear interpolation) |
    {"pulse": {"t0": 0.5, "t1": 0.6, "low": 0, "high": 1}}  (high for t0 <= t < t1)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from ecsim.model.blocks import ModelError
from ecsim.plants.dc_motor import RPM_PER_RADS, DcMotor, DcMotorParams


def signal_value(spec: dict, t: float) -> float:
    if "const" in spec:
        return float(spec["const"])
    if "step" in spec:
        s = spec["step"]
        return float(s["after"] if t >= s["t"] - 1e-12 else s["before"])
    if "ramp" in spec:
        r = spec["ramp"]
        return float(r.get("start", 0.0)) + float(r["slope"]) * max(0.0, t - float(r.get("t0", 0.0)))
    if "pulse" in spec:
        q = spec["pulse"]
        on = float(q["t0"]) - 1e-12 <= t < float(q["t1"]) - 1e-12
        return float(q.get("high", 1.0) if on else q.get("low", 0.0))
    if "points" in spec:
        pts = spec["points"]
        if t <= pts[0][0]:
            return float(pts[0][1])
        for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
            if t <= t1:
                return float(v0) + (float(v1) - float(v0)) * (t - t0) / (t1 - t0)
        return float(pts[-1][1])
    raise ModelError(f"unknown signal definition {spec}")


@dataclass
class TestTrace:
    __test__ = False                          # not a pytest test class
    t: list[float]
    signals: dict[str, list[float]]          # inputs, outputs and (closed loop) plant signals


@dataclass
class CheckOutcome:
    test: str
    name: str
    passed: bool
    detail: str


def n_samples(duration: float, step: float) -> int:
    return int(round(duration / step)) + 1        # includes t = 0 and t = duration, like Simulink


# ---- closed-loop plant (exact discretisation) -----------------------------------
@dataclass
class DiscreteMotor:
    """ZOH-exact discrete model of DcMotor at step Ts: w[k+1] = a w[k] + bv V[k] - bt T[k]."""
    a: float
    bv: float
    bt: float

    @classmethod
    def at(cls, ts: float, p: DcMotorParams | None = None) -> "DiscreteMotor":
        p = p or DcMotorParams()
        alpha = -(p.kt * p.ke / p.r + p.b) / p.j
        e = math.exp(alpha * ts)
        g = (e - 1.0) / alpha
        return cls(a=e, bv=g * p.kt / (p.r * p.j), bt=g / p.j)

    def as_state_space(self) -> dict[str, Any]:
        """A, B (inputs [V, T_load]), C (rpm output), D for a Simulink Discrete State-Space block."""
        return {"A": self.a, "B": [self.bv, -self.bt], "C": RPM_PER_RADS, "D": [0.0, 0.0]}


V_MIN, V_MAX = 0.0, 24.0


# ---- checks ---------------------------------------------------------------------
def _window(tr: TestTrace, t0: float | None, t1: float | None) -> list[int]:
    lo = -math.inf if t0 is None else t0 - 1e-9
    hi = math.inf if t1 is None else t1 + 1e-9
    return [i for i, t in enumerate(tr.t) if lo <= t <= hi]


def _f(v: float) -> str:
    """Enough significant digits that small differences on large values stay visible."""
    return f"{v:.10g}"


def evaluate_check(test: str, chk: dict, tr: TestTrace) -> CheckOutcome:
    kind, sig = chk["kind"], chk["signal"]
    if sig not in tr.signals:
        return CheckOutcome(test, chk.get("name", kind), False, f"signal '{sig}' not available")
    y = tr.signals[sig]
    idx = _window(tr, chk.get("from"), chk.get("to"))
    name = chk.get("name") or f"{sig} {kind}"
    if not idx:
        return CheckOutcome(test, name, False, "empty time window")
    if kind == "within":                       # lo <= y <= hi for all samples in window
        lo, hi = chk.get("min", -math.inf), chk.get("max", math.inf)
        for i in idx:
            if not (lo - 1e-9 <= y[i] <= hi + 1e-9):
                return CheckOutcome(test, name, False, f"{sig} = {_f(y[i])} at t = {tr.t[i]:.3f} s "
                                                       f"(allowed {lo:g} … {hi:g})")
        vals = [y[i] for i in idx]
        return CheckOutcome(test, name, True, f"{sig} stayed in {_f(min(vals))} … {_f(max(vals))}")
    if kind == "near":                         # |y - target| <= tol for all samples in window
        target, tol = float(chk["target"]), float(chk["tol"])
        worst = max(idx, key=lambda i: abs(y[i] - target))
        err = abs(y[worst] - target)
        ok = err <= tol + 1e-9
        return CheckOutcome(test, name, ok, f"max |{sig} − {target:g}| = {err:.4g}"
                            + ("" if ok else f" at t = {tr.t[worst]:.3f} s") + f" (tol {tol:g})")
    if kind == "below_by":                     # y drops below `value` no later than `by`
        thr, by = float(chk["value"]), float(chk["by"])
        for i in idx:
            if y[i] < thr:
                ok = tr.t[i] <= by + 1e-9
                return CheckOutcome(test, name, ok, f"{sig} first below {thr:g} at t = {tr.t[i]:.3f} s (limit {by:g} s)")
        return CheckOutcome(test, name, False, f"{sig} never went below {thr:g}")
    if kind == "at":                           # value at one time instant
        t = float(chk["t"])
        i = min(range(len(tr.t)), key=lambda k: abs(tr.t[k] - t))
        lo, hi = chk.get("min", -math.inf), chk.get("max", math.inf)
        ok = lo - 1e-9 <= y[i] <= hi + 1e-9
        return CheckOutcome(test, name, ok, f"{sig}({tr.t[i]:.3f} s) = {_f(y[i])} (allowed {_f(lo)} … {_f(hi)})")
    raise ModelError(f"unknown check kind {kind!r}")


@dataclass
class MilReport:
    backend: str
    outcomes: list[CheckOutcome] = field(default_factory=list)
    traces: dict[str, TestTrace] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.errors and bool(self.outcomes) and all(o.passed for o in self.outcomes)

    def summary(self) -> str:
        n = len(self.outcomes)
        ok = sum(o.passed for o in self.outcomes)
        lines = [f"{ok}/{n} checks passed ({self.backend} backend)."]
        lines += [f"✗ {e}" for e in self.errors]
        cur = None
        for o in self.outcomes:
            if o.test != cur:
                cur = o.test
                lines.append(f"{'✓' if all(x.passed for x in self.outcomes if x.test == cur) else '✗'} {cur}")
            lines.append(f"    {'ok ' if o.passed else 'FAIL'} {o.name}: {o.detail}")
        return "\n".join(lines)


def run_tests(backend, submission: str, spec: dict) -> MilReport:
    """Check the interface, then run every test of an exercise spec on the given backend."""
    rep = MilReport(backend.name)
    try:
        handle = backend.prepare(submission, spec)
    except ModelError as exc:
        rep.errors.append(str(exc))
        return rep
    try:
        ins, outs = backend.interface(handle)
        exp_in, exp_out = spec["interface"]["inports"], spec["interface"]["outports"]
        if ins != exp_in or outs != exp_out:
            rep.errors.append(f"interface mismatch: model has inports {ins} and outports {outs}; "
                              f"required inports {exp_in} and outports {exp_out} (names and order)")
            return rep
        step = float(spec["interface"]["step"])
        mstep = backend.step_size(handle)
        if abs(mstep - step) > 1e-12:
            rep.errors.append(f"model sample time {mstep:g} s, required fixed step {step:g} s")
            return rep
        for test in spec["tests"]:
            try:
                if test["type"] == "open_loop":
                    tr = backend.run_open_loop(handle, test, step)
                elif test["type"] == "closed_loop":
                    tr = backend.run_closed_loop(handle, test, step)
                else:
                    raise ModelError(f"unknown test type {test['type']}")
            except ModelError as exc:
                rep.errors.append(f"{test['name']}: {exc}")
                continue
            rep.traces[test["name"]] = tr
            for chk in test["checks"]:
                rep.outcomes.append(evaluate_check(test["name"], chk, tr))
    finally:
        backend.release(handle)
    return rep
