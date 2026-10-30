"""DBC subset: messages, signals (Intel and Motorola byte order), value tables; encode/decode.

Supported lines: VERSION, BU_, BO_, SG_, CM_ (message/signal comments), VAL_. Multiplexed signals and
floating-point signal types are reported as unsupported. Bit numbering follows the DBC convention:
Intel (@1) start bit = LSB; Motorola (@0) start bit = MSB in "sawtooth" numbering (bit 7 of byte 0
is bit 7, bit 0 of byte 0 is bit 0, bit 7 of byte 1 is bit 15, ...).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


class DbcError(ValueError):
    pass


@dataclass
class Signal:
    name: str
    start: int
    length: int
    little_endian: bool
    signed: bool
    factor: float = 1.0
    offset: float = 0.0
    minimum: float = 0.0
    maximum: float = 0.0
    unit: str = ""
    receivers: list[str] = field(default_factory=list)
    comment: str = ""
    values: dict[int, str] = field(default_factory=dict)

    def bit_positions(self) -> list[int]:
        """Absolute bit indices (byte*8 + bit-in-byte), LSB first."""
        if self.little_endian:
            return [self.start + i for i in range(self.length)]
        pos, msb_first = self.start, []
        for _ in range(self.length):
            msb_first.append(pos)
            pos = pos + 15 if pos % 8 == 0 else pos - 1
        return list(reversed(msb_first))

    def decode_raw(self, data: bytes) -> int:
        raw = 0
        for i, b in enumerate(self.bit_positions()):
            if b // 8 >= len(data):
                raise DbcError(f"{self.name}: frame too short ({len(data)} bytes)")
            raw |= ((data[b // 8] >> (b % 8)) & 1) << i
        if self.signed and raw & (1 << (self.length - 1)):
            raw -= 1 << self.length
        return raw

    def decode(self, data: bytes) -> float:
        return self.decode_raw(data) * self.factor + self.offset

    def encode_into(self, data: bytearray, physical: float) -> None:
        raw = int(round((physical - self.offset) / self.factor))
        lo, hi = (-(1 << (self.length - 1)), (1 << (self.length - 1)) - 1) if self.signed else (0, (1 << self.length) - 1)
        if not lo <= raw <= hi:
            raise DbcError(f"{self.name}: {physical} {self.unit} is outside the encodable range")
        raw &= (1 << self.length) - 1
        for i, b in enumerate(self.bit_positions()):
            if (raw >> i) & 1:
                data[b // 8] |= 1 << (b % 8)
            else:
                data[b // 8] &= ~(1 << (b % 8)) & 0xFF


@dataclass
class Message:
    frame_id: int
    name: str
    length: int
    sender: str
    extended: bool
    signals: list[Signal] = field(default_factory=list)
    comment: str = ""

    def signal(self, name: str) -> Signal:
        for s in self.signals:
            if s.name == name:
                return s
        raise DbcError(f"message {self.name} has no signal {name}")

    def decode(self, data: bytes) -> dict[str, float]:
        return {s.name: s.decode(data) for s in self.signals}

    def encode(self, values: dict[str, float]) -> bytes:
        buf = bytearray(self.length)
        for name, v in values.items():
            self.signal(name).encode_into(buf, v)
        return bytes(buf)


@dataclass
class Database:
    messages: list[Message] = field(default_factory=list)
    nodes: list[str] = field(default_factory=list)
    version: str = ""

    def by_id(self, frame_id: int) -> Message | None:
        return next((m for m in self.messages if m.frame_id == frame_id), None)

    def by_name(self, name: str) -> Message:
        for m in self.messages:
            if m.name == name:
                return m
        raise DbcError(f"no message {name}")


_BO = re.compile(r"^BO_\s+(\d+)\s+(\w+)\s*:\s*(\d+)\s+(\w+)")
_SG = re.compile(r"^SG_\s+(\w+)\s*(\w*)\s*:\s*(\d+)\|(\d+)@([01])([+-])\s*\(\s*([^,]+),\s*([^)]+)\)\s*"
                 r"\[\s*([^|]+)\|\s*([^\]]+)\]\s*\"([^\"]*)\"\s*(.*)$")


def parse_dbc(text: str) -> Database:
    db = Database()
    cur: Message | None = None
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            cur = None if not raw.startswith((" ", "\t")) else cur
            continue
        if line.startswith("VERSION"):
            db.version = line.split('"')[1] if '"' in line else ""
        elif line.startswith("BU_"):
            db.nodes = line.split(":", 1)[1].split()
        elif line.startswith("BO_ "):
            m = _BO.match(line)
            if not m:
                raise DbcError(f"line {n}: cannot parse message: {line}")
            fid = int(m.group(1))
            ext = bool(fid & 0x80000000)
            cur = Message(fid & 0x1FFFFFFF, m.group(2), int(m.group(3)), m.group(4), ext)
            db.messages.append(cur)
        elif line.startswith("SG_ "):
            if cur is None:
                raise DbcError(f"line {n}: signal outside a message")
            m = _SG.match(line)
            if not m:
                raise DbcError(f"line {n}: cannot parse signal: {line}")
            if m.group(2):
                raise DbcError(f"line {n}: multiplexed signal {m.group(1)} is not supported")
            s = Signal(m.group(1), int(m.group(3)), int(m.group(4)), m.group(5) == "1", m.group(6) == "-",
                       float(m.group(7)), float(m.group(8)), float(m.group(9)), float(m.group(10)), m.group(11),
                       [r for r in re.split(r"[,\s]+", m.group(12).strip()) if r])
            if s.length < 1 or s.length > 64:
                raise DbcError(f"line {n}: signal length must be 1..64")
            if max(s.bit_positions()) >= cur.length * 8 or min(s.bit_positions()) < 0:
                raise DbcError(f"line {n}: signal {s.name} does not fit in {cur.length} bytes")
            cur.signals.append(s)
        elif line.startswith("CM_"):
            m = re.match(r'^CM_\s+(BO_|SG_)\s+(\d+)\s*(\w*)\s+"(.*)"\s*;', line)
            if m:
                msg = next((x for x in db.messages if x.frame_id == (int(m.group(2)) & 0x1FFFFFFF)), None)
                if msg and m.group(1) == "BO_":
                    msg.comment = m.group(4)
                elif msg:
                    msg.signal(m.group(3)).comment = m.group(4)
        elif line.startswith("VAL_ "):
            m = re.match(r"^VAL_\s+(\d+)\s+(\w+)\s+(.*);", line)
            if m:
                msg = next((x for x in db.messages if x.frame_id == (int(m.group(1)) & 0x1FFFFFFF)), None)
                if msg:
                    pairs = re.findall(r'(-?\d+)\s+"([^"]*)"', m.group(3))
                    msg.signal(m.group(2)).values = {int(k): v for k, v in pairs}
        elif line.startswith("SIG_VALTYPE_"):
            raise DbcError(f"line {n}: float signal types are not supported")
    _check_overlaps(db)
    return db


def _check_overlaps(db: Database) -> None:
    for m in db.messages:
        used: dict[int, str] = {}
        for s in m.signals:
            for b in s.bit_positions():
                if b in used:
                    raise DbcError(f"message {m.name}: signals {used[b]} and {s.name} overlap at bit {b}")
                used[b] = s.name


def write_dbc(db: Database) -> str:
    def num(v: float) -> str:
        return repr(v).rstrip("0").rstrip(".") if "." in repr(v) else repr(v)
    out = [f'VERSION "{db.version}"', "", "NS_ :", "", "BS_:", "", "BU_: " + " ".join(db.nodes), ""]
    for m in db.messages:
        fid = m.frame_id | (0x80000000 if m.extended else 0)
        out.append(f"BO_ {fid} {m.name}: {m.length} {m.sender}")
        for s in m.signals:
            out.append(f" SG_ {s.name} : {s.start}|{s.length}@{1 if s.little_endian else 0}{'-' if s.signed else '+'} "
                       f"({num(s.factor)},{num(s.offset)}) [{num(s.minimum)}|{num(s.maximum)}] \"{s.unit}\" "
                       + (",".join(s.receivers) or "Vector__XXX"))
        out.append("")
    for m in db.messages:
        fid = m.frame_id | (0x80000000 if m.extended else 0)
        if m.comment:
            out.append(f'CM_ BO_ {fid} "{m.comment}";')
        for s in m.signals:
            if s.comment:
                out.append(f'CM_ SG_ {fid} {s.name} "{s.comment}";')
            if s.values:
                out.append(f"VAL_ {fid} {s.name} " + " ".join(f'{k} "{v}"' for k, v in sorted(s.values.items())) + " ;")
    return "\n".join(out) + "\n"
