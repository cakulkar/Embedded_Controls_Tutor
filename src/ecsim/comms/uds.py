"""ISO-TP (ISO 15765-2) framing and a UDS (ISO 14229-1) server simulator.

The server models a small ECU: sessions (default 0x01, programming 0x02, extended 0x03),
SecurityAccess level 1 (seed/key; the key algorithm is ECSim's training algorithm, documented
below, not any OEM's), ReadDataByIdentifier, WriteDataByIdentifier (extended session +
unlocked), ReadDTCInformation (0x02 by status mask), ClearDiagnosticInformation, TesterPresent,
ECUReset, and the S3 session timeout. Negative responses use the standard NRC values.

Training key algorithm: key = (seed XOR 0x5A3C) rotated left by 3 bits (16-bit).
"""
from __future__ import annotations

from dataclasses import dataclass, field

NRC = {0x10: "generalReject", 0x11: "serviceNotSupported", 0x12: "subFunctionNotSupported",
       0x13: "incorrectMessageLengthOrInvalidFormat", 0x22: "conditionsNotCorrect", 0x24: "requestSequenceError",
       0x31: "requestOutOfRange", 0x33: "securityAccessDenied", 0x35: "invalidKey", 0x36: "exceededNumberOfAttempts",
       0x37: "requiredTimeDelayNotExpired", 0x7E: "subFunctionNotSupportedInActiveSession",
       0x7F: "serviceNotSupportedInActiveSession"}
SESSIONS = {0x01: "default", 0x02: "programming", 0x03: "extended"}
S3_MS = 5000


def training_key(seed: int) -> int:
    v = (seed ^ 0x5A3C) & 0xFFFF
    return ((v << 3) | (v >> 13)) & 0xFFFF


# ---- ISO-TP -----------------------------------------------------------------------------
def isotp_frames(payload: bytes, fc_block_size: int = 0) -> list[bytes]:
    """Segment a payload into 8-byte CAN frames (classic CAN, no padding byte semantics beyond 0xAA fill)."""
    if len(payload) > 4095:
        raise ValueError("ISO-TP (classic) payload limited to 4095 bytes")
    if len(payload) <= 7:
        return [bytes([len(payload)]) + payload + bytes([0xAA] * (7 - len(payload)))]
    frames = [bytes([0x10 | (len(payload) >> 8), len(payload) & 0xFF]) + payload[:6]]
    rest, sn = payload[6:], 1
    while rest:
        chunk, rest = rest[:7], rest[7:]
        frames.append(bytes([0x20 | (sn & 0xF)]) + chunk + bytes([0xAA] * (7 - len(chunk))))
        sn += 1
    return frames


class IsoTpError(ValueError):
    pass


def isotp_reassemble(frames: list[bytes]) -> bytes:
    if not frames:
        raise IsoTpError("no frames")
    pci = frames[0][0] >> 4
    if pci == 0:
        n = frames[0][0] & 0xF
        if not 1 <= n <= 7:
            raise IsoTpError("single frame length must be 1..7")
        return bytes(frames[0][1:1 + n])
    if pci != 1:
        raise IsoTpError("first frame must be a single frame or a first frame")
    total = ((frames[0][0] & 0xF) << 8) | frames[0][1]
    data = bytearray(frames[0][2:8])
    expect = 1
    for f in frames[1:]:
        if f[0] >> 4 == 3:
            continue                                    # flow control frames in the trace
        if f[0] >> 4 != 2:
            raise IsoTpError("expected consecutive frame")
        if (f[0] & 0xF) != (expect & 0xF):
            raise IsoTpError(f"sequence number error: expected {expect & 0xF}, got {f[0] & 0xF}")
        data += f[1:8]
        expect += 1
    if len(data) < total:
        raise IsoTpError(f"incomplete message: {len(data)} of {total} bytes")
    return bytes(data[:total])


# ---- UDS server -------------------------------------------------------------------------
@dataclass
class EcuDtc:
    code: int              # 3-byte DTC
    status: int            # ISO 14229 status byte (bit0 testFailed, bit3 confirmedDTC, ...)


