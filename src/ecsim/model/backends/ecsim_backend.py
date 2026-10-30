from __future__ import annotations

from ecsim.model.core import Compiled, Simulator, compile_model
from ecsim.model.mil import V_MAX, V_MIN, DiscreteMotor, TestTrace, n_samples, signal_value
from ecsim.plants.dc_motor import RPM_PER_RADS


class EcsimBackend:
    name = "ECSim engine"

    def prepare(self, submission: str, spec: dict) -> Compiled:
        return compile_model(submission)

    def interface(self, c: Compiled) -> tuple[list[str], list[str]]:
        return c.inport_names, c.outport_names

    def step_size(self, c: Compiled) -> float:
        return c.step

    def release(self, c: Compiled) -> None:
        pass

    def run_open_loop(self, c: Compiled, test: dict, step: float) -> TestTrace:
        sim = Simulator(c)
        n = n_samples(float(test["duration"]), step)
        sig = {name: [] for name in c.inport_names + c.outport_names}
        ts = []
        for k in range(n):
            t = k * step
            u = [signal_value(test["inputs"][name], t) for name in c.inport_names]
            y = sim.step(u)
            ts.append(t)
            for name, v in zip(c.inport_names, u):
                sig[name].append(v)
            for name, v in zip(c.outport_names, y):
                sig[name].append(v)
        return TestTrace(ts, sig)

    def run_closed_loop(self, c: Compiled, test: dict, step: float) -> TestTrace:
        """Controller model: inport 1 = measured speed [rpm], outport 1 = voltage command [V]."""
        sim = Simulator(c)
        plant = DiscreteMotor.at(step)
        load = test.get("load_torque", {"const": 0.0})
        n = n_samples(float(test["duration"]), step)
        w = 0.0
        ts, speed, cmd, volts = [], [], [], []
        for k in range(n):
            t = k * step
            rpm = w * RPM_PER_RADS
            u_cmd = sim.step([rpm])[0]
            v = min(max(u_cmd, V_MIN), V_MAX)
            ts.append(t); speed.append(rpm); cmd.append(u_cmd); volts.append(v)
            w = plant.a * w + plant.bv * v - plant.bt * signal_value(load, t)
        return TestTrace(ts, {"speed_rpm": speed, c.outport_names[0]: cmd, "motor_voltage_V": volts})
