"""Stateflow-style state machines as a model block ("Chart").

Chart definition (JSON, stored in the block's params.chart)::

    {"inputs": ["enable", "speed"], "outputs": ["mode"], "locals": {"n": 0},
     "initial": "Off",
     "states": [{"name": "Off", "entry": "mode = 0;"},
                {"name": "On", "initial": "Ramp"},          # composite: default child
                {"name": "Ramp", "parent": "On", "during": "n = n + 1;"}],
     "transitions": [{"from": "Off", "to": "On", "guard": "enable == 1", "action": "", "priority": 1},
                     {"from": "On", "to": "Off", "guard": "enable == 0 || after(2, sec)"}]}

Execution semantics (documented subset of Stateflow, one chart execution per sample hit):
1. First execution: take the default transition into `initial`, then default children down to
   a leaf, running **entry** actions outer → inner.
2. Later executions: check transitions leaving the active states **outermost first**; within a
   state, by ascending `priority` (then definition order). The first transition whose guard is
   true fires: **exit** actions inner → outer up to (not including) the common ancestor of the
   source and destination, then the transition **action**, then **entry** actions outer → inner
   down to the destination and its default children.
3. If nothing fires, **during** actions of active states run outer → inner.
4. At most one transition per execution (no super-step semantics). No junctions, no events,
   no parallel (AND) states. These are planned extensions and are rejected with a clear message.

Temporal logic: `after(n, tick)` is true once the source state has been active for ≥ n chart
executions; `after(t, sec)` once it has been active for ≥ t seconds (n·Ts). Counters reset on entry.

Expressions: numbers, true/false, inputs/outputs/locals, + - * /, comparisons
(== ~= != < <= > >=), logical && || ! ~ (also and/or/not), parentheses, after(...).
Actions: `x = expr;`, `x += expr;`, `x -= expr;` assigning outputs or locals.
"""
from __future__ import annotations

import ast
import copy
import operator
import re
from typing import Any

from ecsim.model.blocks import ModelError

_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
_CMP = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le,
        ast.Gt: operator.gt, ast.GtE: operator.ge}
_NAME = re.compile(r"^[A-Za-z_]\w*$")


def _translate(src: str) -> str:
    s = src.replace("~=", "!=").replace("&&", " and ").replace("||", " or ")
    s = re.sub(r"!(?!=)", " not ", s)
    s = re.sub(r"~", " not ", s)
    s = re.sub(r"\btrue\b", "1", s)
    s = re.sub(r"\bfalse\b", "0", s)
    return s


def compile_expr(src: str, names: set[str], where: str) -> ast.AST:
    try:
        tree = ast.parse(_translate(src).strip() or "1", mode="eval").body
    except SyntaxError:
        raise ModelError(f"{where}: cannot parse expression {src!r}") from None
    _check(tree, names, where, src)
    return tree


def _check(n: ast.AST, names: set[str], where: str, src: str) -> None:
    if isinstance(n, ast.Constant):
        if not isinstance(n.value, (int, float)) or isinstance(n.value, bool):
            raise ModelError(f"{where}: only numbers are allowed in {src!r}")
    elif isinstance(n, ast.Name):
        if n.id not in names:
            raise ModelError(f"{where}: unknown name '{n.id}' in {src!r} (declare it as input, output or local)")
    elif isinstance(n, ast.BinOp) and type(n.op) in _BIN:
        _check(n.left, names, where, src); _check(n.right, names, where, src)
    elif isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd, ast.Not)):
        _check(n.operand, names, where, src)
    elif isinstance(n, ast.BoolOp):
        for v in n.values:
            _check(v, names, where, src)
    elif isinstance(n, ast.Compare) and all(type(o) in _CMP for o in n.ops):
        _check(n.left, names, where, src)
        for c in n.comparators:
            _check(c, names, where, src)
    elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "after":
        if len(n.args) != 2 or not isinstance(n.args[1], ast.Name) or n.args[1].id not in ("tick", "sec"):
            raise ModelError(f"{where}: use after(n, tick) or after(t, sec) in {src!r}")
        _check(n.args[0], names, where, src)
    else:
        raise ModelError(f"{where}: unsupported syntax in {src!r}")


