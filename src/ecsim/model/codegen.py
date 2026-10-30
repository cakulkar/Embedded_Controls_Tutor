"""C code generation from ECSim models (Embedded Coder-style structure).

Generated interface (model name `m`):

    typedef struct { real_T in1; ... } ExtU_m;     extern ExtU_m m_U;    /* root inports  */
    typedef struct { real_T out1; ... } ExtY_m;    extern ExtY_m m_Y;    /* root outports */
    typedef struct { ... } DW_m;                   extern DW_m  m_DW;    /* states, held outputs */
    void m_initialize(void);
    void m_step(void);                             /* one base step: outputs, then state updates */

Properties: C99, no dynamic memory, no recursion, fixed-size data, `double` or `single`
(`real_T` typedef), compiles warning-free with -Wall -Wextra -Werror -pedantic. Every block
of the ECSim library is supported, including Stateflow-style charts. Fixed-point data types
are not generated (taught conceptually; see docs/PROGRESS.md).

The generated code follows exactly the execution order and semantics of ecsim.model.core, so
MIL vs SIL differences come only from arithmetic precision. That is what back-to-back tests expose.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from ecsim.model.blocks import ModelError
from ecsim.model.chart import Chart
from ecsim.model.core import Compiled, compile_model


@dataclass
class GeneratedCode:
    name: str
    header: str
    source: str
    dtype: str
    inports: list[str]
    outports: list[str]

    @property
    def files(self) -> dict[str, str]:
        return {f"{self.name}.h": self.header, f"{self.name}.c": self.source}


def c_ident(s: str) -> str:
    out = re.sub(r"\W", "_", s)
    return out if re.match(r"[A-Za-z_]", out) else "m_" + out


class _Gen:
    def __init__(self, c: Compiled, dtype: str) -> None:
        if dtype not in ("double", "single"):
            raise ModelError("dtype must be 'double' or 'single'")
        self.c, self.dtype = c, dtype
        self.f = "f" if dtype == "single" else ""
        self.m = c_ident(c.name)
        self.dw: list[str] = []          # DW struct members
        self.init: list[str] = []        # initialize body
        self.out: list[str] = []         # step: outputs section
        self.upd: list[str] = []         # step: update section
        self.helpers: list[str] = []
        self.consts: list[str] = []
        self.need_lut = False
        self.need_div = False
        self.need_math = False

    # ---- helpers ------------------------------------------------------------------
    def lit(self, v: float) -> str:
        if v == float("inf"):
            return "ECS_INF"
        if v == float("-inf"):
            return "(-ECS_INF)"
        s = repr(float(v))
        if "e" not in s and "." not in s:
            s += ".0"
        return f"{s}{self.f}" if self.dtype == "single" else s

    def y(self, bid: str, k: int = 0) -> str:
        return f"{self.m}_DW.y_{c_ident(bid)}_{k + 1}"

    def u(self, b, i: int) -> str:
        src = b.inputs[i]
        return self.y(src[0], src[1])

    def guard_rate(self, b, lines: list[str]) -> list[str]:
        if b.every == 1 or not lines:
            return lines
        return [f"if (({self.m}_DW.tick % {b.every}u) == 0u) {{"] + ["    " + ln for ln in lines] + ["}"]

    # ---- per-block ----------------------------------------------------------------
    def block(self, bid: str) -> None:
        c, m = self.c, self.m
        b = c.blocks[bid]
        p, t = b.params, b.type
        for k in range(b.n_out):
            self.dw.append(f"real_T y_{c_ident(bid)}_{k + 1};")
        Y = self.y(bid)
        U = [self.u(b, i) for i in range(b.n_in)]
        o: list[str] = []
        up: list[str] = []
        sid = c_ident(bid)
        if t == "Inport":
            o.append(f"{Y} = {m}_U.{c_ident(p.get('name', bid))};")
        elif t == "Outport":
            o.append(f"{m}_Y.{c_ident(p.get('name', bid))} = {U[0]};")
        elif t == "Constant":
            self.init.append(f"{Y} = {self.lit(float(p['value']))};")
        elif t == "Ground":
            pass
        elif t == "Terminator":
            pass
        elif t == "Gain":
            o.append(f"{Y} = {self.lit(float(p['gain']))} * {U[0]};")
        elif t == "Sum":
            terms = [("" if i == 0 and s == "+" else (" + " if s == "+" else (" - " if i else "-"))) + U[i]
                     for i, s in enumerate(p["signs"])]
            o.append(f"{Y} = {''.join(terms)};")
        elif t == "Product":
            self.need_div = "/" in p["ops"]
            expr = U[0] if p["ops"][0] == "*" else f"ecs_div({self.lit(1.0)}, {U[0]})"
            for op, v in zip(p["ops"][1:], U[1:]):
                expr = f"({expr} * {v})" if op == "*" else f"ecs_div({expr}, {v})"
            o.append(f"{Y} = {expr};")
        elif t == "Abs":
            o.append(f"{Y} = ({U[0]} < {self.lit(0.0)}) ? -{U[0]} : {U[0]};")
        elif t == "MinMax":
            expr = U[0]
            cmp = "<" if p["fn"] == "min" else ">"
            for v in U[1:]:
                o.append(f"{Y} = ({v} {cmp} {expr}) ? {v} : {expr};")
                expr = Y
            if len(U) == 1:
                o.append(f"{Y} = {U[0]};")
        elif t == "Saturation":
            hi, lo = self.lit(float(p["upper"])), self.lit(float(p["lower"]))
            o.append(f"{Y} = ({U[0]} > {hi}) ? {hi} : (({U[0]} < {lo}) ? {lo} : {U[0]});")
        elif t == "RelationalOperator":
            op = "!=" if p["op"] == "~=" else p["op"]
            o.append(f"{Y} = ({U[0]} {op} {U[1]}) ? {self.lit(1.0)} : {self.lit(0.0)};")
        elif t == "LogicalOperator":
            bs = [f"({v} != {self.lit(0.0)})" for v in U]
            expr = {"AND": " && ".join(bs), "OR": " || ".join(bs), "NAND": "!(" + " && ".join(bs) + ")",
                    "NOR": "!(" + " || ".join(bs) + ")", "NOT": f"!{bs[0]}",
                    "XOR": "(" + " + ".join(f"({x} ? 1 : 0)" for x in bs) + ") % 2 == 1"}[p["op"]]
            o.append(f"{Y} = ({expr}) ? {self.lit(1.0)} : {self.lit(0.0)};")
        elif t == "Switch":
            th = self.lit(float(p["threshold"]))
            cond = {"u2 >= Threshold": f"{U[1]} >= {th}", "u2 > Threshold": f"{U[1]} > {th}",
                    "u2 ~= 0": f"{U[1]} != {self.lit(0.0)}"}[p["criteria"]]
            o.append(f"{Y} = ({cond}) ? {U[0]} : {U[2]};")
        elif t == "Lookup1D":
            self.need_lut = True
            bp, tab = p["breakpoints"], p["table"]
            self.consts.append(f"static const real_T {sid}_bp[{len(bp)}] = {{ {', '.join(self.lit(float(v)) for v in bp)} }};")
            self.consts.append(f"static const real_T {sid}_tab[{len(tab)}] = {{ {', '.join(self.lit(float(v)) for v in tab)} }};")
            clip = 1 if p.get("extrapolation", "clip") == "clip" else 0
            o.append(f"{Y} = ecs_lookup1d({sid}_bp, {sid}_tab, {len(bp)}u, {U[0]}, {clip});")
        elif t == "UnitDelay":
            self.dw.append(f"real_T x_{sid};")
            self.init.append(f"{m}_DW.x_{sid} = {self.lit(float(p['initial']))};")
            o.append(f"{Y} = {m}_DW.x_{sid};")
            up.append(f"{m}_DW.x_{sid} = {U[0]};")
        elif t == "DiscreteIntegrator":
            self.dw.append(f"real_T x_{sid};")
            self.init.append(f"{m}_DW.x_{sid} = {self.lit(float(p['initial']))};")
            kt = self.lit(float(p["gain"]) * b.ts)
            nxt = f"{m}_DW.x_{sid} + {kt} * {U[0]}"
            clamp = []
            if "upper" in p:
                clamp.append(f"if (v_{sid} > {self.lit(float(p['upper']))}) {{ v_{sid} = {self.lit(float(p['upper']))}; }}")
            if "lower" in p:
                clamp.append(f"if (v_{sid} < {self.lit(float(p['lower']))}) {{ v_{sid} = {self.lit(float(p['lower']))}; }}")
            body = ["{", f"    real_T v_{sid} = {nxt};"] + ["    " + x for x in clamp]
            if p["method"] == "ForwardEuler":
                o.append(f"{Y} = {m}_DW.x_{sid};")
                up += body + [f"    {m}_DW.x_{sid} = v_{sid};", "}"]
            else:
                o += body + [f"    {Y} = v_{sid};", "}"]
                up += body + [f"    {m}_DW.x_{sid} = v_{sid};", "}"]
        elif t == "DiscreteTransferFcn":
            num, den = [float(v) for v in p["num"]], [float(v) for v in p["den"]]
            num = [0.0] * (len(den) - len(num)) + num
            a0 = den[0]
            bb, aa = [v / a0 for v in num], [v / a0 for v in den]
            n = len(den) - 1
            if n:
                self.dw.append(f"real_T z_{sid}[{n}];")
                self.init.append(f"{{ uint32_t i; for (i = 0u; i < {n}u; i++) {{ {m}_DW.z_{sid}[i] = {self.lit(0.0)}; }} }}")
            z0 = f" + {m}_DW.z_{sid}[0]" if n else ""
            o.append(f"{Y} = {self.lit(bb[0])} * {U[0]}{z0};")
            for i in range(n):
                nxt = f"{m}_DW.z_{sid}[{i + 1}]" if i + 1 < n else self.lit(0.0)
                up.append(f"{m}_DW.z_{sid}[{i}] = {nxt} + {self.lit(bb[i + 1])} * {U[0]} - {self.lit(aa[i + 1])} * {Y};")
        elif t == "RateLimiter":
            self.dw.append(f"real_T prev_{sid};")
            self.init.append(f"{m}_DW.prev_{sid} = {self.lit(float(p['initial']))};")
            r, f_ = self.lit(float(p["rising"]) * b.ts), self.lit(float(p["falling"]) * b.ts)
            o += ["{", f"    real_T d_{sid} = {U[0]} - {m}_DW.prev_{sid};",
                  f"    if (d_{sid} > {r}) {{ d_{sid} = {r}; }}", f"    if (d_{sid} < {f_}) {{ d_{sid} = {f_}; }}",
                  f"    {Y} = {m}_DW.prev_{sid} + d_{sid};", "}"]
            up.append(f"{m}_DW.prev_{sid} = {Y};")
        elif t == "Chart":
            self.chart(bid, b, U)
            return
        else:
            raise ModelError(f"code generation does not support block type {t}")
        self.out += self.guard_rate(b, o)
        self.upd += self.guard_rate(b, up)

    # ---- chart ------------------------------------------------------------------------
    def chart(self, bid: str, b, U: list[str]) -> None:
        ch = Chart(b.params["chart"])
        m, sid = self.m, c_ident(bid)
        names = list(ch.parent)
        idx = {s: i for i, s in enumerate(names)}
        N = len(names)
        pfx = f"{sid}_"
        var = {}
        for k, n in enumerate(ch.inputs):
            var[n] = f"{pfx}in_{c_ident(n)}"
        for n in ch.outputs + list(ch.locals):
            var[n] = f"{m}_DW.{pfx}d_{c_ident(n)}"
            self.dw.append(f"real_T {pfx}d_{c_ident(n)};")
        self.dw += [f"uint8_T {pfx}active[{N}];", f"uint8_T {pfx}n;", f"uint32_T {pfx}ticks[{N}];", f"uint8_T {pfx}started;"]
        for n in ch.outputs:
            self.init.append(f"{var[n]} = {self.lit(0.0)};")
        for n, v in ch.locals.items():
            self.init.append(f"{var[n]} = {self.lit(v)};")
        self.init += [f"{m}_DW.{pfx}n = 0u;", f"{m}_DW.{pfx}started = 0u;"]
        ts = self.lit(b.ts)

        def expr(node, src_state: str | None) -> str:
            if isinstance(node, ast.Constant):
                return self.lit(float(node.value))
            if isinstance(node, ast.Name):
                return var[node.id]
            if isinstance(node, ast.BinOp):
                op = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/"}[type(node.op)]
                return f"({expr(node.left, src_state)} {op} {expr(node.right, src_state)})"
            if isinstance(node, ast.UnaryOp):
                v = expr(node.operand, src_state)
                return f"(-{v})" if isinstance(node.op, ast.USub) else (v if isinstance(node.op, ast.UAdd)
                                                                       else f"(({v}) == {self.lit(0.0)} ? {self.lit(1.0)} : {self.lit(0.0)})")
            if isinstance(node, ast.BoolOp):
                j = " && " if isinstance(node.op, ast.And) else " || "
                return "((" + j.join(f"({expr(v, src_state)} != {self.lit(0.0)})" for v in node.values) + f") ? {self.lit(1.0)} : {self.lit(0.0)})"
            if isinstance(node, ast.Compare):
                ops = {ast.Eq: "==", ast.NotEq: "!=", ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">="}
                parts, left = [], expr(node.left, src_state)
                for op, cmp in zip(node.ops, node.comparators):
                    right = expr(cmp, src_state)
                    parts.append(f"({left} {ops[type(op)]} {right})")
                    left = right
                return "((" + " && ".join(parts) + f") ? {self.lit(1.0)} : {self.lit(0.0)})"
            if isinstance(node, ast.Call):
                k = expr(node.args[0], src_state)
                ticks = f"(real_T){m}_DW.{pfx}ticks[{idx[src_state]}]"
                if node.args[1].id == "tick":
                    return f"(({ticks} >= ({k} - {self.lit(1e-9)})) ? {self.lit(1.0)} : {self.lit(0.0)})"
                return f"((({ticks} * {ts}) >= ({k} - {self.lit(1e-9)})) ? {self.lit(1.0)} : {self.lit(0.0)})"
            raise ModelError("unsupported chart expression in code generation")

        def actions(acts, state: str | None) -> list[str]:
            out = []
            for target, op, tree in acts:
                rhs = expr(tree, state)
                out.append(f"{var[target]} = {rhs};" if op == "=" else
                           f"{var[target]} = {var[target]} {'+' if op == '+=' else '-'} {rhs};")
            return out

        parent = ", ".join(str(idx[ch.parent[s]]) if ch.parent[s] else "255" for s in names)
        dchild = ", ".join(str(idx[ch.default_child[s]]) if ch.default_child[s] else "255" for s in names)
        self.consts.append(f"static const uint8_T {pfx}parent[{N}] = {{ {parent} }};")
        self.consts.append(f"static const uint8_T {pfx}dchild[{N}] = {{ {dchild} }};")

        def sw(kind: str) -> list[str]:
            lines = [f"static void {pfx}{kind}(uint8_T s)", "{", "    switch (s) {"]
            for s in names:
                body = actions(ch.actions[s][kind], s)
                if body:
                    lines += [f"    case {idx[s]}u:"] + ["        " + x for x in body] + ["        break;"]
            lines += ["    default:", "        break;", "    }", "}"]
            return lines

        h = self.helpers
        h += sw("entry") + sw("during") + sw("exit")
        h += [f"static uint8_T {pfx}path_len(uint8_T s)", "{", "    uint8_T n = 0u;",
              f"    while (s != 255u) {{ n++; s = {pfx}parent[s]; }}", "    return n;", "}"]
        h += [f"static uint8_T {pfx}ancestor(uint8_T s, uint8_T level)", "{",
              f"    uint8_T n = {pfx}path_len(s);", "    while (n > (uint8_T)(level + 1u)) {", f"        s = {pfx}parent[s];", "        n--;", "    }",
              "    return s;", "}"]
        h += [f"static void {pfx}enter_chain(uint8_T s)", "{",
              "    /* enter the destination path below the states that stay active (outer -> inner),",
              "       then its default children */",
              f"    uint8_T depth = {pfx}path_len(s);", "    uint8_T cur;",
              f"    while ({m}_DW.{pfx}n < depth) {{",
              f"        cur = {pfx}ancestor(s, {m}_DW.{pfx}n);",
              f"        {m}_DW.{pfx}active[{m}_DW.{pfx}n] = cur;", f"        {m}_DW.{pfx}n++;",
              f"        {m}_DW.{pfx}ticks[cur] = 0u;", f"        {pfx}entry(cur);", "    }",
              f"    cur = {pfx}dchild[s];", "    while (cur != 255u) {",
              f"        {m}_DW.{pfx}active[{m}_DW.{pfx}n] = cur;", f"        {m}_DW.{pfx}n++;",
              f"        {m}_DW.{pfx}ticks[cur] = 0u;", f"        {pfx}entry(cur);", f"        cur = {pfx}dchild[cur];", "    }", "}"]
        # transitions
        tlines = [f"static uint8_T {pfx}try(uint8_T depth, uint8_T src)", "{", "    switch (src) {"]
        for s in names:
            rows = ch.trans[s]
            if not rows:
                continue
            tlines.append(f"    case {idx[s]}u:")
            for to, guard, act in rows:
                g = expr(guard, s)
                tlines += [f"        if ({g} != {self.lit(0.0)}) {{",
                           f"            {pfx}fire(depth, src, {idx[to]}u);"]
                tlines += ["            " + x for x in actions(act, s)]
                tlines += [f"            {pfx}enter_chain({idx[to]}u);", "            return 1u;", "        }"]
            tlines.append("        break;")
        tlines += ["    default:", "        break;", "    }", "    return 0u;", "}"]
        fire = [f"static void {pfx}fire(uint8_T depth, uint8_T src, uint8_T dst)", "{",
                f"    uint8_T ls = {pfx}path_len(src);", f"    uint8_T ld = {pfx}path_len(dst);",
                "    uint8_T common = 0u;", "    uint8_T keep;",
                f"    while ((common < ls) && (common < ld) && ({pfx}ancestor(src, common) == {pfx}ancestor(dst, common))) {{",
                "        common++;", "    }",
                "    if (src == dst) {", "        keep = (uint8_T)(ls - 1u);", "    } else {",
                "        keep = (common < (uint8_T)(depth + 1u)) ? common : (uint8_T)(depth + 1u);", "    }",
                f"    while ({m}_DW.{pfx}n > keep) {{", f"        {m}_DW.{pfx}n--;",
                f"        {pfx}exit({m}_DW.{pfx}active[{m}_DW.{pfx}n]);", "    }", "}"]
        h += fire + tlines
        init_idx = idx[ch.initial]
        o = [f"{{"]
        for n in ch.inputs:
            o.append(f"    real_T {var[n]} = {U[ch.inputs.index(n)]};")
        o += [f"    if ({m}_DW.{pfx}started == 0u) {{", f"        {m}_DW.{pfx}started = 1u;",
              f"        {pfx}enter_chain({init_idx}u);", "    } else {", "        uint8_T d;", "        uint8_T fired = 0u;",
              f"        for (d = 0u; d < {m}_DW.{pfx}n; d++) {{", f"            {m}_DW.{pfx}ticks[{m}_DW.{pfx}active[d]]++;", "        }",
              f"        for (d = 0u; (d < {m}_DW.{pfx}n) && (fired == 0u); d++) {{",
              f"            fired = {pfx}try(d, {m}_DW.{pfx}active[d]);", "        }",
              "        if (fired == 0u) {", f"            for (d = 0u; d < {m}_DW.{pfx}n; d++) {{",
              f"                {pfx}during({m}_DW.{pfx}active[d]);", "            }", "        }", "    }"]
        for k, n in enumerate(ch.outputs):
            o.append(f"    {self.y(bid, k)} = {var[n]};")
        for n in ch.inputs:
            o.append(f"    (void){var[n]};")
        o.append("}")
        # chart input variables are referenced inside helper functions too -> make them file-scope
        for n in ch.inputs:
            self.consts.append(f"static real_T {var[n]};")
        o = [self._strip_decl(x, ch, var) for x in o]
        self.out += self.guard_rate(b, o)

    @staticmethod
    def _strip_decl(line: str, ch: Chart, var: dict) -> str:
        for n in ch.inputs:
            line = line.replace(f"real_T {var[n]} = ", f"{var[n]} = ")
        return line

    # ---- assemble -----------------------------------------------------------------------
    def generate(self) -> GeneratedCode:
        c, m = self.c, self.m
        for bid in c.order:
            self.block(bid)
        multirate = any(b.every > 1 for b in c.blocks.values())
        if multirate:
            self.dw.append("uint32_T tick;")
            self.init.append(f"{m}_DW.tick = 0u;")
            self.upd.append(f"{m}_DW.tick++;")
        ins = [c_ident(n) for n in c.inport_names]
        outs = [c_ident(n) for n in c.outport_names]
        real = "float" if self.dtype == "single" else "double"
        hdr = [f"/* {m}.h - generated by ECSim from model '{c.name}' ({self.dtype} precision). Do not edit. */",
               f"#ifndef {m.upper()}_H", f"#define {m.upper()}_H", "", "#include <stdint.h>", "",
               f"typedef {real} real_T;", "typedef uint8_t uint8_T;", "typedef uint32_t uint32_T;", "",
               "typedef struct {"] + [f"    real_T {n};" for n in ins] + (["    uint8_T unused_;"] if not ins else []) + \
              [f"}} ExtU_{m};", "", "typedef struct {"] + [f"    real_T {n};" for n in outs] + \
              (["    uint8_T unused_;"] if not outs else []) + [f"}} ExtY_{m};", "", "typedef struct {"] + \
              [f"    {x}" for x in self.dw] + (["    uint8_T unused_;"] if not self.dw else []) + [f"}} DW_{m};", "",
              f"extern ExtU_{m} {m}_U;", f"extern ExtY_{m} {m}_Y;", f"extern DW_{m} {m}_DW;", "",
              f"void {m}_initialize(void);", f"void {m}_step(void);    /* call every {c.step:g} s */", "", f"#endif /* {m.upper()}_H */", ""]
        src = [f"/* {m}.c - generated by ECSim from model '{c.name}' ({self.dtype} precision). Do not edit. */",
               f'#include "{m}.h"', "", "#define ECS_INF (1.0e300 * 1.0e300)" if self.dtype == "double" else
               "#define ECS_INF ((real_T)(1.0e300 * 1.0e300))", "",
               f"ExtU_{m} {m}_U;", f"ExtY_{m} {m}_Y;", f"DW_{m} {m}_DW;", ""]
        src += self.consts + ([""] if self.consts else [])
        if self.need_div:
            z = self.lit(0.0)
            src += ["static real_T ecs_div(real_T a, real_T b)", "{",
                    f"    /* division by zero gives +/-inf with the sign of the numerator (+inf for 0/0) */",
                    f"    real_T r;", f"    if (b == {z}) {{", f"        r = (a < {z}) ? -ECS_INF : ECS_INF;", "    } else {",
                    "        r = a / b;", "    }", "    return r;", "}", ""]
        if self.need_lut:
            src += ["static real_T ecs_lookup1d(const real_T *bp, const real_T *tab, uint32_T n, real_T x, int clip)", "{",
                    "    uint32_T i = 0u;", "    real_T f;", "    if ((x <= bp[0]) || (x >= bp[n - 1u])) {",
                    "        if (clip != 0) {", "            return (x <= bp[0]) ? tab[0] : tab[n - 1u];", "        }",
                    "        i = (x <= bp[0]) ? 0u : (n - 2u);", "    } else {", "        uint32_T lo = 0u;", "        uint32_T hi = n - 1u;",
                    "        while ((hi - lo) > 1u) {", "            uint32_T mid = (lo + hi) / 2u;",
                    "            if (bp[mid] <= x) { lo = mid; } else { hi = mid; }", "        }", "        i = lo;", "    }",
                    "    f = (x - bp[i]) / (bp[i + 1u] - bp[i]);", "    return tab[i] + (f * (tab[i + 1u] - tab[i]));", "}", ""]
        src += [x for x in self.helpers] + ([""] if self.helpers else [])
        src += [f"void {m}_initialize(void)", "{"] + [f"    {x}" for x in self.init] + ["}", "",
                f"void {m}_step(void)", "{", "    /* outputs, in sorted execution order */"] + \
               [f"    {x}" for x in self.out] + ["    /* state updates */"] + [f"    {x}" for x in self.upd] + ["}", ""]
        return GeneratedCode(m, "\n".join(hdr), "\n".join(src), self.dtype, ins, outs)


def generate_c(model: str | dict | Compiled, dtype: str = "double") -> GeneratedCode:
    c = model if isinstance(model, Compiled) else compile_model(model)
    return _Gen(c, dtype).generate()
