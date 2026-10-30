"""Vehicle & Machine Simulator: open-loop experiments on the plant models the graders use."""
from __future__ import annotations

from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from ecsim.app.services import AppContext
from ecsim.gui.plot import Series, SignalPlot
from ecsim.gui.widgets import heading
from ecsim.model.blocks import ModelError
from ecsim.plants.explorer import SCENARIOS

COLORS = ["#2563eb", "#dc2626", "#16a34a", "#9333ea"]


class PlantSimScreen(QWidget):
    def __init__(self, ctx: AppContext, learner_id: int) -> None:
        super().__init__()
        self.ctx = ctx
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.addWidget(heading("Vehicle & Machine Simulator"))
        bar = QHBoxLayout()
        self.combo = QComboBox()
        for key, s in SCENARIOS.items():
            self.combo.addItem(s.title, key)
        self.combo.currentIndexChanged.connect(self._rebuild_params)
        self.run_btn = QPushButton("Run"); self.run_btn.clicked.connect(self.run)
        bar.addWidget(QLabel("Plant:")); bar.addWidget(self.combo, 1); bar.addWidget(self.run_btn)
        lay.addLayout(bar)
        self.desc = QLabel(); self.desc.setWordWrap(True)
        lay.addWidget(self.desc)
        body = QHBoxLayout()
        left = QVBoxLayout()
        self.form_host = QWidget(); self.form = QFormLayout(self.form_host)
        left.addWidget(self.form_host)
        self.summary = QLabel(); self.summary.setWordWrap(True); self.summary.setObjectName("muted")
        left.addWidget(self.summary); left.addStretch(1)
        lw = QWidget(); lw.setLayout(left); lw.setFixedWidth(330)
        body.addWidget(lw)
        self.plots_host = QWidget(); self.plots = QVBoxLayout(self.plots_host)
        sc = QScrollArea(); sc.setWidgetResizable(True); sc.setWidget(self.plots_host)
        body.addWidget(sc, 1)
        lay.addLayout(body, 1)
        self.spins: dict[str, QDoubleSpinBox] = {}
        self.plot_widgets: list[SignalPlot] = []
        self._rebuild_params()

    @property
    def scenario(self):
        return SCENARIOS[self.combo.currentData()]

    def _rebuild_params(self) -> None:
        while self.form.rowCount():
            self.form.removeRow(0)
        self.spins = {}
        s = self.scenario
        self.desc.setText(s.description)
        for p in s.params:
            sp = QDoubleSpinBox()
            sp.setRange(p.lo, p.hi); sp.setDecimals(3 if p.hi <= 1 else 2); sp.setValue(p.default)
            sp.setSuffix(f" {p.unit}" if p.unit else "")
            self.form.addRow(p.label, sp)
            self.spins[p.key] = sp
        self.run()

    def run(self) -> None:
        try:
            r = self.scenario.run({k: sp.value() for k, sp in self.spins.items()})
        except ModelError as exc:
            self.summary.setText(f"Error: {exc}")
            return
        for w in self.plot_widgets:
            w.setParent(None); w.deleteLater()
        self.plot_widgets = []
        for panel in r.panels:
            pw = SignalPlot(panel.title, panel.unit, time_in_ms=False)
            pw.set_data(r.t, [Series(n, v, COLORS[i % len(COLORS)], dashed=i > 0) for i, (n, v) in enumerate(panel.series.items())])
            self.plots.addWidget(pw)
            self.plot_widgets.append(pw)
        self.summary.setText("\n".join(r.summary))

    def refresh(self) -> None:
        pass
