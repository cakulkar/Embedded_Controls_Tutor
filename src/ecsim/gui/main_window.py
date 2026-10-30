from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QMainWindow, QStackedWidget, QVBoxLayout,
                               QWidget)

from ecsim import __version__
from ecsim.app.services import AppContext, bootstrap
from ecsim.gui.screens import AcademyScreen, CompetencyScreen, DashboardScreen, PlannedScreen
from ecsim.gui.widgets import THEME

# (title, phase, planned scope) for screens delivered after Phase 1.
PLANNED = {
    "Job Simulator": (14, ["Virtual company with departments, managers, customers, deadlines",
                           "Assignments such as Task #001 (3,000 rpm motor speed controller) with V-model deliverables",
                           "Injected incidents: oscillation, sensor offset, CAN loss, missed deadlines, regressions",
                           "Persona reviewers: tech lead, systems, architect, calibration, test, safety, PM, OEM"]),
    "Control Model Studio": (4, ["Drag-and-drop executable block diagrams, subsystems, sample times",
                                 "Required block library (math, control, logic, dynamic, discrete, automotive, BMS, driveline, safety)",
                                 "State machines, lookup tables, C code generation for supported blocks",
                                 "Model ↔ generated-C back-to-back comparison"]),
    "Virtual ECU Lab": (2, ["Configurable MCU: RAM/flash limits, CPU execution-time model",
                            "Task scheduler, interrupts, ADC/PWM/GPIO, watchdog, reset, bootloader",
                            "Diagnostic memory and fault injection"]),
    "Vehicle & Machine Simulator": (7, ["Engine, e-motor, inverter, battery, transmission, clutch, differential",
                                        "Vehicle dynamics, hydraulic pump/cylinder, steering, brakes, HVAC",
                                        "Radar/camera abstractions; lock-step with the virtual ECU"]),
    "CAN Network Analyzer": (6, ["Trace view, DBC decode/encode (cantools), logging & replay",
                                 "J1939, ISOBUS concepts, UDS, bus-off & timeout fault handling"]),
    "Requirements Manager": (5, ["Customer → system → functional → software requirements",
                                 "Acceptance criteria, reviews, change requests, ReqIF import/export",
                                 "Traceability matrix to implementation and tests"]),
    "Testing & Validation Center": (5, ["MIL / SIL / PIL(approx.) / software-HIL orchestration",
                                        "Unit/integration/regression, coverage, defect tracking, reports"]),
    "Calibration Studio": (12, ["Parameter identification, LUT/breakpoint design, A2L & XCP concepts",
                                "Calibration datasets and change management"]),
    "AI Engineering Mentor": (13, ["Local LLM only, retrieval over local docs with citations",
                                   "Reviews classified correct / poor design / unsafe / incorrect",
                                   "Cannot bypass the hint ladder or solution gate"]),
    "Interview Simulator": (14, ["Domain interviews with answer-dependent follow-up questions",
                                 "Rubric: depth, math, practice, communication, problem solving"]),
    "Engineering Portfolio": (15, ["Capstone deliverables and verification evidence",
                                   "Honest framing: simulated work, not commercial employment"]),
}

SCREEN_ORDER = [
    "Engineering Dashboard", "Learning Academy", "Job Simulator", "Control Model Studio", "Virtual ECU Lab",
    "Vehicle & Machine Simulator", "CAN Network Analyzer", "Requirements Manager", "Testing & Validation Center",
    "Calibration Studio", "AI Engineering Mentor", "Interview Simulator", "Competency & Certification",
    "Engineering Portfolio",
]


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext, learner_id: int) -> None:
        super().__init__()
        self.ctx, self.learner_id = ctx, learner_id
        self.setWindowTitle(f"ECSim {__version__} — Embedded Controls Engineer Job Simulator")
        self.resize(1360, 860)
        central = QWidget(); self.setCentralWidget(central)
        lay = QHBoxLayout(central); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)
        self.nav = QListWidget(); self.nav.setObjectName("nav"); self.nav.setFixedWidth(250)
        self.stack = QStackedWidget()
        lay.addWidget(self.nav); lay.addWidget(self.stack, 1)

        self.screens: dict[str, QWidget] = {}
        for name in SCREEN_ORDER:
            if name == "Engineering Dashboard":
                w = DashboardScreen(ctx, learner_id)
            elif name == "Learning Academy":
                w = AcademyScreen(ctx, learner_id, on_change=self.refresh_all)
            elif name == "Competency & Certification":
                w = CompetencyScreen(ctx, learner_id)
            else:
                phase, scope = PLANNED[name]
                w = PlannedScreen(name, phase, scope)
            self.screens[name] = w
            self.stack.addWidget(w)
            label = name if name not in PLANNED else f"{name}   · P{PLANNED[name][0]}"
            self.nav.addItem(label)
        self.nav.currentRowChanged.connect(self._go)
        self.nav.setCurrentRow(0)
        self.statusBar().showMessage(f"Offline · data in {ctx.settings.home}")

    def _go(self, row: int) -> None:
        self.stack.setCurrentIndex(row)
        w = self.stack.currentWidget()
        if hasattr(w, "refresh"):
            w.refresh()

    def show_screen(self, name: str) -> QWidget:
        self.nav.setCurrentRow(SCREEN_ORDER.index(name))
        return self.screens[name]

    def refresh_all(self) -> None:
        for name in ("Engineering Dashboard", "Competency & Certification"):
            self.screens[name].refresh()


class OnboardingDialog(QDialog):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.setWindowTitle("Welcome to ECSim")
        lay = QVBoxLayout(self)
        intro = QLabel("Create your local learner profile. Everything stays on this computer.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        form = QFormLayout()
        self.name = QLineEdit()
        self.track = QComboBox()
        self.track.addItem("Decide later", None)
        for t in ctx.catalog.tracks:
            self.track.addItem(t["title"], t["code"])
        form.addRow("Name", self.name)
        form.addRow("Career track", self.track)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._accept); bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _accept(self) -> None:
        if self.name.text().strip():
            self.accept()


def run(argv: list[str] | None = None) -> int:
    app = QApplication.instance() or QApplication(argv or sys.argv)
    app.setStyleSheet(THEME)
    ctx = bootstrap()
    learners = ctx.learning.learners()
    if learners:
        lid = learners[0]["id"]
    else:
        dlg = OnboardingDialog(ctx)
        if dlg.exec() != QDialog.Accepted:
            return 0
        lid = ctx.learning.create_learner(dlg.name.text(), dlg.track.currentData())
    win = MainWindow(ctx, lid)
    win.show()
    code = app.exec()
    ctx.close()
    return code


if __name__ == "__main__":
    raise SystemExit(run())
