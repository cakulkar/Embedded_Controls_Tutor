"""Test doubles for the Simulink backend (no MATLAB in CI)."""
from __future__ import annotations

from pathlib import Path

from ecsim.model.blocks import ModelError
from ecsim.model.core import Simulator, compile_model
from ecsim.model.mil import V_MAX, V_MIN
from ecsim.plants.dc_motor import RPM_PER_RADS


class EcsimStandInSession:
    """Implements the Simulink Session protocol by executing an ECSim model stored in a file
    named like a Simulink model. Exercises everything in the backend except MATLAB itself."""

    def __init__(self) -> None:
        self.loaded: dict[str, object] = {}
        self.closed: list[str] = []

    def load(self, path: Path) -> str:
        self.loaded[path.stem] = compile_model(path.read_text(encoding="utf-8"))
        return path.stem

    def root_ports(self, name, kind):
        c = self.loaded[name]
        return c.inport_names if kind == "Inport" else c.outport_names

    def fixed_step(self, name):
        return self.loaded[name].step

    def sim_open_loop(self, name, u, stop):
        sim = Simulator(self.loaded[name])
        return [r[0] for r in u], [sim.step(r[1:]) for r in u]

    def sim_closed_loop(self, name, plant, u, stop, ts):
        sim = Simulator(self.loaded[name])
        w, t, y = 0.0, [], []
        for tk, load in u:
            rpm = w * RPM_PER_RADS
            cmd = sim.step([rpm])[0]
            v = min(max(cmd, V_MIN), V_MAX)
            t.append(tk)
            y.append([rpm, cmd, v])
            w = plant.a * w + plant.bv * v - plant.bt * load
        return t, y

    def sim_precharge(self, name, coeffs, u, stop, ts):
        """Executes the same block equations as PRECHARGE_HARNESS (Unit Delay + two Switches)."""
        sim = Simulator(self.loaded[name])
        vl, t, y = 0.0, [], []
        for tk, req, opened in u:
            k_pre, k_main, fault = sim.step([req, coeffs["v_pack"], vl])
            t.append(tk)
            y.append([vl, k_pre, k_main, fault])
            charge = coeffs["a"] * vl + coeffs["c"]
            sel_pre = charge if k_pre * (1 - opened) > 0.5 else coeffs["b"] * vl
            vl = coeffs["v_pack"] if k_main > 0.5 else sel_pre
        return t, y

    def close(self, name):
        self.closed.append(name)


class RecordingEngine:
    """Mimics the parts of a matlab.engine object the EngineSession uses, and records calls."""

    def __init__(self, ports=None, solver="Fixed-step", ts=0.01, result=None, fail_on=None) -> None:
        self.calls: list[tuple] = []
        self.workspace: dict = {}
        self.ports = ports or {"Inport": [("m/speed_meas_rpm", "1", "speed_meas_rpm")],
                               "Outport": [("m/voltage_cmd_V", "1", "voltage_cmd_V")]}
        self.solver, self.ts, self.result, self.fail_on = solver, ts, result, fail_on

    def _maybe_fail(self, what):
        if self.fail_on and self.fail_on in what:
            raise RuntimeError(f"MatlabExecutionError: simulated failure in {what}")

    def addpath(self, p, nargout=0):
        self.calls.append(("addpath", p))

    def load_system(self, p, nargout=0):
        self.calls.append(("load_system", p))
        self._maybe_fail("load_system")

    def find_system(self, name, *args):
        self.calls.append(("find_system", name) + args)
        kind = args[-1]
        paths = [p for p, _, _ in self.ports[kind]]
        return paths[0] if len(paths) == 1 else paths

    def get_param(self, obj, param):
        self.calls.append(("get_param", obj, param))
        if param == "SolverType":
            return self.solver
        for kind in self.ports.values():
            for path, port, name in kind:
                if path == obj:
                    return port if param == "Port" else name
        raise ModelError(f"unexpected get_param {obj} {param}")

    def eval(self, code, nargout=0):
        self.calls.append(("eval", code))
        self._maybe_fail(code)
        if "ecsim_ts" in code:
            self.workspace["ecsim_ts"] = self.ts
        if "sim(" in code:
            n = len(self.workspace["ecsim_u"])
            t, y = self.result or ([[i * 0.01] for i in range(n)], [[0.0, 0.0, 0.0] for _ in range(n)])
            self.workspace["ecsim_t"], self.workspace["ecsim_y"] = t, y

    def close_system(self, name, flag, nargout=0):
        self.calls.append(("close_system", name))