def eval_expr(n: ast.AST, env: dict[str, float], temporal: tuple[int, float] | None = None) -> float:
    if isinstance(n, ast.Constant):
        return float(n.value)
    if isinstance(n, ast.Name):
        return env[n.id]
    if isinstance(n, ast.BinOp):
        a, b = eval_expr(n.left, env, temporal), eval_expr(n.right, env, temporal)
        if isinstance(n.op, ast.Div) and b == 0:
            return float("inf") if a > 0 else (float("-inf") if a < 0 else 0.0)
        return _BIN[type(n.op)](a, b)
    if isinstance(n, ast.UnaryOp):
        v = eval_expr(n.operand, env, temporal)
        return -v if isinstance(n.op, ast.USub) else (v if isinstance(n.op, ast.UAdd) else float(v == 0))
    if isinstance(n, ast.BoolOp):
        vals = (eval_expr(v, env, temporal) != 0 for v in n.values)
        return float(all(vals) if isinstance(n.op, ast.And) else any(vals))
    if isinstance(n, ast.Compare):
        left = eval_expr(n.left, env, temporal)
        for op, c in zip(n.ops, n.comparators):
            right = eval_expr(c, env, temporal)
            if not _CMP[type(op)](left, right):
                return 0.0
            left = right
        return 1.0
    if isinstance(n, ast.Call):
        ticks, ts = temporal or (0, 0.0)
        k = eval_expr(n.args[0], env, temporal)
        return float(ticks >= k - 1e-9 if n.args[1].id == "tick" else ticks * ts >= k - 1e-9)
    raise ModelError("bad expression")


def compile_actions(src: str, writable: set[str], readable: set[str], where: str) -> list[tuple[str, str, ast.AST]]:
    out = []
    for stmt in re.split(r"[;\n]", src or ""):
        stmt = stmt.strip()
        if not stmt:
            continue
        m = re.match(r"^([A-Za-z_]\w*)\s*(\+=|-=|=)\s*(.+)$", stmt)
        if not m or m.group(3).lstrip().startswith("="):
            raise ModelError(f"{where}: action {stmt!r} must look like 'x = expr', 'x += expr' or 'x -= expr'")
        target, op, expr = m.groups()
        if target not in writable:
            raise ModelError(f"{where}: cannot assign '{target}' (only outputs and locals are writable)")
        out.append((target, op, compile_expr(expr, readable, where)))
    return out


def run_actions(actions, env: dict[str, float], temporal=None) -> None:
    for target, op, tree in actions:
        v = eval_expr(tree, env, temporal)
        env[target] = v if op == "=" else (env[target] + v if op == "+=" else env[target] - v)


