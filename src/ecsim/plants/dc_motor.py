"""Permanent-magnet DC motor driven by a PWM half-bridge, with speed and current sensors.

    J dω/dt = Kt·i − b·ω − T_load,      i = (V − Ke·ω) / R      (armature L neglected)

With L neglected the speed ODE is linear and first order, so each step uses the
exact solution (no integration error). Mechanical time constant:
    τ = J·R / (Kt·Ke + R·b)
"""
from __future__ import annotations

import math
from dataclasses import dataclass

RPM_PER_RADS = 60.0 / (2.0 * math.pi)


@dataclass(frozen=True)
class DcMotorParams:
    v_bat: float = 24.0        # V, supply at 100 % duty
    r: float = 0.5             # ohm
    kt: float = 0.03           # N·m/A   (= Ke in SI units)
    ke: float = 0.03           # V/(rad/s)
    j: float = 1.0e-4          # kg·m²
    b: float = 1.0e-5          # N·m/(rad/s)
    # Speed sensor: 0.5 V at 0 rpm, 4.5 V at 6000 rpm (linear).
    speed_v0: float = 0.5
    speed_v_per_rpm: float = 4.0 / 6000.0
    # Current sensor: 2.5 V at 0 A, 50 mV/A.
    cur_v0: float = 2.5
    cur_v_per_a: float = 0.05

    @property
    def tau(self) -> float:
        return self.j * self.r / (self.kt * self.ke + self.r * self.b)

    @property
    def gain_rpm_per_volt(self) -> float:
        return self.kt / (self.r * self.b + self.kt * self.ke) * RPM_PER_RADS


class DcMotor:
    def __init__(self, params: DcMotorParams | None = None) -> None:
        self.p = params or DcMotorParams()
        self.omega = 0.0          # rad/s
        self.duty = 0.0           # 0..1
        self.load_torque = 0.0    # N·m

    @property
    def voltage(self) -> float:
        return max(0.0, min(1.0, self.duty)) * self.p.v_bat

    @property
    def current(self) -> float:
        return (self.voltage - self.p.ke * self.omega) / self.p.r

    @property
    def rpm(self) -> float:
        return self.omega * RPM_PER_RADS

    def step(self, dt: float) -> None:
        p = self.p
        a = -(p.kt * p.ke / p.r + p.b) / p.j
        c = (p.kt * self.voltage / p.r - self.load_torque) / p.j
        e = math.exp(a * dt)
        self.omega = self.omega * e + (c / a) * (e - 1.0)

    def speed_sensor_v(self) -> float:
        return min(5.0, max(0.0, self.p.speed_v0 + self.p.speed_v_per_rpm * self.rpm))

    def current_sensor_v(self) -> float:
        return min(5.0, max(0.0, self.p.cur_v0 + self.p.cur_v_per_a * self.current))