def fake_double(rows):
    return [list(r) for r in rows]


# ---- synthetic .slx writer (Simulink file layout) for import tests ---------------------
def write_slx(model: dict, path, split_systems: bool = True, fixed_step: str | None = None,
              omit_defaults: bool = True, extra_blocks: list[str] | None = None) -> None:
    """Write an .slx-layout zip from an ECSim model, using Simulink BlockType names and
    parameter names. `omit_defaults` drops values equal to Simulink defaults, as Simulink does."""
    import zipfile
    from xml.sax.saxutils import escape
    from ecsim.model.slx import _DEFAULTS, _TYPE_MAP, _block_args
    rev = {v: k for k, v in _TYPE_MAP.items()}
    systems: dict[str, str] = {}
    counter = [0]

    def sys_xml(m: dict) -> str:
        sid_of, blocks_xml = {}, []
        for b in m["blocks"]:
            counter[0] += 1
            sid = str(counter[0])
            sid_of[b["id"]] = sid
            stype = rev[b["type"]]
            params = dict(_block_args(b))
            if omit_defaults:
                for k, v in _DEFAULTS.get(stype, {}).items():
                    if params.get(k) == v:
                        params.pop(k)
            x, y = b.get("pos", [50, 50])
            params["Position"] = f"[{int(x)}, {int(y)}, {int(x) + 30}, {int(y) + 30}]"
            ps = "".join(f'<P Name="{k}">{escape(str(v))}</P>' for k, v in params.items())
            inner = ""
            if b["type"] == "Subsystem":
                inner = sys_ref(b["params"]["model"])
            blocks_xml.append(f'<Block BlockType="{stype}" Name="{escape(b["id"])}" SID="{sid}">{ps}{inner}</Block>')
        for i, extra in enumerate(extra_blocks or []):
            blocks_xml.append(f'<Block BlockType="{extra}" Name="Unsupported {i}" SID="9{i}"/>')
        by_src: dict[str, list[str]] = {}
        for ln in m["lines"]:
            by_src.setdefault(ln["src"], []).append(ln["dst"])
        lines_xml = []
        for src, dsts in by_src.items():
            sb, sp = src.rsplit(":", 1)
            s = f'<P Name="Src">{sid_of[sb]}#out:{sp}</P>'
            ds = [f'<P Name="Dst">{sid_of[d.rsplit(":", 1)[0]]}#in:{d.rsplit(":", 1)[1]}</P>' for d in dsts]
            if len(ds) == 1:
                lines_xml.append(f"<Line>{s}{ds[0]}</Line>")
            else:   # fan-out is stored as branches
                lines_xml.append(f"<Line>{s}" + "".join(f"<Branch>{d}</Branch>" for d in ds) + "</Line>")
        return "<System>" + "".join(blocks_xml) + "".join(lines_xml) + "</System>"

    def sys_ref(m: dict) -> str:
        body = sys_xml(m)
        if split_systems:
            ref = f"system_{len(systems) + 1}"
            systems[ref] = body
            return f'<System Ref="{ref}"/>'
        return body

    root = sys_ref(model)
    bd = f'<?xml version="1.0" encoding="utf-8"?><ModelInformation Version="1.0"><Model Name="{model["name"]}">{root}</Model></ModelInformation>'
    step = fixed_step if fixed_step is not None else repr(model["solver"]["step"])
    cfg = (f'<?xml version="1.0" encoding="utf-8"?><ConfigSet><P Name="SolverType">Fixed-step</P>'
           f'<P Name="FixedStep">{step}</P></ConfigSet>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("simulink/blockdiagram.xml", bd)
        z.writestr("simulink/configSet0.xml", cfg)
        for ref, body in systems.items():
            z.writestr(f"simulink/systems/{ref}.xml", body)
