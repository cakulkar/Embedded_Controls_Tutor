"""DBC (Intel/Motorola), J1939, ISO-TP, UDS server, traces, UDS script grading."""
import pytest

from ecsim.app.config import CONTENT_DIR
from ecsim.comms.dbc import DbcError, Message, Signal, parse_dbc, write_dbc
from ecsim.comms.j1939 import Dtc, decode_dm1, decode_id, encode_dtc, encode_id
from ecsim.comms.trace import decode_frames, parse_candump
from ecsim.comms.uds import IsoTpError, UdsServer, describe, isotp_frames, isotp_reassemble, training_key
from ecsim.gui.screens.can_analyzer import SAMPLE_DBC
from ecsim.learning.content import load_catalog
from ecsim.learning.graders import UdsScriptGrader
from ecsim.learning.graders.uds_script import run_script

DB = parse_dbc(SAMPLE_DBC.read_text(encoding="utf-8"))


def test_intel_signals_round_trip_including_signed():
    st = DB.by_name("MCU_Status")
    vals = {"MotorSpeed": 3000.0, "MotorMode": 2.0, "AliveCounter": 7.0, "MotorTemp": 65.0, "MotorCurrent": -12.5}
    data = st.encode(vals)
    assert data.hex() == "7017726906ff0000" and st.decode(data) == vals


def test_motorola_bit_layout():
    s = DB.by_name("VCU_Command").signal("SpeedRequest")
    assert s.bit_positions()[-1] == 7 and s.bit_positions()[:8] == list(range(8, 16))   # MSB at bit 7, LSB byte 1
    assert DB.by_name("VCU_Command").encode({"SpeedRequest": 2500, "EnableRequest": 1}).hex() == "09c4800000000000"
    odd = Signal("x", 3, 8, False, False)                       # crosses from byte 0 into byte 1
    assert odd.bit_positions() == [12, 13, 14, 15, 0, 1, 2, 3]
    assert odd.decode(bytes([0x0A, 0xB0] + [0] * 6)) == 0xAB


def test_encode_range_and_frame_length():
    st = DB.by_name("MCU_Status")
    with pytest.raises(DbcError, match="outside the encodable range"):
        st.encode({"MotorTemp": 400})
    with pytest.raises(DbcError, match="too short"):
        st.signal("MotorCurrent").decode(b"\x00\x00")


def test_dbc_write_parse_round_trip_and_errors():
    again = parse_dbc(write_dbc(DB))
    assert [(m.name, m.frame_id, m.extended, [(s.name, s.start, s.length, s.little_endian, s.signed, s.factor, s.offset, s.values)
             for s in m.signals]) for m in again.messages] == \
           [(m.name, m.frame_id, m.extended, [(s.name, s.start, s.length, s.little_endian, s.signed, s.factor, s.offset, s.values)
             for s in m.signals]) for m in DB.messages]
    bad = 'BO_ 1 M: 2 N\n SG_ a : 0|8@1+ (1,0) [0|1] "" X\n SG_ b : 4|8@1+ (1,0) [0|1] "" X\n'
    with pytest.raises(DbcError, match="overlap"):
        parse_dbc(bad)
    with pytest.raises(DbcError, match="does not fit"):
        parse_dbc('BO_ 1 M: 1 N\n SG_ a : 4|8@1+ (1,0) [0|1] "" X\n')
    with pytest.raises(DbcError, match="multiplexed"):
        parse_dbc('BO_ 1 M: 8 N\n SG_ a M : 0|8@1+ (1,0) [0|1] "" X\n')


def test_j1939_ids_and_dm1():
    j = decode_id(0x0CF00400)
    assert (j.priority, j.pgn, j.source, j.destination) == (3, 61444, 0x00, None)
    assert decode_id(0x18FEF100).pgn == 65265
    pdu1 = decode_id(0x18EA0F25)                    # request PGN to DA 0x0F
    assert pdu1.pgn == 0xEA00 and pdu1.destination == 0x0F and pdu1.source == 0x25
    assert encode_id(3, 61444, 0x00) == 0x0CF00400 and encode_id(6, 0xEA00, 0x25, 0x0F) == 0x18EA0F25
    with pytest.raises(ValueError):
        encode_id(6, 0xEA05, 0x25)
    d = Dtc(spn=520199, fmi=31, oc=126)
    lamps, dtcs = decode_dm1(bytes([0x04, 0xFF]) + encode_dtc(d) + encode_dtc(Dtc(110, 3, 1)))
    assert lamps == 0x04 and dtcs == [d, Dtc(110, 3, 1)]
    assert decode_dm1(bytes([0, 0xFF, 0, 0, 0, 0, 0xFF, 0xFF]))[1] == []


