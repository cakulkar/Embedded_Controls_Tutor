"""Simulink interoperability.

* `import_slx(path, variables)`: read a .slx (zip of XML; R2012b+ layout, including the
  R2019+ `simulink/systems/system_*.xml` split) into an ECSim model. Simulink omits
  parameters left at their default value, so the Simulink defaults are applied here.
  Parameters may reference workspace variables; pass them via `variables`, or parse a
  simple `.m` file of assignments with `parse_m_variables`.
* `export_build_script(model)`: MATLAB script that recreates the model with
  new_system/add_block/add_line. This is more robust across releases than writing .slx bytes.

Supported blocks are those in ecsim.model.blocks. Anything else is reported by block path,
so the learner knows exactly what to change.
"""
from __future__ import annotations

import ast
import operator
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from ecsim.model.blocks import ModelError
from ecsim.model.core import FORMAT

# ---- MATLAB numeric expression evaluation (safe subset) -------------------------------
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos}


def matlab_value(text: str, variables: dict[str, object] | None = None):
    """Evaluate a numeric MATLAB parameter: scalars, [a b c] / [a, b; c] vectors, variables,
    + - * / ^, inf, pi. Returns float or list[float]."""
    variables = variables or {}
    t = text.strip()
    if t.startswith("[") and t.endswith("]"):
        body = t[1:-1].replace(";", " ").replace(",", " ")
        parts = [p for p in re.split(r"\s+", body.strip()) if p]
        vals = [matlab_value(p, variables) for p in parts]
        out: list[float] = []
        for v in vals:
            out.extend(v if isinstance(v, list) else [v])
        return out
    expr = t.replace("^", "**").replace(".*", "*").replace("./", "/")
    try:
        node = ast.parse(expr, mode="eval").body
    except SyntaxError:
        raise ModelError(f"cannot evaluate parameter {text!r}") from None

    def ev(n):
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return float(n.value)
        if isinstance(n, ast.Name):
            if n.id in ("inf", "Inf"):
                return float("inf")
            if n.id == "pi":
                return 3.141592653589793
            if n.id in variables:
                v = variables[n.id]
                return [float(x) for x in v] if isinstance(v, list) else float(v)
            raise ModelError(f"parameter {text!r} uses variable '{n.id}', which is not defined. Provide it "
                             "in a variables .m file (e.g. Kp = 0.00174;)")
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        raise ModelError(f"unsupported expression in parameter {text!r}")
    return ev(node)


def parse_m_variables(text: str) -> dict[str, object]:
    """Parse simple `name = value;` assignments (comments with %) from a MATLAB script."""
    out: dict[str, object] = {}
    for raw in text.splitlines():
        line = raw.split("%", 1)[0]
        stmts, depth, cur = [], 0, ""
        for ch in line:                      # split on ';' outside brackets (several statements per line)
            depth += (ch == "[") - (ch == "]")
            if ch == ";" and depth == 0:
                stmts.append(cur)
                cur = ""
            else:
                cur += ch
        stmts.append(cur)
        for stmt in stmts:
            m = re.match(r"^\s*([A-Za-z]\w*)\s*=\s*(.+?)\s*$", stmt)
            if m:
                out[m.group(1)] = matlab_value(m.group(2), out)
    return out


