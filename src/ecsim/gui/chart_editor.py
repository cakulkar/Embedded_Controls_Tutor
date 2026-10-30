"""Chart (state machine) editor: definition text with live validation + state diagram view."""
from __future__ import annotations

import json
import math
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QGraphicsScene, QGraphicsView, QHBoxLayout,
                               QLabel, QPlainTextEdit, QPushButton, QSplitter, QVBoxLayout)

from ecsim.gui.widgets import mono_font
from ecsim.model.blocks import ModelError
from ecsim.model.chart import Chart, from_scxml, to_scxml


def layout_states(spec: dict) -> dict[str, QRectF]:
    """Nested boxes: top-level states in a row, children laid out inside their parent."""
    kids: dict[str | None, list[str]] = {}
    for s in spec.get("states", []):
        kids.setdefault(s.get("parent"), []).append(s["name"])
    rects: dict[str, QRectF] = {}

    def size(name: str) -> tuple[float, float]:
        ch = kids.get(name, [])
        if not ch:
            return 150.0, 70.0
        sizes = [size(c) for c in ch]
        return sum(w for w, _ in sizes) + 30 * (len(ch) + 1), max(h for _, h in sizes) + 70

    def place(name: str, x: float, y: float) -> None:
        w, h = size(name)
        rects[name] = QRectF(x, y, w, h)
        cx = x + 30
        for c in kids.get(name, []):
            cw, _ = size(c)
            place(c, cx, y + 50)
            cx += cw + 30

    x = 0.0
    for top in kids.get(None, []):
        place(top, x, 0)
        x += size(top)[0] + 90
    return rects


class ChartView(QGraphicsView):
    def __init__(self) -> None:
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.Antialiasing)
        self.setBackgroundBrush(QBrush(QColor("#fbfcfd")))

    def show_chart(self, spec: dict, active: list[str] | None = None) -> None:
        sc = self.scene()
        sc.clear()
        rects = layout_states(spec)
        by_name = {s["name"]: s for s in spec.get("states", [])}
        for name, r in sorted(rects.items(), key=lambda kv: kv[1].width() * kv[1].height(), reverse=True):
            s = by_name[name]
            fill = "#d7f0dc" if active and name in active else ("#fff4d6" if any(x.get("parent") == name for x in by_name.values()) else "#ffffff")
            path = QPainterPath()
            path.addRoundedRect(r, 12, 12)
            sc.addPath(path, QPen(QColor("#4a5566"), 1.4), QBrush(QColor(fill)))
            t = sc.addSimpleText(name, QFont("", 9, QFont.Bold))
            t.setPos(r.x() + 8, r.y() + 4)
            acts = "\n".join(f"{k}: {s[k]}" for k in ("entry", "during", "exit") if s.get(k))
            if acts:
                a = sc.addSimpleText(acts, QFont("", 7))
                a.setPos(r.x() + 8, r.y() + 20)
                a.setBrush(QBrush(QColor("#5b6573")))
        init = spec.get("initial")
        if init in rects:
            r = rects[init]
            sc.addEllipse(r.x() - 26, r.y() + 8, 8, 8, QPen(Qt.NoPen), QBrush(QColor("#1d2430")))
            sc.addLine(r.x() - 18, r.y() + 12, r.x(), r.y() + 12, QPen(QColor("#1d2430"), 1.4))
        pair_count: dict[tuple[str, str], int] = {}
        for t in spec.get("transitions", []):
            a, b = rects.get(t["from"]), rects.get(t["to"])
            if a is None or b is None:
                continue
            key = (t["from"], t["to"])
            k = pair_count.get(key, 0)
            pair_count[key] = k + 1
            p1 = QPointF(a.center().x(), a.bottom()) if t["from"] != t["to"] else QPointF(a.right(), a.center().y())
            p2 = QPointF(b.center().x(), b.bottom())
            bend = 40 + 25 * k
            path = QPainterPath(p1)
            path.cubicTo(p1 + QPointF(0, bend), p2 + QPointF(0, bend), p2)
            sc.addPath(path, QPen(QColor("#2f6fde"), 1.3))
            ang = math.atan2(-1, 0)
            for d in (-0.4, 0.4):
                sc.addLine(p2.x(), p2.y(), p2.x() + 8 * math.cos(ang + math.pi + d), p2.y() - 8 * math.sin(ang + math.pi + d),
                           QPen(QColor("#2f6fde"), 1.3))
            label = t.get("guard", "") + (f" / {t['action']}" if t.get("action") else "")
            if label:
                lt = sc.addSimpleText(f"[{label}]" if t.get("guard") else label, QFont("", 7))
                mid = path.pointAtPercent(0.5)
                lt.setPos(mid.x() - 40, mid.y() + 2)
                lt.setBrush(QBrush(QColor("#1f4fb3")))


class ChartEditorDialog(QDialog):
    def __init__(self, spec: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Chart editor")
        self.resize(1200, 700)
        self.result_spec: dict | None = None
        v = QVBoxLayout(self)
        split = QSplitter(Qt.Horizontal)
        self.text = QPlainTextEdit(json.dumps(spec, indent=2))
        self.text.setFont(mono_font())
        self.text.textChanged.connect(self.validate)
        split.addWidget(self.text)
        self.view = ChartView()
        split.addWidget(self.view)
        split.setSizes([480, 720])
        v.addWidget(split, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        v.addWidget(self.status)
        row = QHBoxLayout()
        imp = QPushButton("Import SCXML…"); imp.setObjectName("secondary"); imp.clicked.connect(self._import)
        exp = QPushButton("Export SCXML…"); exp.setObjectName("secondary"); exp.clicked.connect(self._export)
        row.addWidget(imp); row.addWidget(exp); row.addStretch(1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        row.addWidget(self.buttons)
        v.addLayout(row)
        self.validate()

    def validate(self) -> dict | None:
        try:
            spec = json.loads(self.text.toPlainText())
            Chart(spec)
        except json.JSONDecodeError as exc:
            self.status.setText(f"✗ JSON: {exc}")
            self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)
            return None
        except ModelError as exc:
            self.status.setText(f"✗ {exc}")
            self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)
            return None
        self.status.setText(f"✓ Valid chart: {len(spec['states'])} states, {len(spec.get('transitions', []))} transitions, "
                            f"inputs {spec.get('inputs', [])}, outputs {spec.get('outputs', [])}")
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(True)
        self.view.show_chart(spec)
        return spec

    def set_scxml(self, text: str) -> bool:
        try:
            spec = from_scxml(text)
        except ModelError as exc:
            self.status.setText(f"✗ {exc}")
            return False
        self.text.setPlainText(json.dumps(spec, indent=2))
        return True

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import SCXML", "", "SCXML (*.scxml *.xml)")
        if path:
            self.set_scxml(Path(path).read_text(encoding="utf-8"))

    def _export(self) -> None:
        spec = self.validate()
        if spec is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export SCXML", "chart.scxml", "SCXML (*.scxml)")
        if path:
            Path(path).write_text(to_scxml(spec), encoding="utf-8")

    def _accept(self) -> None:
        spec = self.validate()
        if spec is not None:
            self.result_spec = spec
            self.accept()
