"""Grade a learner's Simulink model (.slx/.mdl) with real MATLAB/Simulink.

Requires MATLAB + Simulink and the MATLAB Engine API for Python (`pip install matlabengine`
matching your MATLAB release, or install from <matlabroot>/extern/engines/python).
A shared MATLAB session is reused if one exists (run `matlab.engine.shareEngine` in MATLAB
to avoid the start-up delay); otherwise one is started and kept for the process.

Contract for learner models (stated in every exercise):
* root-level Inport/Outport block names and order match the exercise interface,
* Solver: Fixed-step, discrete, with the exercise's fixed step,
* closed-loop tests reference the model from a generated harness (Model block), so the
  model must be referenceable (standard for a fixed-step discrete controller model).

VERIFICATION STATUS: the command sequence is unit-tested against a recording fake and the
data path against a stand-in session in CI. End-to-end runs against real MATLAB must be
confirmed on a licensed machine (see docs/PROGRESS.md).
"""
from __future__ import annotations

import importlib
import importlib.util
import re
from pathlib import Path
from typing import Any, Protocol

from ecsim.model.blocks import ModelError
from ecsim.model.mil import V_MAX, V_MIN, DiscreteMotor, TestTrace, n_samples, signal_value

_IDENT = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,62}$")

OPEN_LOOP_SIM = (
    "ecsim_out = sim('{name}', 'StopTime', '{stop:.17g}', 'LoadExternalInput', 'on', "
    "'ExternalInput', 'ecsim_u', 'SaveOutput', 'on', 'OutputSaveName', 'yout', 'SaveTime', 'on', "
    "'TimeSaveName', 'tout', 'SaveFormat', 'Array', 'ReturnWorkspaceOutputs', 'on'); "
    "ecsim_t = ecsim_out.get('tout'); ecsim_y = ecsim_out.get('yout');"
)

CLOSED_LOOP_HARNESS = """\
ecsim_h = 'ecsim_cl_harness';
if bdIsLoaded(ecsim_h), close_system(ecsim_h, 0); end
new_system(ecsim_h);
set_param(ecsim_h, 'SolverType', 'Fixed-step', 'Solver', 'FixedStepDiscrete', 'FixedStep', '{ts:.17g}', 'StopTime', '{stop:.17g}');
add_block('simulink/Sources/In1', [ecsim_h '/T_load']);
add_block('simulink/Ports & Subsystems/Model', [ecsim_h '/ctrl'], 'ModelName', '{name}');
add_block('simulink/Discontinuities/Saturation', [ecsim_h '/vsat'], 'UpperLimit', '{vmax:.17g}', 'LowerLimit', '{vmin:.17g}');
add_block('simulink/Signal Routing/Mux', [ecsim_h '/mux'], 'Inputs', '2');
add_block('simulink/Discrete/Discrete State-Space', [ecsim_h '/motor'], 'A', '{A:.17g}', 'B', '[{B0:.17g} {B1:.17g}]', 'C', '{C:.17g}', 'D', '[0 0]', 'InitialCondition', '0', 'SampleTime', '{ts:.17g}');
add_block('simulink/Sinks/Out1', [ecsim_h '/speed_rpm']);
add_block('simulink/Sinks/Out1', [ecsim_h '/cmd']);
add_block('simulink/Sinks/Out1', [ecsim_h '/motor_voltage_V']);
add_line(ecsim_h, 'motor/1', 'ctrl/1');
add_line(ecsim_h, 'motor/1', 'speed_rpm/1');
add_line(ecsim_h, 'ctrl/1', 'vsat/1');
add_line(ecsim_h, 'ctrl/1', 'cmd/1');
add_line(ecsim_h, 'vsat/1', 'mux/1');
add_line(ecsim_h, 'T_load/1', 'mux/2');
add_line(ecsim_h, 'mux/1', 'motor/1');
add_line(ecsim_h, 'vsat/1', 'motor_voltage_V/1');
ecsim_out = sim(ecsim_h, 'LoadExternalInput', 'on', 'ExternalInput', 'ecsim_u', 'SaveOutput', 'on', 'OutputSaveName', 'yout', 'SaveTime', 'on', 'TimeSaveName', 'tout', 'SaveFormat', 'Array', 'ReturnWorkspaceOutputs', 'on');
ecsim_t = ecsim_out.get('tout'); ecsim_y = ecsim_out.get('yout');
close_system(ecsim_h, 0);
"""