# ---- import ---------------------------------------------------------------------
_DEFAULTS = {  # Simulink default values (omitted from .slx when unchanged)
    "Inport": {"Port": "1"}, "Outport": {"Port": "1"}, "Constant": {"Value": "1"}, "Gain": {"Gain": "1"},
    "Sum": {"Inputs": "|++"}, "Product": {"Inputs": "2"}, "Saturate": {"UpperLimit": "0.5", "LowerLimit": "-0.5"},
    "UnitDelay": {"InitialCondition": "0", "SampleTime": "-1"},
    "DiscreteIntegrator": {"gainval": "1.0", "InitialCondition": "0", "IntegratorMethod": "Integration: Forward Euler",
                           "LimitOutput": "off", "UpperSaturationLimit": "inf", "LowerSaturationLimit": "-inf",
                           "SampleTime": "1"},
    "RelationalOperator": {"Operator": "<="}, "Logic": {"Operator": "AND", "Inputs": "2"},
    "Switch": {"Criteria": "u2 >= Threshold", "Threshold": "0"},
    "Lookup_n-D": {"ExtrapMethod": "Linear", "NumberOfTableDimensions": "2"},
    "RateLimiter": {"RisingSlewLimit": "1", "FallingSlewLimit": "-1", "InitialCondition": "0"},
    "MinMax": {"Function": "min", "Inputs": "1"},
    "DiscreteTransferFcn": {"Numerator": "[1]", "Denominator": "[1 0.5]", "SampleTime": "1"},
}
_TYPE_MAP = {"Inport": "Inport", "Outport": "Outport", "Constant": "Constant", "Ground": "Ground",
             "Terminator": "Terminator", "Gain": "Gain", "Sum": "Sum", "Product": "Product", "Abs": "Abs",
             "MinMax": "MinMax", "Saturate": "Saturation", "RateLimiter": "RateLimiter", "UnitDelay": "UnitDelay",
             "DiscreteIntegrator": "DiscreteIntegrator", "DiscreteTransferFcn": "DiscreteTransferFcn",
             "RelationalOperator": "RelationalOperator", "Logic": "LogicalOperator", "Switch": "Switch",
             "Lookup_n-D": "Lookup1D", "SubSystem": "Subsystem"}


def _ident(name: str, used: set[str]) -> str:
    base = re.sub(r"\W+", "_", name.strip()).strip("_") or "blk"
    if not re.match(r"[A-Za-z_]", base):
        base = "b_" + base
    cand, k = base, 1
    while cand in used:
        k += 1
        cand = f"{base}_{k}"
    used.add(cand)
    return cand


