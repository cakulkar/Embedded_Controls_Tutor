from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QHeaderView, QLabel, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ecsim.app.services import AppContext
from ecsim.learning.competency import LEVEL_THRESHOLD
from ecsim.learning.content import LEVELS
from ecsim.gui.widgets import card, heading


class CompetencyScreen(QWidget):
    def __init__(self, ctx: AppContext, learner_id: int) -> None:
        super().__init__()
        self.ctx, self.learner_id = ctx, learner_id
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.addWidget(heading("Competency & Certification"))
        note = QLabel(
            f"A level is achieved when ≥{int(LEVEL_THRESHOLD*100)}% of its points come from independent passes "
            "(no solution viewed, limited hints, no declared AI help) and the previous level is achieved. "
            "Advanced/expert also need a practical exercise. Certification requires the Phase 15 practical "
            "examinations; nothing on this screen is a certificate."
        )
        note.setObjectName("banner"); note.setWordWrap(True)
        lay.addWidget(note)

        top = QHBoxLayout()
        top.addWidget(QLabel("Career track:"))
        self.track = QComboBox()
        self.track.addItem("— none —", None)
        for t in ctx.catalog.tracks:
            self.track.addItem(t["title"], t["code"])
        self.track.currentIndexChanged.connect(self._track_changed)
        top.addWidget(self.track, 1)
        self.score = QLabel(); self.score.setObjectName("h2")
        top.addWidget(self.score)
        lay.addLayout(top)

        self.table = QTableWidget(0, 3 + len(LEVELS))
        self.table.setHorizontalHeaderLabels(["Competency", "Weight", "Achieved", *[l.title() for l in LEVELS]])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        lay.addWidget(card(heading("Evidence by level (points earned / available)", "h2"), self.table), 1)
        self.gaps = QLabel(); self.gaps.setWordWrap(True)
        lay.addWidget(card(heading("Track readiness", "h2"), self.gaps))
        self.refresh()

    def _track_changed(self) -> None:
        code = self.track.currentData()
        self.ctx.learning.set_track(self.learner_id, code)
        self.refresh()

    def refresh(self) -> None:
        p = self.ctx.competency.profile(self.learner_id)
        idx = self.track.findData(p.track_code)
        self.track.blockSignals(True)
        self.track.setCurrentIndex(max(idx, 0))
        self.track.blockSignals(False)
        self.score.setText(f"Weighted score {p.weighted_score:.1f}/100")
        self.table.setRowCount(len(p.competencies))
        for i, cs in enumerate(p.competencies):
            self.table.setItem(i, 0, QTableWidgetItem(f"{cs.code} — {cs.title}"))
            self.table.setItem(i, 1, QTableWidgetItem(f"{cs.weight*100:.0f}%"))
            self.table.setItem(i, 2, QTableWidgetItem((cs.achieved_level or "—").title()))
            for j, ls in enumerate(cs.levels):
                txt = "no content yet" if not ls.points_available else f"{ls.points_earned}/{ls.points_available}"
                it = QTableWidgetItem(txt)
                it.setTextAlignment(Qt.AlignCenter)
                if ls.achieved:
                    it.setBackground(QColor("#d7f0dc"))
                elif not ls.points_available:
                    it.setForeground(Qt.gray)
                self.table.setItem(i, 3 + j, it)
        if not p.track_code:
            self.gaps.setText("Select a career track to see which competency levels it requires.")
        else:
            lines = [f"{'✓' if g.met else '✗'}  {g.competency}: requires {g.required}, achieved {g.achieved or 'none'}"
                     for g in p.track_gaps]
            head = "All track requirements met." if p.track_ready else "Gaps remain:"
            self.gaps.setText(head + "\n" + "\n".join(lines))