# HV precharge plant built from standard blocks. External inputs: hv_request, resistor_open (0/1).
# v_link(k+1) = k_main ? v_pack : (k_precharge AND NOT open) ? v_pack + (v_link - v_pack)*a : v_link*b
PRECHARGE_HARNESS = """\
ecsim_h = 'ecsim_pc_harness';
if bdIsLoaded(ecsim_h), close_system(ecsim_h, 0); end
new_system(ecsim_h);
set_param(ecsim_h, 'SolverType', 'Fixed-step', 'Solver', 'FixedStepDiscrete', 'FixedStep', '{ts:.17g}', 'StopTime', '{stop:.17g}');
add_block('simulink/Sources/In1', [ecsim_h '/hv_request']);
add_block('simulink/Sources/In1', [ecsim_h '/resistor_open']);
add_block('simulink/Sources/Constant', [ecsim_h '/v_pack'], 'Value', '{v_pack:.17g}');
add_block('simulink/Ports & Subsystems/Model', [ecsim_h '/ctrl'], 'ModelName', '{name}');
add_block('simulink/Discrete/Unit Delay', [ecsim_h '/v_link'], 'InitialCondition', '0', 'SampleTime', '{ts:.17g}');
add_block('simulink/Math Operations/Gain', [ecsim_h '/charge_a'], 'Gain', '{a:.17g}');
add_block('simulink/Sources/Constant', [ecsim_h '/charge_c'], 'Value', '{c:.17g}');
add_block('simulink/Math Operations/Sum', [ecsim_h '/charge'], 'Inputs', '++');
add_block('simulink/Math Operations/Gain', [ecsim_h '/bleed'], 'Gain', '{b:.17g}');
add_block('simulink/Sources/Constant', [ecsim_h '/one'], 'Value', '1');
add_block('simulink/Math Operations/Sum', [ecsim_h '/intact'], 'Inputs', '+-');
add_block('simulink/Math Operations/Product', [ecsim_h '/pre_eff'], 'Inputs', '2');
add_block('simulink/Signal Routing/Switch', [ecsim_h '/sel_pre'], 'Criteria', 'u2 > Threshold', 'Threshold', '0.5');
add_block('simulink/Signal Routing/Switch', [ecsim_h '/sel_main'], 'Criteria', 'u2 > Threshold', 'Threshold', '0.5');
add_block('simulink/Sinks/Out1', [ecsim_h '/v_link_V']);
add_block('simulink/Sinks/Out1', [ecsim_h '/k_precharge']);
add_block('simulink/Sinks/Out1', [ecsim_h '/k_main']);
add_block('simulink/Sinks/Out1', [ecsim_h '/fault']);
add_line(ecsim_h, 'hv_request/1', 'ctrl/1');
add_line(ecsim_h, 'v_pack/1', 'ctrl/2');
add_line(ecsim_h, 'v_link/1', 'ctrl/3');
add_line(ecsim_h, 'v_link/1', 'charge_a/1');
add_line(ecsim_h, 'charge_a/1', 'charge/1');
add_line(ecsim_h, 'charge_c/1', 'charge/2');
add_line(ecsim_h, 'v_link/1', 'bleed/1');
add_line(ecsim_h, 'one/1', 'intact/1');
add_line(ecsim_h, 'resistor_open/1', 'intact/2');
add_line(ecsim_h, 'ctrl/1', 'pre_eff/1');
add_line(ecsim_h, 'intact/1', 'pre_eff/2');
add_line(ecsim_h, 'charge/1', 'sel_pre/1');
add_line(ecsim_h, 'pre_eff/1', 'sel_pre/2');
add_line(ecsim_h, 'bleed/1', 'sel_pre/3');
add_line(ecsim_h, 'v_pack/1', 'sel_main/1');
add_line(ecsim_h, 'ctrl/2', 'sel_main/2');
add_line(ecsim_h, 'sel_pre/1', 'sel_main/3');
add_line(ecsim_h, 'sel_main/1', 'v_link/1');
add_line(ecsim_h, 'v_link/1', 'v_link_V/1');
add_line(ecsim_h, 'ctrl/1', 'k_precharge/1');
add_line(ecsim_h, 'ctrl/2', 'k_main/1');
add_line(ecsim_h, 'ctrl/3', 'fault/1');
ecsim_out = sim(ecsim_h, 'LoadExternalInput', 'on', 'ExternalInput', 'ecsim_u', 'SaveOutput', 'on', 'OutputSaveName', 'yout', 'SaveTime', 'on', 'TimeSaveName', 'tout', 'SaveFormat', 'Array', 'ReturnWorkspaceOutputs', 'on');
ecsim_t = ecsim_out.get('tout'); ecsim_y = ecsim_out.get('yout');
close_system(ecsim_h, 0);
"""
PRECHARGE_PORTS = (["hv_request", "v_pack_V", "v_link_V"], ["k_precharge", "k_main", "fault"])