class _Reader:
    def __init__(self, zf: zipfile.ZipFile, variables: dict) -> None:
        self.zf, self.vars = zf, variables
        self.errors: list[str] = []

    def system(self, elem: ET.Element) -> ET.Element:
        ref = elem.get("Ref")
        if ref:
            return ET.fromstring(self.zf.read(f"simulink/systems/{ref}.xml"))
        return elem

    def num(self, path: str, key: str, raw: str):
        try:
            return matlab_value(raw, self.vars)
        except ModelError as exc:
            self.errors.append(f"{path}: {key}: {exc}")
            return 0.0

    def convert(self, sys_elem: ET.Element, name: str, step: float, path: str) -> dict:
        sys_elem = self.system(sys_elem)
        model = {"format": FORMAT, "version": 1, "name": name,
                 "solver": {"type": "FixedStepDiscrete", "step": step}, "blocks": [], "lines": []}
        sid_to_id: dict[str, str] = {}
        used: set[str] = set()
        for b in sys_elem.findall("Block"):
            btype, bname, sid = b.get("BlockType"), b.get("Name", "block"), b.get("SID", "")
            bpath = f"{path}/{bname}"
            p = dict(_DEFAULTS.get(btype, {}))
            p.update({e.get("Name"): (e.text or "") for e in b.findall("P")})
            if btype not in _TYPE_MAP:
                self.errors.append(f"{bpath}: block type '{btype}' is not supported by the ECSim engine")
                continue
            bid = _ident(bname, used)
            sid_to_id[sid] = bid
            blk = {"id": bid, "type": _TYPE_MAP[btype], "params": {}}
            pos = p.get("Position")
            if pos:
                try:
                    v = matlab_value(pos)
                    blk["pos"] = [float(v[0]), float(v[1])]
                except (ModelError, IndexError, TypeError):
                    pass
            self._params(btype, p, blk, bpath, step)
            if btype == "SubSystem":
                inner = b.find("System")
                if inner is None:
                    self.errors.append(f"{bpath}: subsystem contents missing")
                    continue
                if p.get("Mask") == "on":
                    self.errors.append(f"{bpath}: masked subsystem parameters are not imported; values inside are used as-is")
                blk["params"]["model"] = self.convert(inner, bid, step, bpath)
            model["blocks"].append(blk)
        for ln in sys_elem.findall("Line"):
            self._line(ln, None, sid_to_id, model, path)
        return model

    def _params(self, btype: str, p: dict, blk: dict, bpath: str, step: float) -> None:
        P = blk["params"]
        n = lambda k: self.num(bpath, k, p[k])                       # noqa: E731
        if btype in ("Inport", "Outport"):
            P["port"] = int(float(p["Port"]))
            P["name"] = blk["id"]
        elif btype == "Constant":
            P["value"] = n("Value")
        elif btype == "Gain":
            P["gain"] = n("Gain")
        elif btype == "Sum":
            s = p["Inputs"].replace("|", "").strip()
            P["signs"] = "+" * int(s) if s.isdigit() else s
        elif btype == "Product":
            s = p["Inputs"].strip()
            P["ops"] = "*" * int(s) if s.isdigit() else s
        elif btype == "Saturate":
            P["upper"], P["lower"] = n("UpperLimit"), n("LowerLimit")
        elif btype == "UnitDelay":
            P["initial"] = n("InitialCondition" if "InitialCondition" in p else "X0")
        elif btype == "DiscreteIntegrator":
            method = p["IntegratorMethod"]
            if method not in ("Integration: Forward Euler", "Integration: Backward Euler"):
                self.errors.append(f"{bpath}: integrator method '{method}' not supported (use Forward or Backward Euler)")
            P["method"] = "BackwardEuler" if "Backward" in method else "ForwardEuler"
            P["gain"], P["initial"] = n("gainval"), n("InitialCondition")
            if p.get("LimitOutput") == "on":
                P["upper"], P["lower"] = n("UpperSaturationLimit"), n("LowerSaturationLimit")
        elif btype == "RelationalOperator":
            P["op"] = p["Operator"]
        elif btype == "Logic":
            P["op"], P["inputs"] = p["Operator"], int(float(p["Inputs"]))
        elif btype == "Switch":
            crit = p["Criteria"]
            P["criteria"] = "u2 ~= 0" if crit.startswith("u2 ~=") else crit
            P["threshold"] = n("Threshold")
        elif btype == "Lookup_n-D":
            if p.get("NumberOfTableDimensions") not in ("1",):
                self.errors.append(f"{bpath}: only 1-D lookup tables are supported")
            P["breakpoints"] = self.num(bpath, "BreakpointsForDimension1", p.get("BreakpointsForDimension1", "[0 1]"))
            P["table"] = self.num(bpath, "Table", p.get("Table", "[0 1]"))
            P["extrapolation"] = "clip" if p.get("ExtrapMethod") == "Clip" else "linear"
        elif btype == "RateLimiter":
            P["rising"], P["falling"], P["initial"] = n("RisingSlewLimit"), n("FallingSlewLimit"), n("InitialCondition")
        elif btype == "MinMax":
            P["fn"], P["inputs"] = p["Function"], int(float(p["Inputs"]))
        elif btype == "DiscreteTransferFcn":
            P["num"], P["den"] = n("Numerator"), n("Denominator")
        st = p.get("SampleTime")
        if st is not None and btype in ("UnitDelay", "DiscreteIntegrator", "DiscreteTransferFcn"):
            v = self.num(bpath, "SampleTime", st)
            if v != -1:
                blk["sample_time"] = float(v)

    def _line(self, elem, src, sid_to_id, model, path):
        params = {e.get("Name"): (e.text or "") for e in elem.findall("P")}
        src = params.get("Src", src)
        dst = params.get("Dst")
        if src and dst:
            s = self._endpoint(src, sid_to_id, path)
            d = self._endpoint(dst, sid_to_id, path)
            if s and d:
                model["lines"].append({"src": s, "dst": d})
        for br in elem.findall("Branch"):
            self._line(br, src, sid_to_id, model, path)

    def _endpoint(self, ref: str, sid_to_id, path) -> str | None:
        m = re.match(r"^(\d+)#(in|out):(\d+)$", ref.strip())
        if not m:
            self.errors.append(f"{path}: unsupported connection '{ref}' (enable/trigger ports are not supported)")
            return None
        bid = sid_to_id.get(m.group(1))
        return f"{bid}:{m.group(3)}" if bid else None