@dataclass
class UdsServer:
    dids: dict[int, bytes] = field(default_factory=lambda: {
        0xF190: b"WECSIM0000TRAIN01",            # VIN (17 chars)
        0xF187: b"ECSIM-MCU-01",                 # spare part number
        0xF18C: b"SN000123",                     # ECU serial number
        0x0101: bytes([0x0B, 0xB8]),             # speed setpoint, rpm (3000), writable
        0x0102: bytes([0x00]),                   # PWM frequency index, writable
    })
    writable: set[int] = field(default_factory=lambda: {0x0101, 0x0102})
    dtcs: list[EcuDtc] = field(default_factory=lambda: [EcuDtc(0x123456, 0x09), EcuDtc(0x0A1B2C, 0x08)])
    session: int = 0x01
    unlocked: bool = False
    seed: int | None = None
    failed_attempts: int = 0
    locked_until_ms: int = 0
    now_ms: int = 0
    last_request_ms: int = 0
    seed_counter: int = 0x1234
    log: list[tuple[bytes, bytes]] = field(default_factory=list)

    def tick(self, ms: int) -> None:
        self.now_ms += ms
        if self.session != 0x01 and self.now_ms - self.last_request_ms > S3_MS:
            self._to_default()

    def _to_default(self) -> None:
        self.session, self.unlocked, self.seed = 0x01, False, None

    @staticmethod
    def _neg(sid: int, nrc: int) -> bytes:
        return bytes([0x7F, sid, nrc])

    def request(self, req: bytes) -> bytes | None:
        """Process one request; returns the response (None when a positive response is suppressed)."""
        resp = self._handle(bytes(req))
        self.last_request_ms = self.now_ms
        self.log.append((bytes(req), resp or b""))
        return resp

    def _handle(self, req: bytes) -> bytes | None:
        if not req:
            return None
        sid = req[0]
        h = {0x10: self._session, 0x11: self._reset, 0x14: self._clear, 0x19: self._read_dtc, 0x22: self._read,
             0x27: self._security, 0x2E: self._write, 0x3E: self._tester}.get(sid)
        if h is None:
            return self._neg(sid, 0x11)
        return h(req)

    def _suppress(self, req: bytes, resp: bytes) -> bytes | None:
        return None if len(req) > 1 and req[1] & 0x80 else resp

    def _session(self, req):
        if len(req) != 2:
            return self._neg(0x10, 0x13)
        sub = req[1] & 0x7F
        if sub not in SESSIONS:
            return self._neg(0x10, 0x12)
        if sub == 0x02 and self.session != 0x03:
            return self._neg(0x10, 0x22)          # programming only from extended (typical OEM rule)
        if sub != self.session:
            self.unlocked, self.seed = False, None
        self.session = sub
        return self._suppress(req, bytes([0x50, sub, 0x00, 0x32, 0x01, 0xF4]))   # P2 50 ms, P2* 5000 ms

    def _reset(self, req):
        if len(req) != 2:
            return self._neg(0x11, 0x13)
        if req[1] & 0x7F not in (0x01, 0x03):
            return self._neg(0x11, 0x12)
        self._to_default()
        return self._suppress(req, bytes([0x51, req[1] & 0x7F]))

    def _tester(self, req):
        if len(req) != 2:
            return self._neg(0x3E, 0x13)
        if req[1] & 0x7F != 0x00:
            return self._neg(0x3E, 0x12)
        return self._suppress(req, bytes([0x7E, 0x00]))

    def _read(self, req):
        if len(req) < 3 or (len(req) - 1) % 2:
            return self._neg(0x22, 0x13)
        out = bytearray([0x62])
        for k in range(1, len(req), 2):
            did = (req[k] << 8) | req[k + 1]
            if did not in self.dids:
                return self._neg(0x22, 0x31)
            out += bytes([req[k], req[k + 1]]) + self.dids[did]
        return bytes(out)

    def _write(self, req):
        if len(req) < 4:
            return self._neg(0x2E, 0x13)
        did = (req[1] << 8) | req[2]
        if did not in self.dids or did not in self.writable:
            return self._neg(0x2E, 0x31)
        if self.session != 0x03:
            return self._neg(0x2E, 0x7F)
        if not self.unlocked:
            return self._neg(0x2E, 0x33)
        if len(req) - 3 != len(self.dids[did]):
            return self._neg(0x2E, 0x13)
        self.dids[did] = bytes(req[3:])
        return bytes([0x6E, req[1], req[2]])

    def _security(self, req):
        if len(req) < 2:
            return self._neg(0x27, 0x13)
        sub = req[1] & 0x7F
        if self.session == 0x01:
            return self._neg(0x27, 0x7F)
        if sub == 0x01:
            if len(req) != 2:
                return self._neg(0x27, 0x13)
            if self.now_ms < self.locked_until_ms:
                return self._neg(0x27, 0x37)
            if self.unlocked:
                return bytes([0x67, 0x01, 0x00, 0x00])        # already unlocked: zero seed
            self.seed_counter = (self.seed_counter * 75 + 74) % 65537 & 0xFFFF or 0x1234
            self.seed = self.seed_counter
            return bytes([0x67, 0x01, self.seed >> 8, self.seed & 0xFF])
        if sub == 0x02:
            if len(req) != 4:
                return self._neg(0x27, 0x13)
            if self.seed is None:
                return self._neg(0x27, 0x24)
            key = (req[2] << 8) | req[3]
            if key != training_key(self.seed):
                self.failed_attempts += 1
                self.seed = None
                if self.failed_attempts >= 3:
                    self.failed_attempts = 0
                    self.locked_until_ms = self.now_ms + 10000
                    return self._neg(0x27, 0x36)
                return self._neg(0x27, 0x35)
            self.unlocked, self.seed, self.failed_attempts = True, None, 0
            return bytes([0x67, 0x02])
        return self._neg(0x27, 0x12)

    def _read_dtc(self, req):
        if len(req) < 2:
            return self._neg(0x19, 0x13)
        sub = req[1] & 0x7F
        if sub == 0x01:
            if len(req) != 3:
                return self._neg(0x19, 0x13)
            n = sum(1 for d in self.dtcs if d.status & req[2])
            return bytes([0x59, 0x01, 0xFF, 0x01, n >> 8, n & 0xFF])
        if sub == 0x02:
            if len(req) != 3:
                return self._neg(0x19, 0x13)
            out = bytearray([0x59, 0x02, 0xFF])
            for d in self.dtcs:
                if d.status & req[2]:
                    out += bytes([(d.code >> 16) & 0xFF, (d.code >> 8) & 0xFF, d.code & 0xFF, d.status])
            return bytes(out)
        return self._neg(0x19, 0x12)

    def _clear(self, req):
        if len(req) != 4:
            return self._neg(0x14, 0x13)
        group = (req[1] << 16) | (req[2] << 8) | req[3]
        if group == 0xFFFFFF:
            self.dtcs = []
        else:
            before = len(self.dtcs)
            self.dtcs = [d for d in self.dtcs if d.code != group]
            if len(self.dtcs) == before:
                return self._neg(0x14, 0x31)
        return bytes([0x54])


def describe(resp: bytes | None) -> str:
    if resp is None:
        return "(no response: positive response suppressed)"
    if resp[:1] == b"\x7f" and len(resp) >= 3:
        return f"NEGATIVE: service 0x{resp[1]:02X}, NRC 0x{resp[2]:02X} {NRC.get(resp[2], 'unknown')}"
    return "POSITIVE: " + resp.hex(" ").upper()


def parse_hex(text: str) -> bytes:
    t = text.replace("0x", "").replace(",", " ").strip()
    try:
        return bytes.fromhex(t)
    except ValueError:
        raise ValueError(f"not a hex byte string: {text!r} (use e.g. '22 F1 90')") from None
