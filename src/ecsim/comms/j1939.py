"""SAE J1939 identifier handling and DM1 (active DTC) decoding.

29-bit identifier: priority (3) | EDP (1) | DP (1) | PF (8) | PS (8) | SA (8).
PDU1 (PF < 240): PS is the destination address; PGN = EDP·DP·PF·00.
PDU2 (PF >= 240): PS is the group extension; PGN = EDP·DP·PF·PS.
DM1 DTC (4 bytes): SPN (19 bits: byte0 + byte1 + upper 3 bits of byte2), FMI (lower 5 bits of byte2),
CM (bit 7 of byte3), OC (lower 7 bits of byte3). (SPN conversion method CM = 0, the current standard form.)
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class J1939Id:
    priority: int
    pgn: int
    source: int
    destination: int | None      # None for PDU2 (broadcast)

    @property
    def pf(self) -> int:
        return (self.pgn >> 8) & 0xFF


def decode_id(can_id: int) -> J1939Id:
    if not 0 <= can_id < (1 << 29):
        raise ValueError("J1939 uses 29-bit identifiers")
    prio = (can_id >> 26) & 0x7
    edp_dp = (can_id >> 24) & 0x3
    pf = (can_id >> 16) & 0xFF
    ps = (can_id >> 8) & 0xFF
    sa = can_id & 0xFF
    if pf < 240:
        return J1939Id(prio, (edp_dp << 16) | (pf << 8), sa, ps)
    return J1939Id(prio, (edp_dp << 16) | (pf << 8) | ps, sa, None)


def encode_id(priority: int, pgn: int, source: int, destination: int = 0xFF) -> int:
    pf = (pgn >> 8) & 0xFF
    edp_dp = (pgn >> 16) & 0x3
    ps = destination if pf < 240 else pgn & 0xFF
    if pf < 240 and pgn & 0xFF:
        raise ValueError("PDU1 PGNs have a zero low byte (the PS field carries the destination)")
    return ((priority & 7) << 26) | (edp_dp << 24) | (pf << 16) | ((ps & 0xFF) << 8) | (source & 0xFF)


@dataclass(frozen=True)
class Dtc:
    spn: int
    fmi: int
    oc: int


def decode_dm1(data: bytes) -> tuple[int, list[Dtc]]:
    """Return (lamp status byte 0, active DTCs). An all-zero first DTC means 'no active faults'."""
    if len(data) < 6:
        raise ValueError("DM1 needs at least 6 bytes")
    lamps, dtcs = data[0], []
    for k in range(2, len(data) - 3, 4):
        b = data[k:k + 4]
        spn = b[0] | (b[1] << 8) | ((b[2] >> 5) << 16)
        fmi, oc = b[2] & 0x1F, b[3] & 0x7F
        if spn == 0 and fmi == 0 and oc == 0:
            continue
        dtcs.append(Dtc(spn, fmi, oc))
    return lamps, dtcs


def encode_dtc(d: Dtc) -> bytes:
    return bytes([d.spn & 0xFF, (d.spn >> 8) & 0xFF, (((d.spn >> 16) & 0x7) << 5) | (d.fmi & 0x1F), d.oc & 0x7F])