def import_slx(path: str | Path, variables: dict | None = None) -> tuple[dict, list[str]]:
    """Return (ECSim model, warnings). Raises ModelError listing every unsupported item."""
    path = Path(path)
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, FileNotFoundError, OSError) as exc:
        raise ModelError(f"{path.name}: not a readable .slx file ({exc}). Note: .mdl (text) files are not "
                         "supported; save as .slx.") from None
    with zf:
        try:
            root = ET.fromstring(zf.read("simulink/blockdiagram.xml"))
        except KeyError:
            raise ModelError(f"{path.name}: simulink/blockdiagram.xml not found") from None
        step, warnings = 0.01, []
        cfg = next((n for n in zf.namelist() if re.match(r"simulink/configSet\d*\.xml$", n)), None)
        if cfg:
            text = zf.read(cfg).decode("utf-8", "replace")
            fs = re.search(r'<P Name="FixedStep"[^>]*>([^<]+)</P>', text)
            st = re.search(r'<P Name="SolverType"[^>]*>([^<]+)</P>', text)
            if st and st.group(1).strip() != "Fixed-step":
                warnings.append(f"model solver is '{st.group(1).strip()}'; imported as fixed-step discrete")
            if fs:
                try:
                    step = float(matlab_value(fs.group(1), variables))
                except (ModelError, TypeError):
                    warnings.append(f"FixedStep '{fs.group(1)}' not numeric; using 0.01 s")
        else:
            warnings.append("no solver configuration found; using fixed step 0.01 s")
        model_elem = root.find(".//Model")
        system = model_elem.find("System") if model_elem is not None else root.find(".//System")
        if system is None:
            raise ModelError(f"{path.name}: no root system found")
        name = (model_elem.get("Name") if model_elem is not None else None) or path.stem
        reader = _Reader(zf, variables or {})
        model = reader.convert(system, name, step, name)
    hard = [e for e in reader.errors if "masked subsystem" not in e]
    warnings += [e for e in reader.errors if "masked subsystem" in e]
    if hard:
        raise ModelError("cannot import:\n  " + "\n  ".join(hard))
    return model, warnings


# ---- export: MATLAB build script --------------------------------------------------
def _m(v) -> str:
    if isinstance(v, list):
        return "[" + " ".join(_m(x) for x in v) + "]"
    if isinstance(v, float):
        if v == float("inf"):
            return "inf"
        if v == float("-inf"):
            return "-inf"
        return repr(v) if v != int(v) else str(int(v))
    return str(v)


