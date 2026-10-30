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
