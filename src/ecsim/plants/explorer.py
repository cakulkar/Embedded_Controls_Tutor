"""Plant explorer scenarios for the Vehicle & Machine Simulator screen.

Each scenario exposes named parameters with defaults and ranges, and `run(params)` returns a
time vector plus panels of signals grouped by unit. These are open-loop experiments on the
plant models used by the graders, so what a learner observes here is exactly what their
controllers are tested against. Later phases register more plants (powertrain, hydraulics).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ecsim.model.blocks import ModelError


@dataclass(frozen=True)
class Param:
    key: str
    label: str
    default: float
    lo: float
    hi: float
    unit: str = ""


@dataclass
class Panel:
    title: str
    unit: str
    series: dict[str, list[float]] = field(default_factory=dict)


@dataclass
class Result:
    t: list[float]
    panels: list[Panel]
    summary: list[str]


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    description: str
    params: tuple[Param, ...]
    fn: Callable[[dict], Result]

    def run(self, values: dict | None = None) -> Result:
        p = {q.key: q.default for q in self.params}
        for k, v in (values or {}).items():
            q = next((q for q in self.params if q.key == k), None)
            if q is None:
                raise ModelError(f"{self.key}: unknown parameter '{k}'")
            v = float(v)
            if not q.lo <= v <= q.hi:
                raise ModelError(f"{q.label} must be in [{q.lo:g}, {q.hi:g}] {q.unit}".rstrip())
            p[k] = v
        return self.fn(p)


SCENARIOS: dict[str, Scenario] = {}


def register(s: Scenario) -> Scenario:
    if s.key in SCENARIOS:
        raise ValueError(f"duplicate scenario {s.key}")
    SCENARIOS[s.key] = s
    return s


# ---- DC motor ---------------------------------------------------------------------------
def _motor(p: dict) -> Result:
    from dataclasses import replace
    from ecsim.plants.dc_motor import DcMotor, DcMotorParams
    m = DcMotor(replace(DcMotorParams(), v_bat=p["v_bat"]))
    m.duty = p["duty_pct"] / 100.0
    dt, n = 0.001, int(round(p["duration_s"] / 0.001))
    t, rpm, cur, load = [], [], [], []
    for k in range(n + 1):
        tk = k * dt
        m.load_torque = p["load_Nm"] if tk >= p["load_at_s"] else 0.0
        t.append(tk); rpm.append(m.rpm); cur.append(m.current); load.append(m.load_torque)
        m.step(dt)
    mp = m.p
    return Result(t, [Panel("Speed", "rpm", {"speed": rpm}), Panel("Armature current", "A", {"current": cur}),
                      Panel("Load torque", "Nm", {"load": load})],
                  [f"Mechanical time constant τ = {mp.tau * 1000:.1f} ms",
                   f"Static gain {mp.gain_rpm_per_volt:.1f} rpm/V (no load)",
                   f"Final speed {rpm[-1]:.0f} rpm, current {cur[-1]:.2f} A"])


register(Scenario("dc_motor", "DC motor: duty step and load step",
                  "PWM-driven PM DC motor (the plant behind the speed-control exercises).",
                  (Param("duty_pct", "PWM duty", 50, 0, 100, "%"), Param("v_bat", "Supply", 24, 6, 48, "V"),
                   Param("load_Nm", "Load torque", 0.02, 0, 0.2, "Nm"), Param("load_at_s", "Load applied at", 0.5, 0, 5, "s"),
                   Param("duration_s", "Duration", 1.0, 0.05, 5, "s")), _motor))


# ---- Battery cell -----------------------------------------------------------------------
def _cell(p: dict) -> Result:
    from ecsim.plants.battery import Cell, ocv
    c = Cell(soc=p["soc0_pct"] / 100.0)
    dt = 1.0
    n = int(round((p["load_s"] + p["rest_s"]) / dt))
    t, v, o, soc, temp, i = [], [], [], [], [], []
    for k in range(n + 1):
        cur = -p["discharge_A"] if k * dt < p["load_s"] else 0.0
        t.append(k * dt); i.append(cur); v.append(c.terminal_voltage(cur)); o.append(ocv(c.soc))
        soc.append(c.soc * 100); temp.append(c.temp)
        c.step(cur, dt)
    load_end = max(0, min(int(round(p["load_s"] / dt)) - 1, n))   # last sample under load
    return Result(t, [Panel("Cell voltage", "V", {"terminal": v, "OCV(SOC)": o}), Panel("SOC", "%", {"soc": soc}),
                      Panel("Current", "A", {"current": i}), Panel("Temperature", "degC", {"cell": temp})],
                  [f"SOC {soc[0]:.1f} % → {soc[-1]:.1f} %",
                   f"Voltage gap to OCV at end of load: {(o[load_end] - v[load_end]) * 1000:.0f} mV "
                   "(ohmic + polarisation; why voltage under load misleads SOC)",
                   f"Gap after rest: {(o[-1] - v[-1]) * 1000:.1f} mV", f"Peak temperature {max(temp):.2f} °C"])


register(Scenario("battery_cell", "Li-ion cell: discharge pulse and relaxation",
                  "50 Ah Thevenin 1RC cell with thermal model (the plant behind the SOC exercise).",
                  (Param("soc0_pct", "Initial SOC", 80, 5, 100, "%"), Param("discharge_A", "Discharge current", 50, 0, 300, "A"),
                   Param("load_s", "Load duration", 600, 0, 7200, "s"), Param("rest_s", "Rest duration", 600, 0, 7200, "s")), _cell))


# ---- HV precharge -----------------------------------------------------------------------
def _precharge(p: dict) -> Result:
    from ecsim.plants.battery import PrechargeCircuit
    pc = PrechargeCircuit(r_pre=p["r_pre"], c_link=p["c_link_mF"] / 1000.0)
    dt = 0.001
    n = int(round(p["duration_s"] / dt))
    t, v, cur = [], [], []
    t95 = None
    for k in range(n + 1):
        main = p["main_at_s"] > 0 and k * dt >= p["main_at_s"]
        t.append(k * dt); v.append(pc.v_link)
        cur.append(0.0 if main else (pc.v_pack - pc.v_link) / pc.r_pre)
        if t95 is None and pc.v_link >= 0.95 * pc.v_pack:
            t95 = k * dt
        pc.step(True, main, dt)
    tau = pc.r_pre * pc.c_link
    return Result(t, [Panel("DC-link voltage", "V", {"v_link": v, "95 % of pack": [0.95 * pc.v_pack] * len(t)}),
                      Panel("Precharge current", "A", {"i_pre": cur})],
                  [f"τ = RC = {tau * 1000:.1f} ms; 95 % expected at 3.0·τ = {tau * 2995.7:.1f} ms",
                   f"95 % reached at {t95 * 1000:.0f} ms" if t95 is not None else "95 % not reached in this run",
                   f"Peak precharge current {max(cur):.1f} A (pack / R)"])


register(Scenario("precharge", "HV precharge circuit",
                  "400 V pack charging the DC-link capacitor through the precharge resistor (contactor exercise plant).",
                  (Param("r_pre", "Precharge resistor", 50, 1, 1000, "Ohm"), Param("c_link_mF", "DC-link capacitance", 1, 0.05, 20, "mF"),
                   Param("main_at_s", "Close main at (0 = never)", 0, 0, 5, "s"), Param("duration_s", "Duration", 0.5, 0.01, 5, "s")),
                  _precharge))
