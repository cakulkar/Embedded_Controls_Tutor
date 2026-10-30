"""Modelling-guideline checks (static analysis of a model).

The rule set is ECSim's own. Each rule names the concern it shares with the industry guideline
families (MathWorks Advisory Board / JMAAB style guides, MISRA-oriented modelling practice), but
ECSim does not reproduce or claim conformance with those documents. Projects define their own
rule sets; the skill being trained is applying and justifying one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ecsim.model.blocks import ModelError
from ecsim.model.core import compile_model, load_model

_DEFAULT_NAME = re.compile(r"^(Gain|Sum|Product|Constant|Saturation|UnitDelay|DiscreteIntegrator|Switch|Lookup1D|"
                           r"RelationalOperator|LogicalOperator|Abs|MinMax|RateLimiter|Subsystem|Chart|Inport|Outport|"
                           r"DiscreteTransferFcn|Terminator|Ground)\d*$")
_SNAKE = re.compile(r"^[a-z][a-z0-9]*(_[a-zA-Z0-9]+)*$")
UNITS = ("rpm", "V", "A", "As", "Ah", "C", "degC", "K", "Nm", "N", "W", "kW", "Pa", "kPa", "bar", "s", "ms", "Hz", "pct",
         "m", "mm", "mps", "kph", "rad", "deg", "radps", "Wh", "kWh", "Ohm", "flag", "cnt", "mode", "state", "bool")

RULES = {
    "ECS-NAM-01": ("warning", "Blocks keep default names (Gain1, Sum2, …)",
                   "Name blocks after what they compute (e.g. speed_error, Kp): reviews and generated code read like the design."),
    "ECS-NAM-02": ("warning", "Root port names should be snake_case with a unit or type suffix",
                   "e.g. speed_meas_rpm, voltage_cmd_V, enable_cmd. Interfaces without units cause integration defects."),
    "ECS-SIG-01": ("warning", "Block outputs that are not used", "Delete dead logic or terminate it explicitly with a Terminator."),
    "ECS-RT-01": ("error", "Rate transition without a Rate Transition block",
                  "Signals crossing sample rates need an explicit, reviewed transition (deterministic data transfer)."),
    "ECS-DIV-01": ("warning", "Division by a signal",
                   "Guard divisors (Saturation/MinMax away from zero) or justify why the divisor cannot be zero."),
    "ECS-INT-01": ("warning", "Integrator without limits feeding further logic",
                   "Unlimited integrators wind up. Limit them to the physically meaningful range."),
    "ECS-REL-01": ("warning", "Equality comparison (==, ~=) of computed signals",
                   "Floating-point equality is fragile. Compare against a band, or only compare integer-valued mode signals."),
    "ECS-LUT-01": ("info", "Lookup table extrapolates linearly",
                   "Linear extrapolation can command values outside the characterised range. Prefer clipping for actuators."),
    "ECS-CPX-01": ("warning", "Too many blocks at one level (> 25)",
                   "Structure the model into subsystems that each have one responsibility."),
    "ECS-CHT-01": ("warning", "Chart output not assigned in a state's entry action",
                   "Every state should define every output on entry, so no stale value carries over from the previous state."),
    "ECS-CHT-02": ("error", "Unreachable chart state", "A state that is neither initial/default nor a transition target is dead logic."),
    "ECS-CHT-03": ("warning", "State without outgoing transitions (other than final/latched states)",
                   "Check that the state is meant to be terminal (e.g. a latched fault left only by reset)."),
}


@dataclass
class Finding:
    rule: str
    severity: str
    where: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity.upper()}] {self.rule} {self.where}: {self.message}"


def check_model(model: str | dict, max_blocks_per_level: int = 25) -> list[Finding]:
    m = load_model(model)
    out: list[Finding] = []

    def add(rule: str, where: str, detail: str = "") -> None:
        sev, title, _fix = RULES[rule]
        out.append(Finding(rule, sev, where, title + (f" ({detail})" if detail else "")))

    def walk(level: dict, path: str) -> None:
        blocks = level["blocks"]
        if len(blocks) > max_blocks_per_level:
            add("ECS-CPX-01", path or level["name"], f"{len(blocks)} blocks")
        for b in blocks:
            where = f"{path}/{b['id']}" if path else b["id"]
            if _DEFAULT_NAME.match(b["id"]) and b["type"] not in ("Terminator", "Ground"):
                add("ECS-NAM-01", where)
            p = b.get("params", {})
            if b["type"] == "Product" and "/" in p.get("ops", "**"):
                add("ECS-DIV-01", where)
            if b["type"] == "RelationalOperator" and p.get("op") in ("==", "~="):
                add("ECS-REL-01", where)
            if b["type"] == "Lookup1D" and p.get("extrapolation", "clip") == "linear":
                add("ECS-LUT-01", where)
            if b["type"] == "DiscreteIntegrator" and "upper" not in p and "lower" not in p:
                add("ECS-INT-01", where)
            if b["type"] == "Subsystem" and p.get("model"):
                walk(p["model"], where)
            if b["type"] == "Chart":
                _check_chart(p.get("chart") or {}, where, add)
            if not path and b["type"] in ("Inport", "Outport"):
                name = p.get("name", b["id"])
                suffix = name.rsplit("_", 1)[-1] if "_" in name else ""
                typed = suffix in UNITS or suffix in ("cmd", "req", "allowed", "enable", "status", "flag", "mode", "state")
                if not (_SNAKE.match(name) and typed):
                    add("ECS-NAM-02", where, name)

    walk(m, "")
    try:
        c = compile_model(m)
    except ModelError as exc:
        out.append(Finding("ECS-COMPILE", "error", m["name"], str(exc)))
        return out
    for w in c.warnings:
        if w.startswith("rate transition"):
            add("ECS-RT-01", w.split(" ")[2], w)
        elif w.startswith("output '"):
            blk = w.split("'")[1].rsplit(":", 1)[0]
            if c.blocks[blk].type not in ("Chart",):
                add("ECS-SIG-01", blk, w)
    return out


def _check_chart(spec: dict, where: str, add) -> None:
    from ecsim.model.chart import Chart, compile_actions
    try:
        ch = Chart(spec)
    except ModelError:
        return
    targets = {t["to"] for t in spec.get("transitions", [])}
    defaults = {ch.initial} | {d for d in ch.default_child.values() if d}
    sources = {t["from"] for t in spec.get("transitions", [])}
    writable = set(ch.outputs) | set(ch.locals)
    readable = set(ch.inputs) | writable
    for s in spec.get("states", []):
        name = s["name"]
        if name not in targets and name not in defaults:
            add("ECS-CHT-02", f"{where}.{name}")
        if ch.children[name]:
            continue
        assigned = {t for t, _o, _e in compile_actions(s.get("entry", ""), writable, readable, name)}
        missing = [o for o in ch.outputs if o not in assigned]
        if missing:
            add("ECS-CHT-01", f"{where}.{name}", ", ".join(missing))
        anc = ch.path(name)
        if not any(a in sources for a in anc):
            add("ECS-CHT-03", f"{where}.{name}")
