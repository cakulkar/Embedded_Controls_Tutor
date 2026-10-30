"""Model coverage (MIL): which decision outcomes of the model did the tests exercise?

Outcomes per block (decision-style model coverage, in the spirit of Simulink Coverage's
decision coverage for these block types):

| Block | Outcomes |
|---|---|
| Saturation | below lower limit, within, above upper limit |
| Switch | input 1 passed, input 3 passed |
| RelationalOperator, LogicalOperator | true, false |
| MinMax | each input selected |
| Lookup1D | below range, in range, above range |
| DiscreteIntegrator with limits | at lower, within, at upper |
| RateLimiter | rising-limited, not limited, falling-limited |
| Abs | input negative, input non-negative |
| Chart | each state entered, each transition taken |

This is decision coverage of model constructs. It is not condition or MC/DC coverage, and it is
not code coverage. The lesson B05-L2 explains the difference.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ecsim.model.core import Compiled, Simulator


def outcomes_for(c: Compiled) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for bid, b in c.blocks.items():
        t, p = b.type, b.params
        if t == "Saturation":
            out[bid] = ["below lower limit", "within limits", "above upper limit"]
        elif t == "Switch":
            out[bid] = ["passes input 1", "passes input 3"]
        elif t in ("RelationalOperator", "LogicalOperator"):
            out[bid] = ["true", "false"]
        elif t == "MinMax" and int(p["inputs"]) > 1:
            out[bid] = [f"selects input {k + 1}" for k in range(int(p["inputs"]))]
        elif t == "Lookup1D":
            out[bid] = ["below first breakpoint", "within breakpoints", "above last breakpoint"]
        elif t == "DiscreteIntegrator" and ("upper" in p or "lower" in p):
            out[bid] = ["at lower limit", "within limits", "at upper limit"]
        elif t == "RateLimiter":
            out[bid] = ["rising rate limited", "not limited", "falling rate limited"]
        elif t == "Abs":
            out[bid] = ["input negative", "input non-negative"]
        elif t == "Chart":
            from ecsim.model.chart import Chart
            ch = Chart(p["chart"])
            o = [f"state {s} entered" for s in ch.parent]
            for src, rows in ch.trans.items():
                for k, (to, _g, _a) in enumerate(rows):
                    o.append(f"transition {src} → {to}" + (f" #{k + 1}" if sum(1 for r in rows if r[0] == to) > 1 else ""))
            out[bid] = o
    return out


@dataclass
class CoverageReport:
    outcomes: dict[str, list[str]]
    hit: dict[str, set[str]] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(len(v) for v in self.outcomes.values())

    @property
    def covered(self) -> int:
        return sum(len(self.hit.get(b, set()) & set(o)) for b, o in self.outcomes.items())

    @property
    def ratio(self) -> float:
        return self.covered / self.total if self.total else 1.0

    def missing(self) -> list[tuple[str, str]]:
        return [(b, o) for b, outs in self.outcomes.items() for o in outs if o not in self.hit.get(b, set())]

    def merge(self, other: "CoverageReport") -> None:
        for b, s in other.hit.items():
            self.hit.setdefault(b, set()).update(s)

    def summary(self) -> str:
        lines = [f"Decision coverage: {self.covered}/{self.total} outcomes ({self.ratio:.0%})"]
        for b, o in self.missing():
            lines.append(f"    not covered: {b}: {o}")
        return "\n".join(lines)


class CoverageCollector:
    def __init__(self, c: Compiled) -> None:
        self.c = c
        self.report = CoverageReport(outcomes_for(c))

    def attach(self, sim: Simulator) -> Simulator:
        sim.observer = self._observe
        return sim

    def _mark(self, bid: str, outcome: str) -> None:
        self.report.hit.setdefault(bid, set()).add(outcome)

    def _observe(self, bid: str, b, u, y, state) -> None:
        if bid not in self.report.outcomes:
            return
        t, p = b.type, b.params
        if t == "Saturation":
            v = u[0]
            self._mark(bid, "below lower limit" if v < float(p["lower"]) else
                       ("above upper limit" if v > float(p["upper"]) else "within limits"))
        elif t == "Switch":
            self._mark(bid, "passes input 1" if self._crit(p, u[1]) else "passes input 3")
        elif t in ("RelationalOperator", "LogicalOperator"):
            self._mark(bid, "true" if y[0] != 0 else "false")
        elif t == "MinMax":
            self._mark(bid, f"selects input {u.index(y[0]) + 1}")
        elif t == "Lookup1D":
            bp = p["breakpoints"]
            self._mark(bid, "below first breakpoint" if u[0] < bp[0] else
                       ("above last breakpoint" if u[0] > bp[-1] else "within breakpoints"))
        elif t == "DiscreteIntegrator":
            x = state["x"]
            hi, lo = p.get("upper"), p.get("lower")
            self._mark(bid, "at upper limit" if hi is not None and x >= float(hi) else
                       ("at lower limit" if lo is not None and x <= float(lo) else "within limits"))
        elif t == "RateLimiter":
            d = u[0] - y[0]
            self._mark(bid, "rising rate limited" if d > 1e-12 else ("falling rate limited" if d < -1e-12 else "not limited"))
        elif t == "Abs":
            self._mark(bid, "input negative" if u[0] < 0 else "input non-negative")
        elif t == "Chart":
            ch, rt = state["chart"], state["rt"]
            for s in rt.get("entered", []):
                self._mark(bid, f"state {s} entered")
            if rt.get("fired"):
                src, to, k = rt["fired"]
                rows = ch.trans[src]
                label = f"transition {src} → {to}" + (f" #{k + 1}" if sum(1 for r in rows if r[0] == to) > 1 else "")
                self._mark(bid, label)

    @staticmethod
    def _crit(p: dict, u2: float) -> bool:
        th = float(p["threshold"])
        c = p["criteria"]
        return u2 >= th if c == "u2 >= Threshold" else (u2 > th if c == "u2 > Threshold" else u2 != 0)