def _block_args(b: dict) -> list[tuple[str, str]]:
    t, p = b["type"], b.get("params", {})
    from ecsim.model.blocks import LIB
    q = dict(LIB[t].defaults or {})
    q.update(p)
    m: list[tuple[str, str]]
    if t in ("Inport", "Outport"):
        m = [("Port", _m(q.get("port", 1)))]
    elif t == "Constant":
        m = [("Value", _m(q["value"]))]
    elif t == "Gain":
        m = [("Gain", _m(q["gain"]))]
    elif t == "Sum":
        m = [("Inputs", "|" + q["signs"])]
    elif t == "Product":
        m = [("Inputs", q["ops"])]
    elif t == "Saturation":
        m = [("UpperLimit", _m(q["upper"])), ("LowerLimit", _m(q["lower"]))]
    elif t == "UnitDelay":
        m = [("InitialCondition", _m(q["initial"]))]
    elif t == "DiscreteIntegrator":
        m = [("gainval", _m(q["gain"])), ("InitialCondition", _m(q["initial"])),
             ("IntegratorMethod", "Integration: Backward Euler" if q["method"] == "BackwardEuler"
              else "Integration: Forward Euler")]
        if "upper" in q or "lower" in q:
            m += [("LimitOutput", "on"), ("UpperSaturationLimit", _m(q.get("upper", float("inf")))),
                  ("LowerSaturationLimit", _m(q.get("lower", float("-inf"))))]
    elif t == "DiscreteTransferFcn":
        m = [("Numerator", _m(q["num"])), ("Denominator", _m(q["den"]))]
    elif t == "RateLimiter":
        m = [("RisingSlewLimit", _m(q["rising"])), ("FallingSlewLimit", _m(q["falling"])),
             ("InitialCondition", _m(q["initial"]))]
    elif t == "RelationalOperator":
        m = [("Operator", q["op"])]
    elif t == "LogicalOperator":
        m = [("Operator", q["op"])] + ([] if q["op"] == "NOT" else [("Inputs", _m(q["inputs"]))])
    elif t == "Switch":
        m = [("Criteria", q["criteria"]), ("Threshold", _m(q["threshold"]))]
    elif t == "Lookup1D":
        m = [("NumberOfTableDimensions", "1"), ("BreakpointsForDimension1", _m(q["breakpoints"])),
             ("Table", _m(q["table"])), ("ExtrapMethod", "Clip" if q.get("extrapolation") == "clip" else "Linear")]
    elif t == "MinMax":
        m = [("Function", q["fn"]), ("Inputs", _m(q["inputs"]))]
    else:
        m = []
    if b.get("sample_time", -1) not in (-1, None) and t in ("UnitDelay", "DiscreteIntegrator", "DiscreteTransferFcn",
                                                             "Constant", "Gain", "Sum"):
        m.append(("SampleTime", _m(float(b["sample_time"]))))
    elif t in ("DiscreteIntegrator", "DiscreteTransferFcn"):
        m.append(("SampleTime", "-1"))
    return m


def export_build_script(model: dict) -> str:
    from ecsim.model.blocks import LIB
    from ecsim.model.core import load_model
    m = load_model(model)
    name = re.sub(r"\W", "_", m["name"]) or "ecsim_model"
    step = m.get("solver", {}).get("step", 0.01)
    out = [f"% Generated by ECSim: rebuilds model '{name}' in Simulink.",
           "% Run in MATLAB, then save. Requires Simulink.", f"mdl = '{name}';",
           "if bdIsLoaded(mdl), close_system(mdl, 0); end", "new_system(mdl);",
           f"set_param(mdl, 'SolverType', 'Fixed-step', 'Solver', 'FixedStepDiscrete', 'FixedStep', '{_m(float(step))}');"]

    def emit(sysm: dict, parent: str) -> None:
        ordered = sorted(sysm["blocks"], key=lambda b: (b["type"] not in ("Inport", "Outport"),
                                                        b.get("params", {}).get("port", 0)))
        for b in ordered:
            lib = LIB[b["type"]].simulink
            x, y = (b.get("pos") or [100, 100])[:2]
            args = "".join(f", '{k}', '{v}'" for k, v in _block_args(b))
            out.append(f"add_block('{lib}', [{parent} '/{b['id']}']{args}, "
                       f"'Position', [{int(x)} {int(y)} {int(x) + 40} {int(y) + 30}]);")
            if b["type"] == "Subsystem":
                inner_path = f"[{parent} '/{b['id']}']"
                out.append(f"delete_line({inner_path}, 'In1/1', 'Out1/1');")
                out.append(f"delete_block([{inner_path} '/In1']); delete_block([{inner_path} '/Out1']);")
                emit(b["params"]["model"], inner_path)
        for ln in sysm["lines"]:
            sb, sp = ln["src"].rsplit(":", 1)
            db, dp = ln["dst"].rsplit(":", 1)
            out.append(f"add_line({parent}, '{sb}/{sp}', '{db}/{dp}', 'autorouting', 'on');")

    emit(m, "mdl")
    out.append("save_system(mdl);")
    return "\n".join(out) + "\n"
