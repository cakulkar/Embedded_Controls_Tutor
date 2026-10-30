"""Testing & Validation Center and Requirements Manager screens."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QPlainTextEdit, QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget,
                               QVBoxLayout, QWidget)

from ecsim.app.services import AppContext
from ecsim.gui.widgets import heading, mono_font
from ecsim.model.blocks import ModelError
from ecsim.verify.requirements import (LEVELS, STATUSES, Requirement, RequirementError, RequirementStore, from_reqif,
                                       import_into, to_reqif)
from ecsim.verify.runner import html_report, verify


def _table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QTableWidget.NoEditTriggers)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    return t


def _fill(t: QTableWidget, rows: list[list[str]], bad_col: int | None = None) -> None:
    t.setRowCount(len(rows))
    for i, r in enumerate(rows):
        for j, v in enumerate(r):
            it = QTableWidgetItem(v)
            if bad_col is not None and j == bad_col:
                it.setForeground(Qt.darkGreen if v in ("PASS", "passed") else Qt.darkRed)
            t.setItem(i, j, it)


class TestingCenterScreen(QWidget):
    __test__ = False

    def __init__(self, ctx: AppContext, learner_id: int, studio_model=lambda: None) -> None:
        super().__init__()
        self.ctx, self.learner_id, self.studio_model = ctx, learner_id, studio_model
        self.model_json: str | None = None
        self.last = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.addWidget(heading("Testing & Validation Center"))
        row = QHBoxLayout()
        self.src = QComboBox()
        self.src.addItems(["Current Model Studio model", "Model file…"])
        self.tests = QComboBox()
        for e in ctx.catalog.all_exercises():
            if e.kind in ("model_mil", "model_sil"):
                self.tests.addItem(f"{e.id} tests — {e.title}", e.id)
        self.tests.addItem("Custom test file (JSON)…", "__file__")
        for w in (QLabel("Model"), self.src, QLabel("Tests"), self.tests):
            row.addWidget(w)
        self.run_btn = QPushButton("Run verification"); self.run_btn.clicked.connect(self.run)
        self.rep_btn = QPushButton("Export HTML report…"); self.rep_btn.setObjectName("secondary")
        self.rep_btn.clicked.connect(self.export_report); self.rep_btn.setEnabled(False)
        row.addStretch(1); row.addWidget(self.run_btn); row.addWidget(self.rep_btn)
        lay.addLayout(row)
        self.summary = QLabel("Choose a model and a test set, then run."); self.summary.setWordWrap(True)
        lay.addWidget(self.summary)
        self.tabs = QTabWidget()
        self.results = _table(["Test", "Check", "Result", "Evidence"])
        self.coverage = _table(["Block", "Outcome", "Covered"])
        self.findings = _table(["Severity", "Rule", "Where", "Finding"])
        self.trace = _table(["Requirement", "Title", "Status", "Checks"])
        for w, n in ((self.results, "Results"), (self.coverage, "Coverage"), (self.findings, "Guidelines"), (self.trace, "Traceability")):
            self.tabs.addTab(w, n)
        lay.addWidget(self.tabs, 1)

    def _model(self) -> str | None:
        if self.src.currentIndex() == 0:
            m = self.studio_model()
            return json.dumps(m) if m else None
        if self.model_json is None:
            path, _ = QFileDialog.getOpenFileName(self, "Model", "", "ECSim model (*.json)")
            self.model_json = Path(path).read_text(encoding="utf-8") if path else None
        return self.model_json

    def _spec(self) -> tuple[dict | None, str]:
        ex = self.tests.currentData()
        if ex != "__file__":
            e = self.ctx.catalog.exercise(ex)
            return {"interface": e.spec["interface"], "tests": e.spec["tests"]}, f"{ex} requirement tests"
        path, _ = QFileDialog.getOpenFileName(self, "Test file", "", "JSON (*.json)")
        if not path:
            return None, ""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data, Path(path).name

    def run(self, spec: dict | None = None, source: str | None = None) -> bool:
        model = self._model()
        if not model:
            self.summary.setText("No model: build one in the Model Studio, or choose a model file.")
            return False
        if spec is None:
            spec, source = self._spec()
            if spec is None:
                return False
        reqs = RequirementStore(self.ctx.conn, self.learner_id).all()
        try:
            run = verify(model, spec, reqs, source or "custom")
        except (ModelError, KeyError, ValueError) as exc:
            self.summary.setText(f"✗ {exc}")
            return False
        self.last = run
        self.rep_btn.setEnabled(True)
        mil = run.mil
        cov = f"{run.coverage.covered}/{run.coverage.total} ({run.coverage.ratio:.0%})" if run.coverage else "n/a (closed-loop only)"
        self.summary.setText(("✗ " + "; ".join(mil.errors) + "  ") * bool(mil.errors) +
                             f"Checks {sum(o.passed for o in mil.outcomes)}/{len(mil.outcomes)} · decision coverage {cov} · "
                             f"guideline findings {len(run.findings)} · requirements traced "
                             f"{sum(1 for r in run.trace if r.checks)}/{len(run.trace)}")
        _fill(self.results, [[o.test, o.name, "PASS" if o.passed else "FAIL", o.detail] for o in mil.outcomes], 2)
        if run.coverage:
            rows = [[b, o, "yes" if o in run.coverage.hit.get(b, set()) else "NO"] for b, outs in run.coverage.outcomes.items() for o in outs]
            _fill(self.coverage, rows)
        _fill(self.findings, [[f.severity, f.rule, f.where, f.message] for f in run.findings])
        _fill(self.trace, [[r.req_id, r.title, r.status, str(len(r.checks))] for r in run.trace], 2)
        return True

    def export_report(self, path: str | None = None) -> str | None:
        if not self.last:
            return None
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, "Save report", f"verification_{self.last.model_name}.html", "HTML (*.html)")
            if not path:
                return None
        name = self.ctx.conn.execute("SELECT name FROM learner WHERE id=?", (self.learner_id,)).fetchone()[0]
        Path(path).write_text(html_report(self.last, name), encoding="utf-8")
        return path

    def refresh(self) -> None:
        pass


class RequirementsScreen(QWidget):
    def __init__(self, ctx: AppContext, learner_id: int) -> None:
        super().__init__()
        self.ctx = ctx
        self.store = RequirementStore(ctx.conn, learner_id)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.addWidget(heading("Requirements Manager"))
        bar = QHBoxLayout()
        self.ex = QComboBox()
        for e in ctx.catalog.all_exercises():
            if e.kind in ("model_mil", "model_sil"):
                self.ex.addItem(f"{e.id} — {e.title}", e.id)
        imp = QPushButton("Import requirements from exercise"); imp.setObjectName("secondary"); imp.clicked.connect(self.import_exercise)
        ri = QPushButton("Import ReqIF…"); ri.setObjectName("secondary"); ri.clicked.connect(self.import_reqif)
        re_ = QPushButton("Export ReqIF…"); re_.setObjectName("secondary"); re_.clicked.connect(self.export_reqif)
        for w in (self.ex, imp, ri, re_):
            bar.addWidget(w)
        bar.addStretch(1)
        lay.addLayout(bar)
        split = QSplitter(Qt.Horizontal)
        self.table = _table(["ID", "Level", "Title", "Status", "Parent", "Links"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.itemSelectionChanged.connect(self._load_selected)
        split.addWidget(self.table)
        form_w = QWidget()
        f = QFormLayout(form_w)
        self.f_id, self.f_title, self.f_parent = QLineEdit(), QLineEdit(), QLineEdit()
        self.f_level, self.f_status = QComboBox(), QComboBox()
        self.f_level.addItems(LEVELS); self.f_status.addItems(STATUSES)
        self.f_text, self.f_acc = QPlainTextEdit(), QPlainTextEdit()
        self.f_text.setMaximumHeight(110); self.f_acc.setMaximumHeight(80)
        for lab, w in (("ID", self.f_id), ("Level", self.f_level), ("Title", self.f_title), ("Text", self.f_text),
                       ("Acceptance criteria", self.f_acc), ("Parent ID", self.f_parent), ("Status", self.f_status)):
            f.addRow(lab, w)
        btns = QHBoxLayout()
        for lab, fn in (("Save", self.save), ("New", self.clear), ("Delete", self.delete)):
            b = QPushButton(lab); b.clicked.connect(fn)
            if lab != "Save":
                b.setObjectName("secondary")
            btns.addWidget(b)
        f.addRow(btns)
        link_row = QHBoxLayout()
        self.l_kind = QComboBox(); self.l_kind.addItems(["verifies", "implements", "derives"])
        self.l_target = QLineEdit(); self.l_target.setPlaceholderText("e.g. B02-E4:CL1 or speed_pi/integrator")
        lb = QPushButton("Add link"); lb.setObjectName("secondary"); lb.clicked.connect(self.add_link)
        link_row.addWidget(self.l_kind); link_row.addWidget(self.l_target); link_row.addWidget(lb)
        f.addRow("Trace link", link_row)
        self.links = QLabel(); self.links.setWordWrap(True)
        f.addRow("Links", self.links)
        self.msg = QLabel(); self.msg.setWordWrap(True)
        f.addRow(self.msg)
        split.addWidget(form_w)
        split.setSizes([700, 500])
        lay.addWidget(split, 1)
        self.refresh()

    def refresh(self) -> None:
        reqs = self.store.all()
        counts = {}
        for rid, _k, _t in self.store.links():
            counts[rid] = counts.get(rid, 0) + 1
        _fill(self.table, [[r.req_id, r.level, r.title, r.status, r.parent_req_id or "", str(counts.get(r.req_id, 0))] for r in reqs])

    def _load_selected(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        r = self.store.get(self.table.item(rows[0].row(), 0).text())
        if r:
            self.f_id.setText(r.req_id); self.f_level.setCurrentText(r.level); self.f_title.setText(r.title)
            self.f_text.setPlainText(r.text); self.f_acc.setPlainText(r.acceptance); self.f_parent.setText(r.parent_req_id or "")
            self.f_status.setCurrentText(r.status)
            self.links.setText("\n".join(f"{k} → {t}" for _r, k, t in self.store.links(r.req_id)) or "none")

    def clear(self) -> None:
        for w in (self.f_id, self.f_title, self.f_parent):
            w.clear()
        self.f_text.clear(); self.f_acc.clear(); self.links.clear()

    def _do(self, fn, ok_msg: str) -> bool:
        try:
            fn()
        except RequirementError as exc:
            self.msg.setText(f"✗ {exc}")
            return False
        self.msg.setText(ok_msg)
        self.refresh()
        return True

    def save(self) -> bool:
        r = Requirement(self.f_id.text().strip(), self.f_level.currentText(), self.f_title.text(), self.f_text.toPlainText(),
                        self.f_acc.toPlainText(), self.f_parent.text().strip() or None, self.f_status.currentText())
        return self._do(lambda: self.store.upsert(r), f"Saved {r.req_id}")

    def delete(self) -> bool:
        rid = self.f_id.text().strip()
        return self._do(lambda: self.store.delete(rid), f"Deleted {rid}")

    def add_link(self) -> bool:
        rid, tgt = self.f_id.text().strip(), self.l_target.text().strip()
        if not tgt:
            self.msg.setText("✗ enter a link target")
            return False
        ok = self._do(lambda: self.store.link(rid, self.l_kind.currentText(), tgt), f"Linked {rid}")
        self._load_selected()
        return ok

    def import_exercise(self) -> int:
        ex = self.ex.currentData()
        n = self.store.import_exercise(ex, self.ctx.catalog.exercise(ex).spec)
        self.msg.setText(f"Imported {n} requirement(s) from {ex} and linked its tests.")
        self.refresh()
        return n

    def import_reqif(self, path: str | None = None) -> bool:
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, "Import ReqIF", "", "ReqIF (*.reqif *.xml)")
            if not path:
                return False
        try:
            reqs, warns = from_reqif(Path(path).read_text(encoding="utf-8"))
        except RequirementError as exc:
            self.msg.setText(f"✗ {exc}")
            return False
        problems = import_into(self.store, reqs)
        self.msg.setText(f"Imported {len(reqs) - len(problems)} of {len(reqs)}." + "".join(f"\n• {w}" for w in warns + problems))
        self.refresh()
        return not problems

    def export_reqif(self, path: str | None = None) -> str | None:
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, "Export ReqIF", "requirements.reqif", "ReqIF (*.reqif)")
            if not path:
                return None
        Path(path).write_text(to_reqif(self.store.all()), encoding="utf-8")
        self.msg.setText(f"Exported to {path}")
        return path
