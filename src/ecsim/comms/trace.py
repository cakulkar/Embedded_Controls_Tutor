"""CAN trace parsing (candump log/compact formats) and decoding with a DBC (and J1939 PGNs)."""
from __future__ import annotations

import re
from dataclasses import dataclass

from ecsim.comms.dbc import Database
from ecsim.comms.j1939 import decode_id


@dataclass
class Frame:
    t: float
    can_id: int
    extended: bool
    data: bytes


_LOG = re.compile(r"^\(\s*([\d.]+)\)\s+\S+\s+([0-9A-Fa-f]+)#([0-9A-Fa-f]*)$")          # (1.000) can0 123#DEAD
_CMP = re.compile(r"^\s*\S+\s+([0-9A-Fa-f]+)\s+\[(\d)\]\s+((?:[0-9A-Fa-f]{2}\s*)*)$")   # can0 123 [2] DE AD


def parse_candump(text: str) -> list[Frame]:
    frames = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LOG.match(line)
        if m:
            ident, data = m.group(2), bytes.fromhex(m.group(3))
            frames.append(Frame(float(m.group(1)), int(ident, 16), len(ident) > 3, data))
            continue
        m = _CMP.match(line)
        if m:
            ident = m.group(1)
            data = bytes.fromhex(m.group(3).replace(" ", ""))
            if len(data) != int(m.group(2)):
                raise ValueError(f"line {n}: DLC {m.group(2)} does not match {len(data)} data bytes")
            frames.append(Frame(float(len(frames)), int(ident, 16), len(ident) > 3, data))
            continue
        raise ValueError(f"line {n}: not a candump line: {line!r}")
    return frames


def decode_frames(frames: list[Frame], db: Database | None, j1939: bool = False) -> list[dict]:
    rows = []
    for f in frames:
        row = {"t": f.t, "id": f"{f.can_id:08X}" if f.extended else f"{f.can_id:03X}", "dlc": len(f.data),
               "data": f.data.hex(" ").upper(), "name": "", "signals": ""}
        msg = db.by_id(f.can_id) if db else None
        if msg is None and db and j1939 and f.extended:
            pgn = decode_id(f.can_id).pgn
            msg = next((m for m in db.messages if m.extended and decode_id(m.frame_id).pgn == pgn), None)
        if j1939 and f.extended:
            j = decode_id(f.can_id)
            row["name"] = f"PGN {j.pgn} SA {j.source:02X}"
        if msg:
            row["name"] = msg.name + (f" ({row['name']})" if row["name"] else "")
            try:
                vals = msg.decode(f.data)
                row["signals"] = ", ".join(f"{k}={v:g}{(' ' + s.unit) if (s := msg.signal(k)).unit else ''}" +
                                           (f" [{s.values[int(v)]}]" if s.values and int(v) in s.values else "")
                                           for k, v in vals.items())
            except Exception as exc:  # noqa: BLE001 - shown to the user per frame
                row["signals"] = f"decode error: {exc}"
        rows.append(row)
    return rows