def test_isotp_round_trip_and_errors():
    payload = bytes(range(40))
    frames = isotp_frames(payload)
    assert frames[0][:2] == bytes([0x10, 40]) and [f[0] for f in frames[1:]] == [0x21, 0x22, 0x23, 0x24, 0x25]
    assert isotp_reassemble(frames) == payload
    assert isotp_reassemble(isotp_frames(b"\x22\xF1\x90")) == b"\x22\xF1\x90"
    with pytest.raises(IsoTpError, match="sequence"):
        isotp_reassemble([frames[0], frames[2]])
    with pytest.raises(IsoTpError, match="incomplete"):
        isotp_reassemble(frames[:2])


def test_uds_server_behaviour():
    s = UdsServer()
    assert s.request(b"\x2E\x01\x01\x09\xC4") == b"\x7F\x2E\x7F"           # default session
    assert s.request(b"\x10\x03")[:2] == b"\x50\x03"
    assert s.request(b"\x2E\x01\x01\x09\xC4") == b"\x7F\x2E\x33"           # locked
    seed = int.from_bytes(s.request(b"\x27\x01")[2:], "big")
    assert s.request(bytes([0x27, 0x02]) + ((training_key(seed) ^ 1).to_bytes(2, "big"))) == b"\x7F\x27\x35"
    assert s.request(b"\x27\x02\x00\x00") == b"\x7F\x27\x24"               # key without seed
    seed = int.from_bytes(s.request(b"\x27\x01")[2:], "big")
    assert s.request(bytes([0x27, 0x02]) + training_key(seed).to_bytes(2, "big")) == b"\x67\x02"
    assert s.request(b"\x2E\x01\x01\x09\xC4") == b"\x6E\x01\x01" and s.dids[0x0101] == b"\x09\xC4"
    assert s.request(b"\x2E\xF1\x90" + b"X" * 17) == b"\x7F\x2E\x31"       # VIN not writable
    assert s.request(b"\x19\x02\x08") == b"\x59\x02\xFF\x12\x34\x56\x09\x0A\x1B\x2C\x08"
    assert s.request(b"\x14\xFF\xFF\xFF") == b"\x54" and s.dtcs == []
    assert s.request(b"\x3E\x80") is None                                   # suppressed
    assert s.request(b"\x22\x12\x34") == b"\x7F\x22\x31"
    assert s.request(b"\x31\x01") == b"\x7F\x31\x11"
    s.tick(6000)
    assert s.session == 1 and not s.unlocked                                # S3 timeout
    assert "serviceNotSupportedInActiveSession" in describe(b"\x7F\x2E\x7F")


def test_security_lockout_after_three_wrong_keys():
    s = UdsServer()
    s.request(b"\x10\x03")
    codes = []
    for _ in range(3):
        s.request(b"\x27\x01")
        codes.append(s.request(b"\x27\x02\x00\x01")[2])
    assert codes == [0x35, 0x35, 0x36]
    assert s.request(b"\x27\x01") == b"\x7F\x27\x37"
    s.tick(11000)
    s.request(b"\x10\x03")
    assert s.request(b"\x27\x01")[:2] == b"\x67\x01"


def test_trace_parse_and_decode():
    text = "(0.000) can0 100#7017726906FF0000\n(0.010) can0 0CF004FE#FFFFFF8016FFFFFF\n  vcan0  200   [2]  09 C4\n"
    frames = parse_candump(text)
    rows = decode_frames(frames, DB, j1939=True)
    assert "MotorSpeed=3000 rpm" in rows[0]["signals"] and "[RUN]" in rows[0]["signals"]
    assert rows[1]["name"].startswith("EEC1") and "PGN 61444" in rows[1]["name"] and "EngineSpeed=" in rows[1]["signals"]
    assert "decode error" in rows[2]["signals"]                               # 2-byte frame for an 8-byte message
    with pytest.raises(ValueError, match="DLC"):
        parse_candump("can0 123 [3] 01 02")


def test_uds_script_exercise_quality():
    ex = load_catalog(CONTENT_DIR).exercise("I01-E7")
    g = UdsScriptGrader()
    assert g.grade(ex.spec, ex.spec["reference"]).passed
    assert not g.grade(ex.spec, ex.spec["starter"]).passed
    for m in ex.spec["mutants"]:
        assert not g.grade(ex.spec, m).passed, m
    _srv, _t, _e, err = run_script("27 02 $KEY")
    assert "before a seed" in err
    assert "Script error" in g.grade(ex.spec, "zz top").feedback