def precharge_coefficients(r_pre: float, c_link: float, v_pack: float, ts: float) -> dict:
    """Exact discretisation of the ECSim precharge circuit (identical to PrechargeCircuit.step)."""
    import math
    a = math.exp(-ts / (r_pre * c_link))
    return {"a": a, "c": v_pack * (1.0 - a), "b": math.exp(-ts / 5.0), "v_pack": v_pack}


def matlab_engine_available() -> bool:
    try:
        return importlib.util.find_spec("matlab") is not None and importlib.util.find_spec("matlab.engine") is not None
    except (ImportError, ValueError):
        return False


class Session(Protocol):
    def load(self, path: Path) -> str: ...
    def root_ports(self, name: str, kind: str) -> list[str]: ...
    def fixed_step(self, name: str) -> float: ...
    def sim_open_loop(self, name: str, u: list[list[float]], stop: float) -> tuple[list[float], list[list[float]]]: ...
    def sim_closed_loop(self, name: str, plant: DiscreteMotor, u: list[list[float]], stop: float,
                        ts: float) -> tuple[list[float], list[list[float]]]: ...
    def sim_precharge(self, name: str, coeffs: dict, u: list[list[float]], stop: float,
                      ts: float) -> tuple[list[float], list[list[float]]]: ...
    def close(self, name: str) -> None: ...


def _rows(m: Any) -> list[list[float]]:
    """matlab.double (row-major iterable) -> list of rows of float."""
    out = []
    for row in m:
        try:
            out.append([float(v) for v in row])
        except TypeError:
            out.append([float(row)])
    return out


