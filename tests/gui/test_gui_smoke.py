import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from ecsim.gui.main_window import PLANNED, SCREEN_ORDER, MainWindow  # noqa: E402
from ecsim.gui.screens import PlannedScreen  # noqa: E402
from ecsim.gui.widgets import THEME  # noqa: E402

from conftest import needs_cc  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    a.setStyleSheet(THEME)
    return a


@pytest.fixture
def win(app, ctx, learner):
    w = MainWindow(ctx, learner)
    yield w
    w.close()


def test_all_fourteen_screens(win):
    assert len(SCREEN_ORDER) == 14 and win.stack.count() == 14
    for name in SCREEN_ORDER:
        win.show_screen(name)
        screen = win.screens[name]
        # Unbuilt screens are honestly labelled with their phase.
        assert isinstance(screen, PlannedScreen) == (name in PLANNED)


def test_numeric_submit_updates_dashboard(win):
    academy = win.show_screen("Learning Academy")
    academy.select("A02-E1")
    ev = academy.exercise_view
    assert ev.ex.id == "A02-E1"
    ev.numeric.setText("-12288")
    ev.submit()
    assert "PASS" in ev.feedback.toPlainText()
    dash = win.show_screen("Engineering Dashboard")
    assert dash.recent.rowCount() == 1 and dash.recent.item(0, 1).text() == "A02-E1"


def test_choice_and_solution_gate(win):
    academy = win.show_screen("Learning Academy")
    academy.select("A01-E1")
    ev = academy.exercise_view
    assert ev.request_solution(confirm=False) is None          # gated, no dialog
    ev.choice_group.button(0).setChecked(True)
    ev.submit()
    assert "FAIL" in ev.feedback.toPlainText()
    assert "Option 2" in ev.request_solution()


def test_hint_button_state(win):
    academy = win.show_screen("Learning Academy")
    academy.select("A02-E2")
    ev = academy.exercise_view
    ev.next_hint()
    assert "Hint 1" in ev.hints.text() and not ev.hint_btn.isEnabled()


@needs_cc
def test_code_exercise_in_gui(win, ctx):
    academy = win.show_screen("Learning Academy")
    academy.select("A01-E3")
    ev = academy.exercise_view
    assert "TODO" in ev.code.toPlainText()
    ev.code.setPlainText(ctx.catalog.exercise("A01-E3").spec["reference"])
    ev.submit()
    assert "9/9" in ev.feedback.toPlainText()


def test_lesson_and_track(win, ctx, learner):
    academy = win.show_screen("Learning Academy")
    academy.select("B01-L1")
    academy.lesson_view.done_btn.click()
    assert "B01-L1" in ctx.learning.completed_lessons(learner)
    comp = win.show_screen("Competency & Certification")
    comp.track.setCurrentIndex(comp.track.findData("ADAS_CTL"))
    assert ctx.competency.profile(learner).track_code == "ADAS_CTL"


@needs_cc
def test_ecu_lab_screen(win, ctx):
    from ecsim.gui.screens import EcuLabScreen
    lab = win.show_screen("Virtual ECU Lab")
    assert isinstance(lab, EcuLabScreen)
    assert "TODO" in lab.code.toPlainText()                      # starter, never the reference
    assert not lab.load_last_submission()
    lab.code.setPlainText(ctx.catalog.exercise("A05-E1").spec["reference"])
    lab.profile.setCurrentIndex(lab.profile.findData("TC3XX_LIKE"))
    lab.scenario.setCurrentIndex(lab.scenario.findData("motor_task_hang"))
    lab.run()
    assert lab.summary.text().startswith("<b>PASS")
    assert lab.checks.rowCount() == 3 and "reset at 1600 ms" in lab.log.toPlainText()
    lab.code.setPlainText("void app_init(void) { int x; }")
    lab.run()
    assert "Build failed" in lab.summary.text() and "your_app.c" in lab.log.toPlainText()


def test_model_exercise_in_academy(win, ctx):
    academy = win.show_screen("Learning Academy")
    academy.select("B02-E4")
    ev = academy.exercise_view
    assert ev.slx_btn.isVisibleTo(ev) and '"format": "ecsim-model"' in ev.code.toPlainText()
    ev.submit()
    assert "FAIL" in ev.feedback.toPlainText() and "REQ-1" in ev.feedback.toPlainText()
    ev.code.setPlainText(ctx.catalog.exercise("B02-E4").spec["reference"])
    ev.submit()
    assert "8/8 checks passed" in ev.feedback.toPlainText()
    academy.select("B01-E1")
    assert not ev.slx_btn.isVisibleTo(ev)


def test_can_analyzer_screen(win):
    can = win.show_screen("CAN Network Analyzer")
    assert "3 messages" in can.db_label.text() and can.tree.topLevelItemCount() == 3
    assert can.decode() == 6
    can.filter.setText("EEC1")
    assert can.trace_table.rowCount() == 1
    assert "POSITIVE" in can.send("22 F1 90") and "ISO-TP frames" in can.uds_log.toPlainText()
    assert "serviceNotSupportedInActiveSession" in can.send("2E 01 01 09 C4")
    can.send("10 03"); can.send("27 01")
    assert can.send("27 02 $KEY") == "POSITIVE: 67 02"
    assert "unlocked" in can.state.text()
    can.wait(6000)
    assert "default" in can.state.text() and "S3 timeout" in can.uds_log.toPlainText()
    assert not can.load_dbc_text("BO_ 1 M: 1 N\n SG_ a : 4|8@1+ (1,0) [0|1] \"\" X\n", "bad.dbc")


def test_plant_simulator_screen(app, ctx, learner):
    from ecsim.gui.screens.plant_sim import PlantSimScreen
    from ecsim.plants.explorer import SCENARIOS
    s = PlantSimScreen(ctx, learner)
    for i, key in enumerate(SCENARIOS):
        s.combo.setCurrentIndex(i)
        assert set(s.spins) == {p.key for p in SCENARIOS[key].params}
        assert len(s.plot_widgets) == len(SCENARIOS[key].run().panels)
        assert "Error" not in s.summary.text()
    s.combo.setCurrentIndex(list(SCENARIOS).index("precharge"))
    s.spins["r_pre"].setValue(100); s.run()
    assert "95 % reached at 300 ms" in s.summary.text()
