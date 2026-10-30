"""Lithium-ion cell: first-order Thevenin equivalent circuit + lumped thermal model.

    V = OCV(SOC) + I·R0 + V1,     dV1/dt = I/C1 − V1/(R1·C1)          (I > 0 = charging)
    dSOC/dt = I / (3600·Q)
    m·cp·dT/dt = I²·(R0 + R1·…) − h·(T − T_amb)   (Joule heat from R0 and R1 branch)

Parameters are representative of a 50 Ah NMC automotive cell, not a specific product.
The RC branch is integrated exactly per step (constant current within a step).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

OCV_SOC = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
OCV_V = [3.00, 3.45, 3.55, 3.62, 3.67, 3.73, 3.82, 3.92, 4.02, 4.10, 4.20]


def ocv(soc: float) -> float:
    s = min(max(soc, 0.0), 1.0)
    for i in range(len(OCV_SOC) - 1):
        if s <= OCV_SOC[i + 1]:
            f = (s - OCV_SOC[i]) / (OCV_SOC[i + 1] - OCV_SOC[i])
            return OCV_V[i] + f * (OCV_V[i + 1] - OCV_V[i])
    return OCV_V[-1]


@dataclass
class CellParams:
    capacity_ah: float = 50.0
    r0: float = 0.002
    r1: float = 0.0015
    c1: float = 20000.0
    mass_kg: float = 0.9
    cp: float = 1000.0
    h_w_per_k: float = 0.5
    t_amb: float = 25.0


@dataclass
class Cell:
    p: CellParams = field(default_factory=CellParams)
    soc: float = 0.8
    v1: float = 0.0
    temp: float = 25.0

    def terminal_voltage(self, current: float) -> float:
        return ocv(self.soc) + current * self.p.r0 + self.v1

    def step(self, current: float, dt: float) -> None:
        p = self.p
        tau = p.r1 * p.c1
        e = math.exp(-dt / tau)
        self.v1 = self.v1 * e + current * p.r1 * (1 - e)
        self.soc = min(max(self.soc + current * dt / (3600.0 * p.capacity_ah), 0.0), 1.0)
        heat = current * current * p.r0 + (self.v1 * self.v1) / p.r1
        self.temp += dt * (heat - p.h_w_per_k * (self.temp - p.t_amb)) / (p.mass_kg * p.cp)


@dataclass
class PrechargeCircuit:
    """HV DC link: pack voltage → precharge resistor → link capacitor; main contactor shorts the resistor."""
    v_pack: float = 400.0
    r_pre: float = 50.0
    c_link: float = 0.001
    v_link: float = 0.0
    resistor_open: bool = False

    def step(self, k_precharge: bool, k_main: bool, dt: float) -> None:
        if k_main:
            self.v_link = self.v_pack                      # main contactor: link follows the pack
        elif k_precharge and not self.resistor_open:
            tau = self.r_pre * self.c_link
            self.v_link = self.v_pack + (self.v_link - self.v_pack) * math.exp(-dt / tau)
        else:
            self.v_link *= math.exp(-dt / 5.0)              # slow bleed through the discharge resistor
