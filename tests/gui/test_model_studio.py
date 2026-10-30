"""Control Model Studio GUI: palette, canvas wiring (real mouse events), properties, undo,
subsystems, simulate, grade, import/export, save/open."""
import json

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ecsim.gui.main_window import MainWindow  # noqa: E402
from ecsim.gui.screens.model_studio import BlockItem, WireItem, parse_signal  # noqa: E402
from ecsim.gui.widgets import THEME  # noqa: E402
from ecsim.model import ModelError  # noqa: E402

from fakes import write_slx  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    a.setStyleSheet(THEME)
    return a


@pytest.fixture
def win(app, ctx, learner):
    w = MainWindow(ctx, learner)
    w.resize(1400, 900)
    w.show()
    yield w
    w.close()


@pytest.fixture
def studio(win):
    s = win.show_screen("Control Model Studio")
    QApplication.processEvents()
    return s


def build_pi(st):
    ids = {}
    for bid, t, pos, params in [("speed_meas_rpm", "Inport", (0, 100), None), ("sp", "Constant", (0, 0), {"value": 3000}),
                                ("e", "Sum", (180, 50), {"signs": "+-"}), ("kp", "Gain", (360, 0), {"gain": 0.00174}),
                                ("ki", "DiscreteIntegrator", (360, 120), {"gain": 0.0316, "upper": 24, "lower": 0}),
                                ("u", "Sum", (540, 50), None), ("sat", "Saturation", (700, 50), {"upper": 24, "lower": 0}),
                                ("voltage_cmd_V", "Outport", (860, 50), None)]:
        ids[bid] = st.doc.add_block(t, pos, bid=bid, params=params)
    for s, d in [("sp:1", "e:1"), ("speed_meas_rpm:1", "e:2"), ("e:1", "kp:1"), ("e:1", "ki:1"), ("kp:1", "u:1"),
                 ("ki:1", "u:2"), ("u:1", "sat:1"), ("sat:1", "voltage_cmd_V:1")]:
        assert st.connect(s, d)


