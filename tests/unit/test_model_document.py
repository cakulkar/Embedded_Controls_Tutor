"""Model document (editor backend): edits, undo/redo, subsystems, validation."""
import json

import pytest

from ecsim.model import ModelError, Simulator
from ecsim.model.document import PALETTE, ModelDocument, coerce_param
from ecsim.model.blocks import LIB


def pi_doc() -> ModelDocument:
    d = ModelDocument()
    d.add_block("Inport", bid="speed")
    d.add_block("Constant", bid="sp", params={"value": 3000})
    d.add_block("Sum", bid="err", params={"signs": "+-"})
    d.add_block("Gain", bid="kp", params={"gain": 0.001})
    d.add_block("Outport", bid="cmd")
    for s, t in [("sp:1", "err:1"), ("speed:1", "err:2"), ("err:1", "kp:1"), ("kp:1", "cmd:1")]:
        d.connect(s, t)
    return d


def test_palette_covers_every_block_type():
    listed = {t for ts in PALETTE.values() for t in ts}
    assert listed == set(LIB)


def test_build_compile_simulate():
    d = pi_doc()
    c = d.compile()
    assert c.inport_names == ["speed"] and c.outport_names == ["cmd"]
    assert Simulator(c).step([2000]) == pytest.approx([1.0])


def test_undo_redo_full_history():
    d = pi_doc()
    snapshots = []
    while d.can_undo:
        snapshots.append(json.dumps(d.model))
        d.undo()
    assert d.model["blocks"] == [] and not d.can_undo
    while d.can_redo:
        d.redo()
    assert json.dumps(d.model) == snapshots[0]
    d.undo()
    d.add_block("Gain")                  # a new edit clears the redo stack
    assert not d.can_redo


def test_connection_rules():
    d = pi_doc()
    with pytest.raises(ModelError, match="already connected"):
        d.connect("speed:1", "err:1")
    with pytest.raises(ModelError, match="no input port 3"):
        d.connect("speed:1", "err:3")
    with pytest.raises(ModelError, match="no output port 2"):
        d.connect("kp:2", "cmd:1")
    d.disconnect("cmd:1")
    with pytest.raises(ModelError, match="nothing connected"):
        d.disconnect("cmd:1")


def test_param_change_prunes_dangling_lines_in_one_undo_step():
    d = pi_doc()
    n = len(d.model["lines"])
    d.set_param("err", "signs", "+")
    assert len(d.model["lines"]) == n - 1
    d.undo()
    assert len(d.model["lines"]) == n and d.block("err")["params"]["signs"] == "+-"


def test_invalid_params_are_rejected_before_changing_anything():
    d = pi_doc()
    before = json.dumps(d.model)
    with pytest.raises(ModelError):
        d.set_param("err", "signs", "+x")
    with pytest.raises(ModelError):
        d.add_block("Saturation", params={"upper": -1, "lower": 1})
    assert json.dumps(d.model) == before


def test_remove_renumbers_ports_and_drops_lines():
    d = ModelDocument()
    a, b, c = d.add_block("Inport"), d.add_block("Inport"), d.add_block("Inport")
    assert [d.block(x)["params"]["port"] for x in (a, b, c)] == [1, 2, 3]
    d.remove_block(b)
    assert [d.block(x)["params"]["port"] for x in (a, c)] == [1, 2]


def test_port_reorder_swaps():
    d = ModelDocument()
    a, b = d.add_block("Inport"), d.add_block("Inport")
    d.set_param(b, "port", 1)
    assert d.block(a)["params"]["port"] == 2 and d.block(b)["params"]["port"] == 1
    with pytest.raises(ModelError, match="between 1 and 2"):
        d.set_param(a, "port", 5)


def test_rename_updates_lines_and_port_names():
    d = pi_doc()
    d.rename_block("speed", "speed_meas_rpm")
    assert d.compile().inport_names == ["speed_meas_rpm"]
    with pytest.raises(ModelError, match="already exists"):
        d.rename_block("kp", "err")
    with pytest.raises(ModelError, match="invalid block name"):
        d.rename_block("kp", "k p")


def test_group_into_subsystem_preserves_behaviour():
    d = pi_doc()
    before = Simulator(d.compile()).step([2500])
    sid = d.group_into_subsystem(["err", "kp", "sp"])
    c = d.compile()
    assert any(k.startswith(f"{sid}/") for k in c.blocks)
    assert Simulator(c).step([2500]) == pytest.approx(before)
    d.undo()
    assert {"err", "kp", "sp"} <= {b["id"] for b in d.model["blocks"]}
    with pytest.raises(ModelError, match="cannot be grouped"):
        d.group_into_subsystem(["speed"])


def test_subsystem_editing_round_trip():
    d = ModelDocument()
    d.add_block("Inport", bid="u")
    s = d.add_block("Subsystem")
    d.add_block("Outport", bid="y")
    d.connect("u:1", f"{s}:1"); d.connect(f"{s}:1", "y:1")
    inner = ModelDocument.from_json(json.dumps(d.block(s)["params"]["model"]))
    inner.disconnect("Out1:1")
    inner.add_block("Gain", bid="g", params={"gain": 4})
    inner.connect("In1:1", "g:1"); inner.connect("g:1", "Out1:1")
    d.set_subsystem_model(s, inner.model)
    assert Simulator(d.compile()).step([2]) == [8]


def test_step_and_sample_time_validation():
    d = pi_doc()
    with pytest.raises(ModelError):
        d.set_step(0)
    with pytest.raises(ModelError):
        d.set_sample_time("kp", 0)
    d.set_sample_time("kp", 0.02)
    assert d.compile().blocks["kp"].ts == 0.02


@pytest.mark.parametrize("t,k,text,val", [("Gain", "gain", "2.5", 2.5), ("Sum", "signs", " +- ", "+-"),
                                          ("Lookup1D", "table", "[0, 1, 4]", [0.0, 1.0, 4.0]),
                                          ("LogicalOperator", "inputs", "3", 3)])
def test_coerce_param(t, k, text, val):
    assert coerce_param(t, k, text) == val


def test_coerce_param_errors():
    with pytest.raises(ModelError, match="number expected"):
        coerce_param("Gain", "gain", "abc")
    with pytest.raises(ModelError, match="list"):
        coerce_param("Lookup1D", "table", "[a]")


def test_document_json_round_trip_adds_layout():
    d = pi_doc()
    d2 = ModelDocument.from_json(d.to_json())
    assert d2.model == d.model
    raw = json.loads(d.to_json())
    for b in raw["blocks"]:
        b.pop("pos")
    assert all("pos" in b for b in ModelDocument.from_json(json.dumps(raw)).model["blocks"])
