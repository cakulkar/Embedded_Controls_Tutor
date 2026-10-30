"""Grade a UDS request sequence against a simulated ECU: goals on the final ECU state + transcript.

Script lines: hex request bytes ("22 F1 90"), "wait <ms>" (advances time; S3 timeout applies), comments with #.
"$KEY" in a request is replaced by the training key for the seed from the most recent 67 01 response.
"""
from __future__ import annotations

from typing import Any

from ecsim.comms.uds import UdsServer, describe, parse_hex, training_key
from ecsim.learning.graders.base import GradeResult

MAX_LINES = 60


def run_script(script: str, server: UdsServer | None = None) -> tuple[UdsServer, list[str], list[tuple[bytes, bytes | None]], str | None]:
    srv = server or UdsServer()
    transcript, exchanges, seed = [], [], None
    lines = [ln.split("#", 1)[0].strip() for ln in script.splitlines()]
    lines = [ln for ln in lines if ln]
    if len(lines) > MAX_LINES:
        return srv, transcript, exchanges, f"too many lines (max {MAX_LINES})"
    for n, ln in enumerate(lines, 1):
        if ln.lower().startswith("wait"):
            try:
                ms = int(ln.split()[1])
            except (IndexError, ValueError):
                return srv, transcript, exchanges, f"line {n}: use 'wait <milliseconds>'"
            srv.tick(ms)
            transcript.append(f"… wait {ms} ms (session now {srv.session:02X})")
            continue
        if "$KEY" in ln.upper():
            if seed is None:
                return srv, transcript, exchanges, f"line {n}: $KEY used before a seed was received (send 27 01 first)"
            k = training_key(seed)
            ln = ln.upper().replace("$KEY", f"{k >> 8:02X} {k & 0xFF:02X}")
        try:
            req = parse_hex(ln)
        except ValueError as exc:
            return srv, transcript, exchanges, f"line {n}: {exc}"
        srv.tick(10)
        resp = srv.request(req)
        exchanges.append((req, resp))
        if resp and resp[:2] == b"\x67\x01" and len(resp) == 4:
            seed = (resp[2] << 8) | resp[3]
        transcript.append(f"→ {req.hex(' ').upper():<24} ← {describe(resp)}")
    return srv, transcript, exchanges, None


class UdsScriptGrader:
    def grade(self, spec: dict[str, Any], submission: str) -> GradeResult:
        srv, transcript, ex, err = run_script(submission)
        if err:
            return GradeResult(False, 0.0, f"Script error: {err}\n\n" + "\n".join(transcript))
        results, lines = [], []
        negatives = sum(1 for _q, r in ex if r and r[:1] == b"\x7f")
        for g in spec["goals"]:
            k = g["kind"]
            if k == "read_did":
                did = int(g["did"])
                ok = any(r and r[:1] == b"\x62" and q[1:3] == bytes([did >> 8, did & 0xFF]) for q, r in ex)
                text = f"read DID 0x{did:04X}"
            elif k == "did_value":
                did = int(g["did"])
                ok = srv.dids.get(did, b"").hex().upper() == g["hex"].upper()
                text = f"DID 0x{did:04X} = {g['hex'].upper()} (now {srv.dids.get(did, b'').hex().upper()})"
            elif k == "no_dtcs":
                ok, text = not srv.dtcs, f"DTC memory empty (now {len(srv.dtcs)} DTC(s))"
            elif k == "final_session":
                ok = srv.session == int(g["session"])
                text = f"final session {int(g['session']):02X} (now {srv.session:02X})"
            elif k == "max_negative":
                ok, text = negatives <= int(g["n"]), f"at most {g['n']} negative response(s) (got {negatives})"
            else:
                ok, text = False, f"unknown goal {k}"
            results.append(ok)
            lines.append(f"    {'ok ' if ok else 'FAIL'} {g.get('name', text)}" + (f": {text}" if g.get("name") else ""))
        passed = all(results)
        fb = f"{sum(results)}/{len(results)} goals met.\n" + "\n".join(lines) + "\n\nTranscript:\n" + "\n".join(transcript)
        return GradeResult(passed, sum(results) / len(results), fb)
