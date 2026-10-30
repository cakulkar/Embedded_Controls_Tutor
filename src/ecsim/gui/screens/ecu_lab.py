from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QPlainTextEdit,
                               QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ecsim.app.services import AppContext
from ecsim.ecu.profiles import PROFILES
from ecsim.ecu.scenarios import SCENARIOS, SPEED_SETPOINT_RPM, SPEED_TOL_RPM
from ecsim.gui.plot import Marker, Series, SignalPlot
from ecsim.gui.widgets import CodeEditor, card, heading, mono_font

PRACTICAL = "A05-E1"


class EcuLabScreen(QWidget):
    """Develop and debug firmware on the virtual ECU. Runs here are not graded."""

    def __init__(self, ctx: AppContext, learner_id: int) -> None:
        super().__init__()
        self.ctx, self.learner_id = ctx, learner_id
        self.last = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.addWidget(heading("Virtual ECU Lab"))
        note = QLabel("Software-in-the-loop, simulated HIL: your C application runs against a simulated motor on a "
                      "modelled target. It is not equivalent to physical HIL (dSPACE/NI). Lab runs are logged but "
                      "not graded; submit the exercise in the Learning Academy for evidence.")
        note.setObjectName("banner"); note.setWordWrap(True)
        lay.addWidget(note)

        bar = QHBoxLayout()
        self.profile = QComboBox()
        for p in PROFILES.values():
            self.profile.addItem(p.title, p.code)
        self.scenario = QComboBox()
        for s in SCENARIOS.values():
            self.scenario.addItem(f"{s.title} ({s.name})", s.name)
        self.load_btn = QPushButton("Load my last submission"); self.load_btn.setObjectName("secondary")
        self.open_btn = QPushButton("Open .c file…"); self.open_btn.setObjectName("secondary")
        self.run_btn = QPushButton("Build && Run")
        self.csv_btn = QPushButton("Export trace CSV"); self.csv_btn.setObjectName("secondary")
        self.csv_btn.setEnabled(False)
        for w in (QLabel("Target"), self.profile, QLabel("Scenario"), self.scenario):
            bar.addWidget(w)
        bar.addStretch(1)
        for w in (self.load_btn, self.open_btn, self.csv_btn, self.run_btn):
            bar.addWidget(w)
        lay.addLayout(bar)
        self.profile_info = QLabel(); self.profile_info.setObjectName("muted"); self.profile_info.setWordWrap(True)
        lay.addWidget(self.profile_info)

        split = QSplitter(Qt.Horizontal)
        self.code = CodeEditor()
        split.addWidget(self.code)
        right = QWidget(); rl = QVBoxLayout(right); rl.setContentsMargins(0, 0, 0, 0)
        self.summary = QLabel("Build and run a scenario to see results."); self.summary.setWordWrap(True)
        self.speed_plot = SignalPlot("Motor speed", "rpm")
        self.duty_plot = SignalPlot("PWM duty", "%")
        self.checks = QTableWidget(0, 2)
        self.checks.setHorizontalHeaderLabels(["Check", "Result"])
        self.checks.verticalHeader().setVisible(False)
        self.checks.setEditTriggers(QTableWidget.NoEditTriggers)
        self.checks.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setFont(mono_font())
        self.log.setMaximumHeight(140)
        rl.addWidget(self.summary)
        rl.addWidget(self.speed_plot, 2)
        rl.addWidget(self.duty_plot, 1)
        rl.addWidget(card(heading("Checks", "h2"), self.checks), 1)
        rl.addWidget(self.log)
        split.addWidget(right)
        split.setSizes([520, 760])
        lay.addWidget(split, 1)

        self.profile.currentIndexChanged.connect(self._profile_changed)
        self.load_btn.clicked.connect(self.load_last_submission)
        self.open_btn.clicked.connect(self._open_file)
        self.run_btn.clicked.connect(self.run)
        self.csv_btn.clicked.connect(self._export_csv)
        self._profile_changed()
        self.code.setPlainText(ctx.catalog.exercise(PRACTICAL).spec["starter"])

    def _profile_changed(self) -> None:
        p = PROFILES[self.profile.currentData()]
        wd = p.watchdog
        txt = (f"{p.cpu_mhz} MHz · {p.cores} core(s), {p.cores_simulated} simulated · {wd.name}: timeout "
               f"{wd.timeout_ms} ms" + (f", window opens {wd.window_min_ms} ms after a service" if wd.window_min_ms else "")
               + f" · {p.notes}")
        if p.planned:
            txt += "  Planned: " + "; ".join(p.planned) + "."
        self.profile_info.setText(txt)

    def load_last_submission(self) -> bool:
        for ex in ("A05-E2", "A05-E1"):
            src = self.ctx.learning.last_submission(self.learner_id, ex)
            if src:
                self.code.setPlainText(src)
                self.log.setPlainText(f"Loaded your last {ex} submission.")
                return True
        self.log.setPlainText("No A05 submission yet; the starter template is loaded.")
        return False

    def _open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open C source", "", "C files (*.c);;All files (*)")
        if path:
            self.code.setPlainText(Path(path).read_text(encoding="utf-8"))

    def _export_csv(self) -> None:
        if not self.last:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export trace", f"{self.last.scenario}.csv", "CSV (*.csv)")
        if path:
            Path(path).write_text(self.last.trace.to_csv(), encoding="utf-8")

    def run(self) -> None:
        QGuiApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            lab = self.ctx.ecu_lab.run(self.code.toPlainText(), self.profile.currentData(),
                                       self.scenario.currentData(), 1, self.learner_id)
        finally:
            QGuiApplication.restoreOverrideCursor()
        if not lab.build.ok:
            self.summary.setText("Build failed.")
            self.log.setPlainText(lab.build.log)
            self.checks.setRowCount(0)
            return
        if not lab.outcome.ok:
            self.summary.setText("Simulation did not complete.")
            self.log.setPlainText(lab.outcome.error)
            return
        r = lab.outcome.result
        self.last = r
        self.csv_btn.setEnabled(True)
        status = "PASS" if r.passed else "FAIL"
        res = lab.build.resources
        size = (f" · host size (indicative): code {res.text} B, static RAM {res.static_ram} B" if res.available else "")
        self.summary.setText(f"<b>{status}</b> · {r.scenario} on {r.profile} · CPU load {r.cpu_load:.1%} · "
                             f"watchdog services {r.wdg_services} · deadline misses {r.deadline_misses} · "
                             f"resets {len(r.resets)}{size}")
        tr = r.trace
        markers = [Marker(e["time_ms"], e["label"], "#a1460d") for e in r.events]
        markers += [Marker(x["time_ms"], f"reset ({x['reason']})", "#c0262d") for x in r.resets]
        self.speed_plot.set_data(tr.t_ms, [Series("speed", tr.speed_rpm, "#2f6fde"),
                                           Series("setpoint", [SPEED_SETPOINT_RPM] * len(tr.t_ms), "#5b6573", True)],
                                 markers, band=(SPEED_SETPOINT_RPM - SPEED_TOL_RPM, SPEED_SETPOINT_RPM + SPEED_TOL_RPM))
        self.duty_plot.set_data(tr.t_ms, [Series("duty", tr.duty_pct, "#1b8a5a")], markers, y_range=(-5, 105))
        self.checks.setRowCount(len(r.checks))
        for i, c in enumerate(r.checks):
            self.checks.setItem(i, 0, QTableWidgetItem(c.name))
            it = QTableWidgetItem(("✓ " if c.passed else "✗ ") + c.detail)
            it.setForeground(Qt.darkGreen if c.passed else Qt.darkRed)
            self.checks.setItem(i, 1, it)
        lines = [f"reset at {x['time_ms']} ms: {x['reason']} - {x['detail']}" for x in r.resets]
        lines += [f"DEM event {d['event_id']} -> {'FAILED' if d['status'] else 'PASSED'} at {d['time_ms']} ms"
                  for d in r.dem_log]
        lines += [f"{t['task']}: {t['activations']} activations, {t['dropped']} dropped" for t in r.tasks
                  if t["activations"] or t["dropped"]]
        self.log.setPlainText("\n".join(lines) or "No resets or diagnostic events.")

    def refresh(self) -> None:
        pass