class Chart:
    """Compiled chart. Instances are immutable; runtime state lives in a dict (see init_state)."""

    def __init__(self, spec: dict) -> None:
        if not isinstance(spec, dict):
            raise ModelError("chart definition must be an object")
        for key in ("parallel", "junctions", "events"):
            if spec.get(key):
                raise ModelError(f"chart feature '{key}' is not supported yet")
        self.inputs = list(spec.get("inputs", []))
        self.outputs = list(spec.get("outputs", []))
        self.locals = {k: float(v) for k, v in (spec.get("locals") or {}).items()}
        names = self.inputs + self.outputs + list(self.locals)
        for nm in names:
            if not _NAME.match(nm) or nm in ("after", "tick", "sec", "and", "or", "not", "true", "false"):
                raise ModelError(f"invalid data name {nm!r}")
        if len(set(names)) != len(names):
            raise ModelError("input, output and local names must be unique")
        if not self.outputs:
            raise ModelError("a chart needs at least one output")
        readable = set(names)
        writable = set(self.outputs) | set(self.locals)
        states = spec.get("states") or []
        if not states:
            raise ModelError("a chart needs at least one state")
        self.parent: dict[str, str | None] = {}
        self.default_child: dict[str, str | None] = {}
        self.actions: dict[str, dict[str, list]] = {}
        for s in states:
            nm = s.get("name", "")
            if not _NAME.match(nm):
                raise ModelError(f"invalid state name {nm!r}")
            if nm in self.parent:
                raise ModelError(f"duplicate state '{nm}'")
            self.parent[nm] = s.get("parent")
            self.default_child[nm] = s.get("initial")
            self.actions[nm] = {k: compile_actions(s.get(k, ""), writable, readable, f"state {nm} {k}")
                                for k in ("entry", "during", "exit")}
        for nm, par in self.parent.items():
            if par is not None and par not in self.parent:
                raise ModelError(f"state '{nm}': unknown parent '{par}'")
        self.children = {nm: [c for c, p in self.parent.items() if p == nm] for nm in self.parent}
        for nm, kids in self.children.items():
            dc = self.default_child[nm]
            if kids and dc not in kids:
                raise ModelError(f"composite state '{nm}' needs 'initial' set to one of its children {kids}")
            if not kids and dc:
                raise ModelError(f"state '{nm}' has 'initial' but no children")
        for nm in self.parent:                         # no cycles in hierarchy
            seen, cur = set(), nm
            while cur is not None:
                if cur in seen:
                    raise ModelError(f"state hierarchy has a cycle at '{nm}'")
                seen.add(cur)
                cur = self.parent[cur]
        self.initial = spec.get("initial")
        if self.initial not in self.parent or self.parent[self.initial] is not None:
            raise ModelError("chart 'initial' must name a top-level state")
        self.trans: dict[str, list[tuple[str, ast.AST, list]]] = {nm: [] for nm in self.parent}
        rows = []
        for i, t in enumerate(spec.get("transitions") or []):
            f, to = t.get("from"), t.get("to")
            if f not in self.parent or to not in self.parent:
                raise ModelError(f"transition {i + 1}: unknown state in {f!r} -> {to!r}")
            where = f"transition {f}->{to}"
            rows.append((f, int(t.get("priority", 1000 + i)), i, to,
                         compile_expr(t.get("guard", ""), readable, where),
                         compile_actions(t.get("action", ""), writable, readable, where)))
        for f, _p, _i, to, g, a in sorted(rows, key=lambda r: (r[1], r[2])):
            self.trans[f].append((to, g, a))
        self.spec = copy.deepcopy(spec)

    # ---- hierarchy helpers ----------------------------------------------------------
    def path(self, s: str) -> list[str]:
        out = []
        while s is not None:
            out.append(s)
            s = self.parent[s]
        return list(reversed(out))

    def _descend(self, s: str) -> list[str]:
        chain = [s]
        while self.default_child[chain[-1]]:
            chain.append(self.default_child[chain[-1]])
        return chain

    # ---- runtime ------------------------------------------------------------------------
    def init_state(self) -> dict:
        env = {n: 0.0 for n in self.inputs + self.outputs}
        env.update(self.locals)
        return {"env": env, "active": [], "ticks": {}, "started": False, "entered": [], "fired": None}

    def _enter(self, st: dict, chain: list[str], ts: float) -> None:
        for s in chain:
            st["active"].append(s)
            st["ticks"][s] = 0
            st.setdefault("entered", []).append(s)
            run_actions(self.actions[s]["entry"], st["env"], (0, ts))

    def step(self, st: dict, inputs: list[float], ts: float) -> list[float]:
        env = st["env"]
        st["entered"], st["fired"] = [], None          # per-step record (coverage)
        for n, v in zip(self.inputs, inputs):
            env[n] = float(v)
        if not st["started"]:
            st["started"] = True
            self._enter(st, self._descend(self.initial), ts)
        else:
            for s in st["active"]:
                st["ticks"][s] += 1
            fired = False
            for depth, src in enumerate(list(st["active"])):
                for to, guard, action in self.trans[src]:
                    if eval_expr(guard, env, (st["ticks"][src], ts)):
                        st["fired"] = (src, to, self.trans[src].index((to, guard, action)))
                        self._fire(st, depth, src, to, action, ts)
                        fired = True
                        break
                if fired:
                    break
            if not fired:
                for s in st["active"]:
                    run_actions(self.actions[s]["during"], env, (st["ticks"][s], ts))
        return [env[o] for o in self.outputs]

    def _fire(self, st: dict, depth: int, src: str, to: str, action, ts: float) -> None:
        src_path, dst_path = self.path(src), self.path(to)
        common = 0
        while common < min(len(src_path), len(dst_path)) and src_path[common] == dst_path[common]:
            common += 1
        if src == to:                                  # self-transition: exit and re-enter
            common = len(src_path) - 1
        active = st["active"]
        keep = min(common, depth + 1) if src != to else len(src_path) - 1
        while len(active) > keep:                      # exit inner -> outer
            s = active.pop()
            run_actions(self.actions[s]["exit"], st["env"], (st["ticks"][s], ts))
        run_actions(action, st["env"])
        self._enter(st, dst_path[len(active):] + self._descend(to)[1:], ts)


def chart_ports(params: dict) -> tuple[int, int]:
    spec = params.get("chart") or {}
    return len(spec.get("inputs", [])), len(spec.get("outputs", []))


