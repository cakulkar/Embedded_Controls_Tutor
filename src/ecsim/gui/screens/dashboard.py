from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QGridLayout, QHBoxLayout, QHeaderView, QLabel, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ecsim.app import toolchain
from ecsim.app.services import AppContext
from ecsim.gui.widgets import card, heading


def _table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QTableWidget.NoEditTriggers)
    t.setSelectionMode(QTableWidget.NoSelection)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    return t


class DashboardScreen(QWidget):
    def __init__(self, ctx: AppContext, learner_id: int) -> None:
        super().__init__()
        self.ctx, self.learner_id = ctx, learner_id
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        self.title = heading("Engineering Dashboard")
        lay.addWidget(self.title)

        stats = QHBoxLayout()
        self.score_lbl = QLabel(); self.score_lbl.setObjectName("h1")
        self.track_lbl = QLabel(); self.track_lbl.setWordWrap(True)
        self.progress_lbl = QLabel()
        stats.addWidget(card(QLabel("Weighted competency score"), self.score_lbl,
                             self._muted("Progress indicator from independent work only — not a certification.")))
        stats.addWidget(card(QLabel("Career track"), self.track_lbl))
        stats.addWidget(card(QLabel("Content progress"), self.progress_lbl))
        lay.addLayout(stats)

        grid = QGridLayout()
        self.recent = _table(["When (UTC)", "Exercise", "Result", "Evidence"])
        self.tools = _table(["Tool", "Status", "Used for"])
        grid.addWidget(card(heading("Recent attempts", "h2"), self.recent), 0, 0)
        grid.addWidget(card(heading("Local toolchain", "h2"), self.tools), 0, 1)
        lay.addLayout(grid, 1)
        self._tool_status = toolchain.probe()
        self.refresh()

    @staticmethod
    def _muted(text: str) -> QLabel:
        l = QLabel(text); l.setObjectName("muted"); l.setWordWrap(True)
        return l

    def refresh(self) -> None:
        ctx, lid = self.ctx, self.learner_id
        row = ctx.conn.execute(
            "SELECT l.name, t.title FROM learner l LEFT JOIN career_track t ON t.code=l.track_code WHERE l.id=?",
            (lid,),
        ).fetchone()
        self.title.setText(f"Engineering Dashboard — {row['name']}")
        prof = ctx.competency.profile(lid)
        self.score_lbl.setText(f"{prof.weighted_score:.1f} / 100")
        if prof.track_code:
            met = sum(g.met for g in prof.track_gaps)
            self.track_lbl.setText(f"{row['title']}\n{met}/{len(prof.track_gaps)} competency requirements met")
        else:
            self.track_lbl.setText("No track selected (choose one in Competency & Certification)")
        exs = ctx.catalog.all_exercises()
        done = sum(1 for e in exs if ctx.learning.state(lid, e.id).passed_independently)
        lessons = sum(len(m.lessons) for m in ctx.catalog.modules)
        self.progress_lbl.setText(
            f"{done}/{len(exs)} exercises passed independently\n"
            f"{len(ctx.learning.completed_lessons(lid))}/{lessons} lessons read"
        )
        att = ctx.learning.attempts(lid, limit=12)
        self.recent.setRowCount(len(att))
        for i, a in enumerate(att):
            vals = [a.submitted_at[:19].replace("T", " "), a.exercise_id, "PASS" if a.passed else "fail",
                    "independent" if a.independent else "assisted"]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if j == 2:
                    it.setForeground(Qt.darkGreen if a.passed else Qt.darkRed)
                self.recent.setItem(i, j, it)
        tools = list(self._tool_status.values())
        self.tools.setRowCount(len(tools))
        for i, t in enumerate(tools):
            self.tools.setItem(i, 0, QTableWidgetItem(t.name))
            st = QTableWidgetItem("available" if t.available else "not installed")
            st.setForeground(Qt.darkGreen if t.available else Qt.gray)
            self.tools.setItem(i, 1, st)
            self.tools.setItem(i, 2, QTableWidgetItem(t.purpose))