class EngineSession:
    """Real MATLAB through matlab.engine. `eng` and `double` are injectable for tests."""

    def __init__(self, eng: Any, double: Any) -> None:
        self.eng = eng
        self.double = double

    @classmethod
    def start(cls) -> "EngineSession":
        me = importlib.import_module("matlab.engine")
        matlab = importlib.import_module("matlab")
        shared = me.find_matlab()
        eng = me.connect_matlab(shared[0]) if shared else me.start_matlab("-nodesktop")
        return cls(eng, matlab.double)

    def _call(self, what: str, fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except Exception as exc:    # matlab.engine.MatlabExecutionError and friends
            raise ModelError(f"MATLAB error while {what}: {exc}") from None

    def load(self, path: Path) -> str:
        name = path.stem
        if not _IDENT.match(name):
            raise ModelError(f"model file name '{name}' is not a valid Simulink model name")
        self._call("adding the model folder to the path", self.eng.addpath, str(path.parent), nargout=0)
        self._call("loading the model", self.eng.load_system, str(path), nargout=0)
        return name

    def root_ports(self, name: str, kind: str) -> list[str]:
        paths = self._call("finding ports", self.eng.find_system, name, "SearchDepth", 1.0, "BlockType", kind)
        if isinstance(paths, str):
            paths = [paths]
        ports = []
        for p in paths:
            num = int(float(self._call("reading port number", self.eng.get_param, p, "Port")))
            ports.append((num, self._call("reading port name", self.eng.get_param, p, "Name")))
        return [n for _, n in sorted(ports)]

    def fixed_step(self, name: str) -> float:
        stype = self._call("reading the solver type", self.eng.get_param, name, "SolverType")
        if stype != "Fixed-step":
            raise ModelError(f"solver type is '{stype}'; set Solver to Fixed-step, discrete (no continuous states)")
        self._call("evaluating FixedStep (use a number, not 'auto')", self.eng.eval,
                   f"ecsim_ts = eval(get_param('{name}', 'FixedStep'));", nargout=0)
        return float(self.eng.workspace["ecsim_ts"])

    def _result(self) -> tuple[list[float], list[list[float]]]:
        t = [r[0] for r in _rows(self.eng.workspace["ecsim_t"])]
        y = _rows(self.eng.workspace["ecsim_y"])
        return t, y

    def sim_open_loop(self, name, u, stop):
        self.eng.workspace["ecsim_u"] = self.double(u)
        self._call("simulating", self.eng.eval, OPEN_LOOP_SIM.format(name=name, stop=stop), nargout=0)
        return self._result()

    def sim_closed_loop(self, name, plant, u, stop, ts):
        ss = plant.as_state_space()
        script = CLOSED_LOOP_HARNESS.format(name=name, ts=ts, stop=stop, vmin=V_MIN, vmax=V_MAX,
                                            A=ss["A"], B0=ss["B"][0], B1=ss["B"][1], C=ss["C"])
        self.eng.workspace["ecsim_u"] = self.double(u)
        self._call("building and simulating the closed-loop harness", self.eng.eval, script, nargout=0)
        return self._result()

    def sim_precharge(self, name, coeffs, u, stop, ts):
        script = PRECHARGE_HARNESS.format(name=name, ts=ts, stop=stop, **coeffs)
        self.eng.workspace["ecsim_u"] = self.double(u)
        self._call("building and simulating the precharge harness", self.eng.eval, script, nargout=0)
        return self._result()

    def close(self, name: str) -> None:
        try:
            self.eng.close_system(name, 0.0, nargout=0)
        except Exception:   # noqa: BLE001 - closing is best effort
            pass


_SESSION: Session | None = None


def get_session() -> Session:
    global _SESSION
    if _SESSION is None:
        if not matlab_engine_available():
            raise ModelError("MATLAB Engine for Python is not installed; cannot run .slx models. "
                             "Submit an ECSim model instead, or install the engine for your MATLAB release.")
        _SESSION = EngineSession.start()
    return _SESSION


def set_session(session: Session | None) -> None:
    """Inject a session (tests, or a custom MATLAB connection)."""
    global _SESSION
    _SESSION = session


class SimulinkBackend:
    name = "Simulink (MATLAB)"

    def __init__(self, session: Session | None = None) -> None:
        self._session = session

    @property
    def session(self) -> Session:
        return self._session or get_session()

    def prepare(self, submission: str, spec: dict) -> dict:
        path = Path(submission.strip()).expanduser()
        if not path.is_file():
            raise ModelError(f"model file not found: {path}")
        return {"name": self.session.load(path.resolve()), "path": path}

    def interface(self, h: dict) -> tuple[list[str], list[str]]:
        return self.session.root_ports(h["name"], "Inport"), self.session.root_ports(h["name"], "Outport")

    def step_size(self, h: dict) -> float:
        return self.session.fixed_step(h["name"])

    def release(self, h: dict) -> None:
        self.session.close(h["name"])

    def _check_len(self, t: list[float], n: int) -> None:
        if len(t) != n:
            raise ModelError(f"Simulink returned {len(t)} samples, expected {n}; check that the model is "
                             "fixed-step discrete with the required step and no variable-step blocks")

    def run_open_loop(self, h: dict, test: dict, step: float) -> TestTrace:
        ins, outs = self.interface(h)
        n = n_samples(float(test["duration"]), step)
        ts = [k * step for k in range(n)]
        u = [[t] + [signal_value(test["inputs"][name], t) for name in ins] for t in ts]
        t, y = self.session.sim_open_loop(h["name"], u, (n - 1) * step)
        self._check_len(t, n)
        sig = {name: [r[i + 1] for r in u] for i, name in enumerate(ins)}
        for j, name in enumerate(outs):
            sig[name] = [row[j] for row in y]
        return TestTrace(ts, sig)

    def run_closed_loop(self, h: dict, test: dict, step: float) -> TestTrace:
        plant = test.get("plant", "dc_motor")
        if plant == "precharge":
            return self._precharge(h, test, step)
        if plant != "dc_motor":
            raise ModelError(f"closed-loop tests with the '{plant}' plant run on the ECSim engine only in this "
                             "release; submit an ECSim model (e.g. import your .slx in the Model Studio)")
        _, outs = self.interface(h)
        n = n_samples(float(test["duration"]), step)
        ts = [k * step for k in range(n)]
        load = test.get("load_torque", {"const": 0.0})
        u = [[t, signal_value(load, t)] for t in ts]
        t, y = self.session.sim_closed_loop(h["name"], DiscreteMotor.at(step), u, (n - 1) * step, step)
        self._check_len(t, n)
        return TestTrace(ts, {"speed_rpm": [r[0] for r in y], outs[0]: [r[1] for r in y],
                              "motor_voltage_V": [r[2] for r in y]})

    def _precharge(self, h: dict, test: dict, step: float) -> TestTrace:
        ins, outs = self.interface(h)
        if (ins, outs) != PRECHARGE_PORTS:
            raise ModelError(f"precharge harness needs inports {PRECHARGE_PORTS[0]} and outports {PRECHARGE_PORTS[1]} "
                             f"in this order; the model has {ins} / {outs}")
        pp = test.get("plant_params", {})
        coeffs = precharge_coefficients(float(pp.get("r_pre", 50.0)), float(pp.get("c_link", 0.001)), 400.0, step)
        open_at = pp.get("resistor_open_at")
        n = n_samples(float(test["duration"]), step)
        ts = [k * step for k in range(n)]
        u = [[t, signal_value(test["hv_request"], t),
              1.0 if open_at is not None and t >= float(open_at) - 1e-12 else 0.0] for t in ts]
        t, y = self.session.sim_precharge(h["name"], coeffs, u, (n - 1) * step, step)
        self._check_len(t, n)
        sig = {"hv_request": [r[1] for r in u], "v_pack_V": [coeffs["v_pack"]] * n, "v_link_V": [r[0] for r in y]}
        for j, name in enumerate(outs):
            sig[name] = [r[j + 1] for r in y]
        return TestTrace(ts, sig)