# ---- SCXML interchange ---------------------------------------------------------------
def to_scxml(spec: dict) -> str:
    """Export to SCXML (W3C). Guards/actions are kept as text in ECSim's expression language."""
    from xml.sax.saxutils import quoteattr
    Chart(spec)
    kids: dict[str | None, list[dict]] = {}
    for s in spec["states"]:
        kids.setdefault(s.get("parent"), []).append(s)
    trans: dict[str, list[dict]] = {}
    for t in spec.get("transitions", []):
        trans.setdefault(t["from"], []).append(t)

    def state_xml(s: dict, ind: str) -> str:
        attrs = f' id={quoteattr(s["name"])}' + (f' initial={quoteattr(s["initial"])}' if s.get("initial") else "")
        body = []
        for kind, tag in (("entry", "onentry"), ("exit", "onexit")):
            if s.get(kind):
                body.append(f'{ind}  <{tag}><script>{_esc(s[kind])}</script></{tag}>')
        if s.get("during"):
            body.append(f'{ind}  <ecsim:during>{_esc(s["during"])}</ecsim:during>')
        for t in sorted(trans.get(s["name"], []), key=lambda t: t.get("priority", 1000)):
            cond = f' cond={quoteattr(t["guard"])}' if t.get("guard") else ""
            pr = f' ecsim:priority="{t["priority"]}"' if "priority" in t else ""
            act = f'<script>{_esc(t["action"])}</script>' if t.get("action") else ""
            body.append(f'{ind}  <transition target={quoteattr(t["to"])}{cond}{pr}>{act}</transition>')
        for c in kids.get(s["name"], []):
            body.append(state_xml(c, ind + "  "))
        return f"{ind}<state{attrs}>\n" + "\n".join(body) + f"\n{ind}</state>"

    data = "".join(f'\n    <data id={quoteattr(n)} expr="0" ecsim:role="input"/>' for n in spec.get("inputs", []))
    data += "".join(f'\n    <data id={quoteattr(n)} expr="0" ecsim:role="output"/>' for n in spec.get("outputs", []))
    data += "".join(f'\n    <data id={quoteattr(n)} expr="{v}" ecsim:role="local"/>'
                    for n, v in (spec.get("locals") or {}).items())
    states = "\n".join(state_xml(s, "  ") for s in kids.get(None, []))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<scxml xmlns="http://www.w3.org/2005/07/scxml" xmlns:ecsim="urn:ecsim:chart" version="1.0" '
            f'initial={quoteattr(spec["initial"])} datamodel="ecsim">\n  <datamodel>{data}\n  </datamodel>\n{states}\n</scxml>\n')


def _esc(s: str) -> str:
    from xml.sax.saxutils import escape
    return escape(s)


def from_scxml(text: str) -> dict:
    from xml.etree import ElementTree as ET
    ns = {"s": "http://www.w3.org/2005/07/scxml", "e": "urn:ecsim:chart"}
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ModelError(f"invalid SCXML: {exc}") from None
    if not root.tag.endswith("scxml"):
        raise ModelError("not an SCXML document")
    spec: dict[str, Any] = {"inputs": [], "outputs": [], "locals": {}, "states": [], "transitions": []}
    for d in root.iter("{http://www.w3.org/2005/07/scxml}data"):
        role = d.get("{urn:ecsim:chart}role", "local")
        if role == "input":
            spec["inputs"].append(d.get("id"))
        elif role == "output":
            spec["outputs"].append(d.get("id"))
        else:
            spec["locals"][d.get("id")] = float(d.get("expr", "0"))

    def walk(el, parent):
        for s in el.findall("s:state", ns):
            st = {"name": s.get("id")}
            if parent:
                st["parent"] = parent
            if s.get("initial"):
                st["initial"] = s.get("initial")
            for kind, tag in (("entry", "onentry"), ("exit", "onexit")):
                sc = s.find(f"s:{tag}/s:script", ns)
                if sc is not None and sc.text:
                    st[kind] = sc.text
            dur = s.find("e:during", ns)
            if dur is not None and dur.text:
                st["during"] = dur.text
            spec["states"].append(st)
            for t in s.findall("s:transition", ns):
                tr = {"from": st["name"], "to": t.get("target"), "guard": t.get("cond", "")}
                if t.get("{urn:ecsim:chart}priority"):
                    tr["priority"] = int(t.get("{urn:ecsim:chart}priority"))
                sc = t.find("s:script", ns)
                if sc is not None and sc.text:
                    tr["action"] = sc.text
                spec["transitions"].append(tr)
            walk(s, st["name"])
    walk(root, None)
    first_top = next((s["name"] for s in spec["states"] if "parent" not in s), None)
    spec["initial"] = root.get("initial") or first_top
    Chart(spec)
    return spec
