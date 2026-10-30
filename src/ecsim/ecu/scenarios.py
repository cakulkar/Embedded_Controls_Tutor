"""Closed-loop scenarios: virtual ECU + plant in lock-step, fault injection, checks.

A scenario is data plus small check functions, so new departments can add their
own scenarios (BMS, hydraulics...) without touching the engine.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from ecsim.ecu.profiles import get_profile
from ecsim.ecu.vecu import HANG_US, RESET_WATCHDOG, AdcFault, VirtualEcu
from ecsim.plants.dc_motor import DcMotor

# Motor-control application contract (shared with content A05).
SPEED_SETPOINT_RPM = 3000.0
SPEED_TOL_RPM = 60.0
DEM_SPEED_SENSOR = 1
DEM_WDG_RESET = 3
ADC_CH_SPEED, ADC_CH_CURRENT = 0, 1


@dataclass
class Event:
    time_ms: int
    kind: str                     # adc_fault | clear_adc_fault | load_torque | task_wcet
    args: dict = field(default_factory=dict)
    label: str = ""


@dataclass
class Trace:
    t_ms: list[int] = field(default_factory=list)
    speed_rpm: list[float] = field(default_factory=list)
    duty_pct: list[float] = field(default_factory=list)
    current_a: list[float] = field(default_factory=list)
    sensor_v: list[float] = field(default_factory=list)
    in_reset: list[int] = field(default_factory=list)

    def window(self, start: int, end: int) -> range:
        return range(max(0, start - 1), min(len(self.t_ms), end))

    def to_csv(self) -> str:
        rows = ["t_ms,speed_rpm,duty_pct,current_a,speed_sensor_v,in_reset"]
        for i in range(len(self.t_ms)):
            rows.append(f"{self.t_ms[i]},{self.speed_rpm[i]:.2f},{self.duty_pct[i]:.2f},"
                        f"{self.current_a[i]:.3f},{self.sensor_v[i]:.4f},{self.in_reset[i]}")
        return "\n".join(rows) + "\n"


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


@dataclass
class RunContext:
    ecu: VirtualEcu
    trace: Trace


Check = tuple[str, Callable[[RunContext], tuple[bool, str]]]


@dataclass
class Scenario:
    name: str
    title: str
    description: str
    duration_ms: int
    events: list[Event]
    checks: list[Check]
    adc_noise_v: float = 0.002


# ---- reusable checks ---------------------------------------------------------
def speed_within(start: int, end: int, tol: float = SPEED_TOL_RPM) -> Check:
    def fn(rc: RunContext):
        tr = rc.trace
        worst, at = 0.0, None
        for i in tr.window(start, end):
            err = abs(tr.speed_rpm[i] - SPEED_SETPOINT_RPM)
            if err > worst:
                worst, at = err, tr.t_ms[i]
        ok = worst <= tol
        return ok, (f"max |error| {worst:.0f} rpm" + ("" if ok else f" at t={at} ms") + f" (limit {tol:.0f})")
    return (f"speed {SPEED_SETPOINT_RPM:.0f}±{tol:.0f} rpm during {start}-{end} ms", fn)


def overshoot_below(limit_rpm: float) -> Check:
    def fn(rc: RunContext):
        peak = max(rc.trace.speed_rpm) if rc.trace.speed_rpm else 0.0
        return peak <= limit_rpm, f"peak speed {peak:.0f} rpm (limit {limit_rpm:.0f})"
    return (f"overshoot: peak ≤ {limit_rpm:.0f} rpm", fn)


def no_resets() -> Check:
    def fn(rc: RunContext):
        r = rc.ecu.resets
        return not r, "no resets" if not r else f"{len(r)} reset(s): first at {r[0].time_ms} ms ({r[0].detail})"
    return ("no ECU resets", fn)


def no_dem_failures() -> Check:
    def fn(rc: RunContext):
        f = [d for d in rc.ecu.dem_log if d.status == 1]
        return not f, "no failed events" if not f else f"event {f[0].event_id} failed at {f[0].time_ms} ms"
    return ("no diagnostic events reported failed", fn)


def no_deadline_misses() -> Check:
    def fn(rc: RunContext):
        m = rc.ecu.deadline_misses
        return not m, "none" if not m else f"{len(m)} miss(es), first {m[0].task} at {m[0].time_ms} ms"
    return ("no OS deadline misses", fn)


def dem_failed_by(event_id: int, after: int, by: int) -> Check:
    def fn(rc: RunContext):
        t = rc.ecu.dem_first_failed(event_id, after)
        ok = t is not None and t <= by
        return ok, f"reported at {t} ms" if t is not None else "never reported failed"
    return (f"DEM event {event_id} reported FAILED by t={by} ms", fn)


def duty_zero_during(start: int, end: int) -> Check:
    def fn(rc: RunContext):
        tr = rc.trace
        for i in tr.window(start, end):
            if tr.duty_pct[i] > 0.0:
                return False, f"duty {tr.duty_pct[i]:.1f} % at t={tr.t_ms[i]} ms"
        return True, "PWM held at 0 %"
    return (f"safe state: PWM 0 % during {start}-{end} ms", fn)


def watchdog_reset_between(start: int, end: int) -> Check:
    def fn(rc: RunContext):
        r = [x for x in rc.ecu.resets if x.reason == RESET_WATCHDOG]
        ok = bool(r) and start <= r[0].time_ms <= end
        return ok, f"watchdog reset at {r[0].time_ms} ms" if r else "no watchdog reset - is the watchdog serviced from the task that hung?"
    return (f"watchdog reset between {start}-{end} ms", fn)


def reset_count_at_most(n: int) -> Check:
    def fn(rc: RunContext):
        k = len(rc.ecu.resets)
        return k <= n, f"{k} reset(s)"
    return (f"at most {n} reset(s)", fn)


def dem_failed_after_reset(event_id: int, within_ms: int) -> Check:
    def fn(rc: RunContext):
        if not rc.ecu.resets:
            return False, "no reset happened"
        t0 = rc.ecu.resets[0].time_ms
        t = rc.ecu.dem_first_failed(event_id, t0)
        ok = t is not None and t - t0 <= within_ms
        return ok, f"reported {t - t0} ms after reset" if t is not None else "never reported after reset"
    return (f"DEM event {event_id} reported within {within_ms} ms of the reset", fn)


# ---- motor-control scenarios -------------------------------------------------
SCENARIOS: dict[str, Scenario] = {
    s.name: s for s in [
        Scenario("motor_nominal", "Nominal 3000 rpm",
                 "Start from standstill, hold 3000 rpm.", 3000, [],
                 [speed_within(1000, 3000), overshoot_below(3300), no_resets(), no_dem_failures(),
                  no_deadline_misses()]),
        Scenario("motor_load_step", "Load torque step",
                 "0.03 N·m load applied at 1.5 s; speed must recover.", 3000,
                 [Event(1500, "load_torque", {"nm": 0.03}, "load step 0.03 N·m")],
                 [speed_within(1000, 1500), speed_within(2000, 3000), no_resets()]),
        Scenario("motor_speed_sensor_open", "Speed sensor open circuit",
                 "At 1.5 s the speed sensor line opens (ADC reads 0 V).", 3000,
                 [Event(1500, "adc_fault", {"ch": ADC_CH_SPEED, "mode": "open"}, "speed sensor open")],
                 [speed_within(1000, 1500), dem_failed_by(DEM_SPEED_SENSOR, 1500, 1550),
                  duty_zero_during(1550, 3000), no_resets()]),
        Scenario("motor_task_hang", "10 ms task hangs",
                 "At 1.5 s task_10ms stops completing (until the next reset).", 3000,
                 [Event(1500, "task_wcet", {"task": "task_10ms", "us": HANG_US}, "task_10ms hang")],
                 [watchdog_reset_between(1500, 1700), dem_failed_after_reset(DEM_WDG_RESET, 50),
                  reset_count_at_most(1)]),
    ]
}


@dataclass
class ScenarioResult:
    scenario: str
    profile: str
    passed: bool
    checks: list[CheckResult]
    cpu_load: float
    resets: list[dict]
    dem_log: list[dict]
    deadline_misses: int
    tasks: list[dict]
    wdg_services: int
    trace: Trace
    events: list[dict]

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ScenarioResult":
        d = dict(d)
        d["checks"] = [CheckResult(**c) for c in d["checks"]]
        d["trace"] = Trace(**d["trace"])
        return cls(**d)


def run_scenario(lib_path: Path, scenario: str, profile: str, seed: int = 1) -> ScenarioResult:
    sc = SCENARIOS[scenario]
    prof = get_profile(profile)
    motor = DcMotor()
    tr = Trace()
    events = sorted(sc.events, key=lambda e: e.time_ms)
    with VirtualEcu(lib_path, prof, seed=seed) as ecu:
        for ch in range(len(ecu.adc_noise_v)):
            ecu.adc_noise_v[ch] = sc.adc_noise_v
        ei = 0
        for _ in range(sc.duration_ms):
            t_next = ecu.time_ms + 1
            while ei < len(events) and events[ei].time_ms <= t_next:
                _apply(events[ei], ecu, motor)
                ei += 1
            motor.duty = ecu.duty(0)
            motor.step(0.001)
            ecu.analog_in[ADC_CH_SPEED] = motor.speed_sensor_v()
            ecu.analog_in[ADC_CH_CURRENT] = motor.current_sensor_v()
            ecu.tick()
            tr.t_ms.append(ecu.time_ms)
            tr.speed_rpm.append(motor.rpm)
            tr.duty_pct.append(ecu.duty(0) * 100.0)
            tr.current_a.append(motor.current)
            tr.sensor_v.append(ecu.pin_volts(ADC_CH_SPEED))
            tr.in_reset.append(1 if ecu.in_reset else 0)
        rc = RunContext(ecu, tr)
        checks = []
        for name, fn in sc.checks:
            ok, detail = fn(rc)
            checks.append(CheckResult(name, bool(ok), detail))
        return ScenarioResult(
            scenario=sc.name, profile=prof.code, passed=all(c.passed for c in checks), checks=checks,
            cpu_load=ecu.cpu_load,
            resets=[{"time_ms": r.time_ms, "reason": r.reason_name, "detail": r.detail} for r in ecu.resets],
            dem_log=[{"time_ms": d.time_ms, "event_id": d.event_id, "status": d.status} for d in ecu.dem_log],
            deadline_misses=len(ecu.deadline_misses), tasks=ecu.task_stats(), wdg_services=ecu.wdg_services,
            trace=tr, events=[{"time_ms": e.time_ms, "label": e.label or e.kind} for e in events],
        )


def _apply(ev: Event, ecu: VirtualEcu, motor: DcMotor) -> None:
    a = ev.args
    if ev.kind == "adc_fault":
        ecu.adc_faults[a["ch"]] = AdcFault(a["mode"], a.get("value", 0.0))
    elif ev.kind == "clear_adc_fault":
        ecu.adc_faults.pop(a["ch"], None)
    elif ev.kind == "load_torque":
        motor.load_torque = a["nm"]
    elif ev.kind == "task_wcet":
        ecu.wcet_override[a["task"]] = a["us"]
    else:
        raise ValueError(f"unknown event kind {ev.kind}")