def drag(view, a, b):
    pa, pb = view.mapFromScene(a), view.mapFromScene(b)
    vp = view.viewport()
    QTest.mousePress(vp, Qt.LeftButton, Qt.NoModifier, pa)
    QTest.mouseMove(vp, QPoint((pa.x() + pb.x()) // 2, (pa.y() + pb.y()) // 2))
    QTest.mouseMove(vp, pb)
    QTest.mouseRelease(vp, Qt.LeftButton, Qt.NoModifier, pb)
    QApplication.processEvents()


def test_canvas_renders_document(studio):
    build_pi(studio)
    assert len(studio.canvas.blocks) == 8 and len(studio.canvas.wires) == 8
    assert studio.check_model() and "execution order" in studio.messages.toPlainText()


def test_wire_by_mouse_drag(studio):
    studio.doc.add_block("Constant", (0, 0), bid="c")
    studio.doc.add_block("Outport", (300, 0), bid="o")
    QApplication.processEvents()
    v = studio.canvas
    v.centerOn(150, 30)
    drag(v, v.port_item("c", "out", 1).scenePos(), v.port_item("o", "in", 1).scenePos())
    assert studio.doc.model["lines"] == [{"src": "c:1", "dst": "o:1"}]
    assert len(studio.canvas.wires) == 1


def test_move_block_by_mouse_is_undoable(studio):
    studio.doc.add_block("Gain", (100, 100), bid="g")
    QApplication.processEvents()
    v = studio.canvas
    v.centerOn(200, 150)
    item = v.blocks["g"]
    drag(v, item.sceneBoundingRect().center(), item.sceneBoundingRect().center() + type(item.pos())(80, 40))
    assert studio.doc.block("g")["pos"] == [180, 140]
    studio.undo()
    assert studio.doc.block("g")["pos"] == [100, 100]


def test_properties_panel_edits_and_validates(studio):
    build_pi(studio)
    studio.canvas.blocks["kp"].setSelected(True)
    QApplication.processEvents()
    assert studio.current_block == "kp"
    studio.prop_edits["gain"].setText("0.002")
    studio.prop_edits["__id__"].setText("Kp_gain")
    studio.apply_props()
    assert studio.doc.block("Kp_gain")["params"]["gain"] == 0.002
    studio.canvas.blocks["sat"].setSelected(True)
    QApplication.processEvents()
    studio.prop_edits["lower"].setText("99")
    studio.apply_props()
    assert "lower limit is greater" in studio.messages.toPlainText()
    assert studio.doc.block("sat")["params"]["lower"] == 0


def test_delete_selection_and_undo_buttons(studio):
    build_pi(studio)
    studio.canvas.blocks["ki"].setSelected(True)
    studio.delete_selected()
    assert "ki" not in studio.canvas.blocks and studio.buttons["undo"].isEnabled()
    studio.undo()
    assert "ki" in studio.canvas.blocks
    w = next(w for w in studio.canvas.wires if w.dst.ref == "voltage_cmd_V:1")
    w.setSelected(True)
    studio.delete_selected()
    assert not any(ln["dst"] == "voltage_cmd_V:1" for ln in studio.doc.model["lines"])


def test_simulate_open_and_closed_loop(studio):
    build_pi(studio)
    studio.sim_inputs.item(0, 1).setText("const 3000")
    studio.sim_dur.setValue(0.5)
    assert studio.simulate()
    t, s = studio.last_sim
    assert len(t) == 51 and max(abs(v) for v in s["voltage_cmd_V"]) < 1e-9
    studio.sim_closed.setChecked(True)
    studio.sim_dur.setValue(3.0)
    assert studio.simulate()
    assert abs(studio.last_sim[1]["speed_rpm"][-1] - 3000) < 5
    studio.sim_inputs.item(0, 1).setText("wobble")
    studio.sim_closed.setChecked(False)
    assert not studio.simulate() and "use 'const V'" in studio.messages.toPlainText()


def test_grade_from_studio(studio, ctx, learner):
    build_pi(studio)
    studio.submit_to_exercise()
    assert "8/8 checks passed" in studio.grade_out.toPlainText()
    assert ctx.learning.state(learner, "B02-E4").passed_independently


def test_subsystem_navigation_and_edit(studio):
    build_pi(studio)
    for b in ("e", "kp", "ki", "u"):
        studio.canvas.blocks[b].setSelected(True)
    studio.group_selected()
    sid = next(b["id"] for b in studio.doc.model["blocks"] if b["type"] == "Subsystem")
    studio.open_block(sid)
    assert studio.stack and "kp" in studio.canvas.blocks and studio.buttons["up"].isEnabled()
    studio.doc.set_param("kp", "gain", 0.0)           # edit inside the subsystem
    studio.go_up()
    assert not studio.stack
    inner = studio.doc.block(sid)["params"]["model"]
    assert next(b for b in inner["blocks"] if b["id"] == "kp")["params"]["gain"] == 0.0
    assert studio.check_model()


def test_save_open_import_export(studio, tmp_path):
    build_pi(studio)
    path = tmp_path / "pi.json"
    studio.save_to(str(path))
    assert json.loads(path.read_text(encoding="utf-8"))["blocks"]
    assert "modified" not in studio.crumbs.text()
    studio.new_model()
    assert studio.doc.model["blocks"] == []
    slx = tmp_path / "pi.slx"
    write_slx(json.loads(path.read_text(encoding="utf-8")), slx)
    assert studio.import_slx(str(slx))
    assert len(studio.doc.model["blocks"]) == 8 and studio.check_model()
    bad = tmp_path / "bad.slx"
    bad.write_text("x", encoding="utf-8")
    assert not studio.import_slx(str(bad)) and "not a readable" in studio.messages.toPlainText()


def test_open_in_studio_from_academy(win, ctx):
    academy = win.show_screen("Learning Academy")
    academy.select("B02-E4")
    academy.exercise_view.studio_btn.click()
    studio = win.screens["Control Model Studio"]
    assert win.stack.currentWidget() is studio
    assert "speed_meas_rpm" in studio.canvas.blocks
    assert studio.grade_ex.currentData() == "B02-E4"


def test_parse_signal():
    assert parse_signal("3000") == {"const": 3000.0}
    assert parse_signal("step 1 0 5") == {"step": {"t": 1.0, "before": 0.0, "after": 5.0}}
    with pytest.raises(ModelError):
        parse_signal("pulse 1")


def test_chart_editing_in_studio(studio):
    from ecsim.gui.chart_editor import ChartEditorDialog
    from PySide6.QtWidgets import QDialogButtonBox
    studio.doc.add_block("Chart", (100, 100), bid="ch")
    studio.canvas.blocks["ch"].setSelected(True)
    QApplication.processEvents()
    assert "Chart" in studio.prop_title.text()
    holder = {}

    def factory(spec, parent):
        d = ChartEditorDialog(spec, parent)
        holder["d"] = d
        d.text.setPlainText('{"inputs": ["a", "b"], "outputs": ["y"], "initial": "S", "states": [{"name": "S", "during": "y = a + b"}]}')
        d._accept()
        return d
    assert studio.edit_chart("ch", factory)
    assert studio.doc.ports("ch") == (2, 1)
    d = ChartEditorDialog({"outputs": []}, None)
    assert "✗" in d.status.text() and not d.buttons.button(QDialogButtonBox.Ok).isEnabled()
    d.text.setPlainText("{bad json")
    assert "JSON" in d.status.text()
    from ecsim.model.chart import to_scxml
    good = {"inputs": [], "outputs": ["y"], "initial": "S", "states": [{"name": "S", "entry": "y = 1"}]}
    assert d.set_scxml(to_scxml(good)) and "✓" in d.status.text()
    assert len(d.view.scene().items()) > 2
