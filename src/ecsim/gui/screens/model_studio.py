"""Control Model Studio: graphical, executable block-diagram editor.

The canvas is a view of a ModelDocument (ecsim.model.document). Every edit is a document
command (undo/redo); the scene is rebuilt from the document after each change. Simulation
uses the same engine that grades exercises, so what you see is what is graded.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QFont, QKeySequence, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QGraphicsEllipseItem, QGraphicsItem, QGraphicsPathItem, QGraphicsRectItem,
                               QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QTableWidget,
                               QTableWidgetItem, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ecsim.app.services import AppContext
from ecsim.gui.plot import Series, SignalPlot
from ecsim.gui.widgets import heading, mono_font
from ecsim.model.blocks import LIB, ModelError
from ecsim.model.core import Simulator
from ecsim.model.document import PALETTE, ModelDocument, coerce_param, port_counts
from ecsim.model.mil import signal_value
from ecsim.model.slx import export_build_script, import_slx, parse_m_variables

BW, BH = 110, 56            # block size
PORT_R = 5
MIME = "application/x-ecsim-block"
COLORS = {"Inport": "#e8f1ff", "Outport": "#e8f1ff", "Subsystem": "#fff4d6", "Constant": "#eef7ee"}


def parse_signal(text: str) -> dict:
    """'const 3000' | 'step 1.0 0 4000' | 'ramp 0 100' (t0 slope) | '3000' -> signal spec."""
    parts = text.split()
    try:
        if len(parts) == 1:
            return {"const": float(parts[0])}
        kind = parts[0].lower()
        if kind == "const":
            return {"const": float(parts[1])}
        if kind == "step":
            return {"step": {"t": float(parts[1]), "before": float(parts[2]), "after": float(parts[3])}}
        if kind == "ramp":
            return {"ramp": {"t0": float(parts[1]), "slope": float(parts[2]), "start": float(parts[3]) if len(parts) > 3 else 0.0}}
    except (IndexError, ValueError):
        pass
    raise ModelError(f"signal {text!r}: use 'const V', 'step T BEFORE AFTER' or 'ramp T0 SLOPE [START]'")


def block_label(b: dict) -> str:
    p = b.get("params", {})
    t = b["type"]
    detail = {"Gain": lambda: f"K={p.get('gain', 1):g}", "Constant": lambda: f"{p.get('value', 0):g}",
              "Sum": lambda: p.get("signs", "++"), "Product": lambda: p.get("ops", "**"),
              "Saturation": lambda: f"[{p.get('lower', -0.5):g}, {p.get('upper', 0.5):g}]",
              "RelationalOperator": lambda: p.get("op", "<="), "LogicalOperator": lambda: p.get("op", "AND"),
              "Inport": lambda: f"In {p.get('port', 1)}", "Outport": lambda: f"Out {p.get('port', 1)}",
              "UnitDelay": lambda: "1/z", "DiscreteIntegrator": lambda: "K·T/(z−1)" if p.get("method", "ForwardEuler") == "ForwardEuler" else "K·T·z/(z−1)",
              "Lookup1D": lambda: "1-D LUT", "Subsystem": lambda: "⊞ subsystem",
              "Chart": lambda: f"{len((p.get('chart') or {}).get('states', []))} states"}.get(t, lambda: "")()
    return f"{t}\n{detail}" if detail else t


class PortItem(QGraphicsEllipseItem):
    def __init__(self, block_id: str, role: str, index: int, parent: QGraphicsItem) -> None:
        super().__init__(-PORT_R, -PORT_R, 2 * PORT_R, 2 * PORT_R, parent)
        self.block_id, self.role, self.index = block_id, role, index
        self.setBrush(QBrush(QColor("#2f6fde" if role == "out" else "#ffffff")))
        self.setPen(QPen(QColor("#2f6fde"), 1.5))
        self.setAcceptHoverEvents(True)
        self.setZValue(3)

    @property
    def ref(self) -> str:
        return f"{self.block_id}:{self.index}"


class BlockItem(QGraphicsRectItem):
    def __init__(self, studio: "ModelStudioScreen", b: dict) -> None:
        super().__init__(0, 0, BW, BH)
        self.studio, self.block_id = studio, b["id"]
        self.setPos(*b.get("pos", [100, 100]))
        self.setBrush(QBrush(QColor(COLORS.get(b["type"], "#ffffff"))))
        self.setPen(QPen(QColor("#4a5566"), 1.2))
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemSendsGeometryChanges)
        t = QGraphicsSimpleTextItem(block_label(b), self)
        t.setFont(QFont("", 8))
        t.setPos(6, 6)
        name = QGraphicsSimpleTextItem(b["id"], self)
        name.setFont(QFont("", 8, QFont.Bold))
        name.setPos(0, BH + 2)
        n_in, n_out = port_counts(b)
        self.inputs = [self._port("in", k, n_in) for k in range(1, n_in + 1)]
        self.outputs = [self._port("out", k, n_out) for k in range(1, n_out + 1)]
        self._press_pos: QPointF | None = None

    def _port(self, role: str, k: int, n: int) -> PortItem:
        p = PortItem(self.block_id, role, k, self)
        p.setPos(0 if role == "in" else BW, BH * k / (n + 1))
        return p

    def itemChange(self, change, value):  # noqa: N802
        if change == QGraphicsItem.ItemPositionHasChanged and self.scene():
            self.studio.canvas.update_wires()
        return super().itemChange(change, value)

    def mousePressEvent(self, ev):  # noqa: N802
        self._press_pos = self.pos()
        super().mousePressEvent(ev)

    def mouseReleaseEvent(self, ev):  # noqa: N802
        super().mouseReleaseEvent(ev)
        if self._press_pos is not None and self.pos() != self._press_pos:
            moved = [i for i in self.scene().selectedItems() if isinstance(i, BlockItem)] or [self]
            for it in moved:
                self.studio.doc.move_block(it.block_id, round(it.pos().x() / 10) * 10, round(it.pos().y() / 10) * 10)
        self._press_pos = None

    def mouseDoubleClickEvent(self, ev):  # noqa: N802
        self.studio.open_block(self.block_id)


class WireItem(QGraphicsPathItem):
    def __init__(self, src: PortItem, dst: PortItem) -> None:
        super().__init__()
        self.src, self.dst = src, dst
        self.setPen(QPen(QColor("#1d2430"), 1.4))
        self.setFlags(QGraphicsItem.ItemIsSelectable)
        self.setZValue(1)
        self.update_path()

    def update_path(self) -> None:
        a, b = self.src.scenePos(), self.dst.scenePos()
        mid = (a.x() + b.x()) / 2 if b.x() > a.x() + 20 else a.x() + 20
        path = QPainterPath(a)
        if b.x() > a.x() + 20:
            path.lineTo(mid, a.y()); path.lineTo(mid, b.y()); path.lineTo(b)
        else:   # feedback wire: route below
            low = max(a.y(), b.y()) + BH
            path.lineTo(a.x() + 20, a.y()); path.lineTo(a.x() + 20, low); path.lineTo(b.x() - 20, low)
            path.lineTo(b.x() - 20, b.y()); path.lineTo(b)
        arrow = QPainterPath(b)
        arrow.lineTo(b.x() - 7, b.y() - 4); arrow.moveTo(b); arrow.lineTo(b.x() - 7, b.y() + 4)
        path.addPath(arrow)
        self.setPath(path)

    def paint(self, painter, option, widget=None):
        self.setPen(QPen(QColor("#2f6fde" if self.isSelected() else "#1d2430"), 2.2 if self.isSelected() else 1.4))
        super().paint(painter, option, widget)


class Canvas(QGraphicsView):
    def __init__(self, studio: "ModelStudioScreen") -> None:
        super().__init__()
        self.studio = studio
        self.setScene(QGraphicsScene(self))
        self.scene().setSceneRect(QRectF(-200, -200, 2400, 1600))
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setAcceptDrops(True)
        self.setBackgroundBrush(QBrush(QColor("#fbfcfd")))
        self.blocks: dict[str, BlockItem] = {}
        self.wires: list[WireItem] = []
        self._drag_src: PortItem | None = None
        self._temp: QGraphicsPathItem | None = None

    def rebuild(self, model: dict) -> None:
        selected = {i.block_id for i in self.scene().selectedItems() if isinstance(i, BlockItem)}
        self.scene().clear()
        self.blocks, self.wires = {}, []
        for b in model["blocks"]:
            it = BlockItem(self.studio, b)
            self.scene().addItem(it)
            self.blocks[b["id"]] = it
            it.setSelected(b["id"] in selected)
        for ln in model["lines"]:
            sb, sp = ln["src"].rsplit(":", 1)
            db, dp = ln["dst"].rsplit(":", 1)
            try:
                w = WireItem(self.blocks[sb].outputs[int(sp) - 1], self.blocks[db].inputs[int(dp) - 1])
            except (KeyError, IndexError):
                continue
            self.scene().addItem(w)
            self.wires.append(w)

    def update_wires(self) -> None:
        for w in self.wires:
            w.update_path()

    def port_item(self, block_id: str, role: str, index: int) -> PortItem:
        b = self.blocks[block_id]
        return (b.inputs if role == "in" else b.outputs)[index - 1]

    # wiring by drag from an output port to an input port
    def mousePressEvent(self, ev):  # noqa: N802
        item = self.itemAt(ev.position().toPoint())
        if isinstance(item, PortItem) and item.role == "out":
            self._drag_src = item
            self._temp = QGraphicsPathItem()
            self._temp.setPen(QPen(QColor("#2f6fde"), 1.4, Qt.DashLine))
            self.scene().addItem(self._temp)
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):  # noqa: N802
        if self._drag_src and self._temp:
            p = QPainterPath(self._drag_src.scenePos())
            p.lineTo(self.mapToScene(ev.position().toPoint()))
            self._temp.setPath(p)
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):  # noqa: N802
        if self._drag_src:
            src, self._drag_src = self._drag_src, None
            self.scene().removeItem(self._temp)
            self._temp = None
            target = next((i for i in self.items(ev.position().toPoint()) if isinstance(i, PortItem) and i.role == "in"), None)
            if target is not None:
                self.studio.connect(src.ref, target.ref)
            return
        super().mouseReleaseEvent(ev)

    def keyPressEvent(self, ev):  # noqa: N802
        if ev.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.studio.delete_selected()
            return
        super().keyPressEvent(ev)

    def wheelEvent(self, ev):  # noqa: N802
        if ev.modifiers() & Qt.ControlModifier:
            f = 1.15 if ev.angleDelta().y() > 0 else 1 / 1.15
            self.scale(f, f)
            return
        super().wheelEvent(ev)

    def dragEnterEvent(self, ev):  # noqa: N802
        if ev.mimeData().hasFormat(MIME):
            ev.acceptProposedAction()

    dragMoveEvent = dragEnterEvent

    def dropEvent(self, ev):  # noqa: N802
        t = bytes(ev.mimeData().data(MIME)).decode()
        p = self.mapToScene(ev.position().toPoint())
        self.studio.add_block(t, (p.x() - BW / 2, p.y() - BH / 2))
        ev.acceptProposedAction()


class Palette(QTreeWidget):
    def __init__(self, studio: "ModelStudioScreen") -> None:
        super().__init__()
        self.studio = studio
        self.setHeaderHidden(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragOnly)
        for group, types in PALETTE.items():
            g = QTreeWidgetItem([group])
            for t in types:
                it = QTreeWidgetItem([t])
                it.setData(0, Qt.UserRole, t)
                it.setToolTip(0, f"{LIB[t].simulink}\n{LIB[t].doc}".strip())
                g.addChild(it)
            self.addTopLevelItem(g)
        self.expandAll()
        self.itemDoubleClicked.connect(lambda it, _c: it.data(0, Qt.UserRole) and studio.add_block(it.data(0, Qt.UserRole)))

    def mimeData(self, items):  # noqa: N802
        md = super().mimeData(items)
        t = items[0].data(0, Qt.UserRole) if items else None
        if t:
            md.setData(MIME, t.encode())
        return md


class ModelStudioScreen(QWidget):
    def __init__(self, ctx: AppContext, learner_id: int) -> None:
        super().__init__()
        self.ctx, self.learner_id = ctx, learner_id
        self.stack: list[tuple[ModelDocument, str | None]] = []   # (parent doc, subsystem id)
        self.doc = ModelDocument()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        top = QHBoxLayout()
        top.addWidget(heading("Control Model Studio"))
        self.crumbs = QLabel(); self.crumbs.setObjectName("muted")
        top.addWidget(self.crumbs, 1)
        lay.addLayout(top)

        bar = QHBoxLayout()
        self.buttons: dict[str, QPushButton] = {}
        for key, label, fn in [("new", "New", self.new_model), ("open", "Open…", self.open_dialog),
                               ("save", "Save", self.save), ("import", "Import .slx…", self.import_dialog),
                               ("export", "Export Simulink script…", self.export_dialog), ("undo", "Undo", self.undo),
                               ("redo", "Redo", self.redo), ("delete", "Delete", self.delete_selected),
                               ("group", "Create subsystem", self.group_selected), ("up", "▲ Up", self.go_up),
                               ("check", "Check model", self.check_model)]:
            b = QPushButton(label)
            if key not in ("check",):
                b.setObjectName("secondary")
            b.clicked.connect(fn)
            bar.addWidget(b)
            self.buttons[key] = b
        bar.addStretch(1)
        lay.addLayout(bar)
        for seq, fn in ((QKeySequence.Undo, self.undo), (QKeySequence.Redo, self.redo), (QKeySequence.Save, self.save)):
            a = QAction(self)
            a.setShortcut(seq)
            a.triggered.connect(fn)
            self.addAction(a)

        split = QSplitter(Qt.Horizontal)
        self.palette = Palette(self)
        split.addWidget(self.palette)
        center = QSplitter(Qt.Vertical)
        self.canvas = Canvas(self)
        center.addWidget(self.canvas)
        self.tabs = QTabWidget()
        self.messages = QPlainTextEdit(); self.messages.setReadOnly(True); self.messages.setFont(mono_font())
        self.tabs.addTab(self.messages, "Messages")
        self.tabs.addTab(self._build_sim_panel(), "Simulate")
        self.tabs.addTab(self._build_grade_panel(), "Grade against exercise")
        self.tabs.addTab(self._build_code_panel(), "Code / SIL / vECU")
        center.addWidget(self.tabs)
        center.setSizes([560, 260])
        split.addWidget(center)
        split.addWidget(self._build_props())
        split.setSizes([170, 900, 260])
        lay.addWidget(split, 1)
        self._bind(self.doc)

    # ---- panels ----------------------------------------------------------------
    def _build_props(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(heading("Properties", "h2"))
        self.prop_title = QLabel("Select a block"); self.prop_title.setWordWrap(True)
        v.addWidget(self.prop_title)
        self.form_host = QWidget()
        self.form = QFormLayout(self.form_host)
        v.addWidget(self.form_host)
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.clicked.connect(self.apply_props)
        v.addWidget(self.apply_btn)
        v.addSpacing(12)
        v.addWidget(heading("Solver", "h2"))
        row = QHBoxLayout()
        row.addWidget(QLabel("Fixed-step discrete, step [s]"))
        self.step_box = QDoubleSpinBox(); self.step_box.setDecimals(4); self.step_box.setRange(0.0001, 10.0)
        self.step_box.setSingleStep(0.001)
        self.step_box.editingFinished.connect(lambda: self._try(lambda: self.doc.set_step(self.step_box.value())))
        row.addWidget(self.step_box)
        v.addLayout(row)
        v.addStretch(1)
        self.prop_edits: dict[str, QLineEdit] = {}
        self.current_block: str | None = None
        return w

    def _build_sim_panel(self) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        left = QVBoxLayout()
        self.sim_inputs = QTableWidget(0, 2)
        self.sim_inputs.setHorizontalHeaderLabels(["Inport", "Signal (const V | step T A B | ramp T0 S)"])
        self.sim_inputs.horizontalHeader().setStretchLastSection(True)
        left.addWidget(self.sim_inputs)
        row = QHBoxLayout()
        row.addWidget(QLabel("Duration [s]"))
        self.sim_dur = QDoubleSpinBox(); self.sim_dur.setRange(0.01, 600); self.sim_dur.setValue(3.0)
        row.addWidget(self.sim_dur)
        self.sim_closed = QCheckBox("Closed loop with DC motor (In1 = speed rpm, Out1 = volts)")
        row.addWidget(self.sim_closed)
        self.sim_btn = QPushButton("Run")
        self.sim_btn.clicked.connect(self.simulate)
        row.addWidget(self.sim_btn)
        left.addLayout(row)
        h.addLayout(left, 1)
        self.sim_plot = SignalPlot("Simulation", "value")
        h.addWidget(self.sim_plot, 2)
        return w

    def _build_grade_panel(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        row = QHBoxLayout()
        self.grade_ex = QComboBox()
        for e in self.ctx.catalog.all_exercises():
            if e.kind == "model_mil":
                self.grade_ex.addItem(f"{e.id} — {e.title}", e.id)
        row.addWidget(self.grade_ex, 1)
        self.grade_btn = QPushButton("Submit this model")
        self.grade_btn.clicked.connect(self.submit_to_exercise)
        row.addWidget(self.grade_btn)
        v.addLayout(row)
        self.grade_out = QPlainTextEdit(); self.grade_out.setReadOnly(True); self.grade_out.setFont(mono_font())
        v.addWidget(self.grade_out)
        return w

    def _build_code_panel(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        row = QHBoxLayout()
        self.code_dtype = QComboBox()
        self.code_dtype.addItems(["double", "single"])
        row.addWidget(QLabel("Precision"))
        row.addWidget(self.code_dtype)
        for label, fn in (("Generate C", self.generate_code), ("Save code…", self.save_code),
                          ("Back-to-back vs exercise tests", self.run_b2b)):
            b = QPushButton(label)
            b.setObjectName("secondary" if "Save" in label else "")
            b.clicked.connect(fn)
            row.addWidget(b)
        self.deploy_profile = QComboBox()
        from ecsim.ecu.profiles import PROFILES
        for pcode, prof in PROFILES.items():
            self.deploy_profile.addItem(prof.title, pcode)
        row.addWidget(self.deploy_profile)
        dep = QPushButton("Deploy to virtual ECU")
        dep.clicked.connect(self.deploy)
        row.addWidget(dep)
        row.addStretch(1)
        v.addLayout(row)
        self.code_view = QPlainTextEdit(); self.code_view.setReadOnly(True); self.code_view.setFont(mono_font())
        v.addWidget(self.code_view)
        self.generated = None
        return w

    def generate_code(self) -> bool:
        from ecsim.model.codegen import generate_c
        doc = self._commit_to_root()
        try:
            self.generated = generate_c(doc.model, self.code_dtype.currentText())
        except ModelError as exc:
            self.messages.setPlainText(f"✗ {exc}")
            self.tabs.setCurrentIndex(0)
            return False
        g = self.generated
        self.code_view.setPlainText(f"/* ===== {g.name}.h ===== */\n{g.header}\n/* ===== {g.name}.c ===== */\n{g.source}")
        self.tabs.setCurrentIndex(3)
        return True

    def save_code(self, folder: str | None = None) -> bool:
        if not self.generated and not self.generate_code():
            return False
        if folder is None:
            folder = QFileDialog.getExistingDirectory(self, "Save generated code to folder")
            if not folder:
                return False
        for name, text in self.generated.files.items():
            Path(folder, name).write_text(text, encoding="utf-8")
        self.messages.setPlainText(f"Wrote {', '.join(self.generated.files)} to {folder}")
        return True

    def run_b2b(self) -> bool:
        from ecsim.model.sil import back_to_back
        doc = self._commit_to_root()
        ex_id = self.grade_ex.currentData()
        if not ex_id:
            return False
        spec = self.ctx.catalog.exercise(ex_id).spec
        dtype = self.code_dtype.currentText()
        tol = float(spec.get("b2b", {}).get("abs", 1e-9 if dtype == "double" else 1e-3))
        try:
            rep = back_to_back(doc.to_json(), spec, dtype, tol)
        except ModelError as exc:
            self.code_view.setPlainText(f"✗ {exc}")
            return False
        errs = rep.mil.errors + rep.sil.errors
        lines = [f"Back-to-back: model (MIL, double) vs generated C (SIL, {dtype}), tolerance {tol:g}", ""]
        lines += [f"✗ {e}" for e in errs]
        for cmp in rep.comparisons:
            lines.append(f"{'ok ' if cmp.passed else 'FAIL'} [{cmp.test}] {cmp.signal}: max |MIL − SIL| = {cmp.max_abs:.4g}"
                         + ("" if cmp.passed else f" at t = {cmp.at_t:.3f} s"))
        lines.append("")
        lines.append("EQUIVALENT within tolerance" if rep.equivalent and not errs else "NOT equivalent: investigate numerics")
        self.code_view.setPlainText("\n".join(lines))
        self.tabs.setCurrentIndex(3)
        self.last_b2b = rep
        return rep.equivalent and not errs

    def deploy(self) -> bool:
        from ecsim.model.deploy import deploy_and_run
        doc = self._commit_to_root()
        try:
            build, res = deploy_and_run(doc.model, self.deploy_profile.currentData(), dtype=self.code_dtype.currentText())
        except ModelError as exc:
            self.code_view.setPlainText(f"✗ {exc}")
            self.tabs.setCurrentIndex(3)
            return False
        if not build.ok:
            self.code_view.setPlainText(build.log)
            return False
        lines = [f"Generated code + integration wrapper deployed to {self.deploy_profile.currentText()}:", ""]
        ok = True
        for sc, out in res.items():
            if not out.ok:
                lines.append(f"✗ {sc}: {out.error}")
                ok = False
                continue
            r = out.result
            ok &= r.passed
            lines.append(f"{'✓' if r.passed else '✗'} {sc}: CPU load {r.cpu_load:.1%}, resets {len(r.resets)}")
            lines += [f"    {'ok ' if c.passed else 'FAIL'} {c.name}: {c.detail}" for c in r.checks]
        lines.append("\nOpen the Virtual ECU Lab to see the signals of hand-written firmware; this run uses the generated code.")
        self.code_view.setPlainText("\n".join(lines))
        self.tabs.setCurrentIndex(3)
        return ok

    # ---- document binding --------------------------------------------------------
    def _bind(self, doc: ModelDocument) -> None:
        self.doc = doc
        doc.listeners[:] = [self._on_changed]
        self._on_changed()

    def _on_changed(self) -> None:
        self.canvas.rebuild(self.doc.model)
        self.canvas.scene().selectionChanged.connect(self._selection_changed)
        self.step_box.blockSignals(True)
        self.step_box.setValue(float(self.doc.model.get("solver", {}).get("step", 0.01)))
        self.step_box.blockSignals(False)
        self.buttons["undo"].setEnabled(self.doc.can_undo)
        self.buttons["redo"].setEnabled(self.doc.can_redo)
        self.buttons["up"].setEnabled(bool(self.stack))
        path = [p.model["name"] for p, _ in self.stack] + [self.doc.model["name"]]
        self.crumbs.setText("  ›  ".join(path) + ("  •  modified" if self._root_doc().dirty else ""))
        self._refresh_inputs()
        if self.current_block and not any(b["id"] == self.current_block for b in self.doc.model["blocks"]):
            self.current_block = None
        self._show_props(self.current_block)

    def _root_doc(self) -> ModelDocument:
        return self.stack[0][0] if self.stack else self.doc

    def _refresh_inputs(self) -> None:
        names = [b.get("params", {}).get("name", b["id"]) for b in
                 sorted((b for b in self.doc.model["blocks"] if b["type"] == "Inport"),
                        key=lambda b: b["params"].get("port", 1))]
        old = {self.sim_inputs.item(r, 0).text(): self.sim_inputs.item(r, 1).text()
               for r in range(self.sim_inputs.rowCount()) if self.sim_inputs.item(r, 0) and self.sim_inputs.item(r, 1)}
        self.sim_inputs.setRowCount(len(names))
        for r, n in enumerate(names):
            it = QTableWidgetItem(n); it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            self.sim_inputs.setItem(r, 0, it)
            self.sim_inputs.setItem(r, 1, QTableWidgetItem(old.get(n, "step 0.1 0 1")))

    def _selection_changed(self) -> None:
        try:
            sel = [i for i in self.canvas.scene().selectedItems() if isinstance(i, BlockItem)]
        except RuntimeError:     # scene being rebuilt
            return
        self.current_block = sel[0].block_id if len(sel) == 1 else None
        self._show_props(self.current_block)

    def _show_props(self, bid: str | None) -> None:
        while self.form.rowCount():
            self.form.removeRow(0)
        self.prop_edits = {}
        self.apply_btn.setEnabled(bid is not None)
        if bid is None:
            self.prop_title.setText("Select one block to edit its parameters. Double-click a subsystem to open it.")
            return
        b = self.doc.block(bid)
        bt = LIB[b["type"]]
        self.prop_title.setText(f"<b>{b['type']}</b><br><span style='color:#5b6573'>{bt.simulink}</span>")
        self.prop_edits["__id__"] = QLineEdit(b["id"])
        self.form.addRow("Name", self.prop_edits["__id__"])
        params = dict(bt.defaults or {})
        params.update(b.get("params", {}))
        if b["type"] == "Chart":
            btn = QPushButton("Edit chart…")
            btn.clicked.connect(lambda: self.edit_chart(bid))
            self.form.addRow("states", btn)
        for k, v in params.items():
            if k in ("model", "chart"):
                continue
            e = QLineEdit(json.dumps(v) if isinstance(v, list) else (str(v) if not isinstance(v, float) else f"{v:g}"))
            self.form.addRow(k, e)
            self.prop_edits[k] = e
        if b["type"] not in ("Inport", "Outport", "Subsystem", "Terminator"):
            e = QLineEdit(f"{b.get('sample_time', -1):g}")
            self.form.addRow("sample time", e)
            self.prop_edits["__ts__"] = e

    def edit_chart(self, bid: str, dialog_factory=None) -> bool:
        """Open the chart editor; `dialog_factory` lets tests drive the dialog without exec()."""
        from ecsim.gui.chart_editor import ChartEditorDialog
        b = self.doc.block(bid)
        spec = b.get("params", {}).get("chart") or LIB["Chart"].defaults["chart"]
        dlg = (dialog_factory or ChartEditorDialog)(spec, self)
        if dialog_factory is None and not dlg.exec():
            return False
        if dlg.result_spec is None:
            return False
        return self._try(lambda: self.doc.set_param(bid, "chart", dlg.result_spec))

    def apply_props(self) -> None:
        bid = self.current_block
        if not bid:
            return
        b = self.doc.block(bid)
        # Read every field first: each document edit rebuilds this panel.
        texts = {k: e.text() for k, e in self.prop_edits.items()}

        def run():
            for k, text in texts.items():
                if k in ("__id__", "__ts__"):
                    continue
                cur = b.get("params", {}).get(k, (LIB[b["type"]].defaults or {}).get(k))
                val = coerce_param(b["type"], k, text)
                if val != cur:
                    self.doc.set_param(bid, k, val)
            if "__ts__" in texts:
                try:
                    ts = float(texts["__ts__"])
                except ValueError:
                    raise ModelError("sample time: number expected (-1 = inherited)") from None
                if ts != b.get("sample_time", -1):
                    self.doc.set_sample_time(bid, ts)
            new_id = texts["__id__"].strip()
            if new_id != bid:
                self.doc.rename_block(bid, new_id)
                self.current_block = new_id
        self._try(run)

    # ---- actions -------------------------------------------------------------------
    def _try(self, fn) -> bool:
        try:
            fn()
            return True
        except (ModelError, ValueError) as exc:
            self.messages.setPlainText(f"✗ {exc}")
            self.tabs.setCurrentIndex(0)
            return False

    def add_block(self, type_name: str, pos=None) -> str | None:
        if pos is None:
            c = self.canvas.mapToScene(self.canvas.viewport().rect().center())
            n = len(self.doc.model["blocks"])
            pos = (c.x() - BW / 2 + 20 * (n % 5), c.y() - BH / 2 + 20 * (n % 5))
        out = {}
        self._try(lambda: out.setdefault("id", self.doc.add_block(type_name, pos)))
        return out.get("id")

    def connect(self, src: str, dst: str) -> bool:
        return self._try(lambda: self.doc.connect(src, dst))

    def delete_selected(self) -> None:
        items = self.canvas.scene().selectedItems()
        wires = [w for w in items if isinstance(w, WireItem)]
        blocks = [b.block_id for b in items if isinstance(b, BlockItem)]
        for w in wires:
            self._try(lambda w=w: self.doc.disconnect(w.dst.ref))
        for bid in blocks:
            self._try(lambda bid=bid: self.doc.remove_block(bid))

    def group_selected(self) -> None:
        ids = [b.block_id for b in self.canvas.scene().selectedItems() if isinstance(b, BlockItem)]
        self._try(lambda: self.doc.group_into_subsystem(ids))

    def undo(self) -> None:
        label = self.doc.undo()
        if label:
            self.messages.setPlainText(f"Undone: {label}")

    def redo(self) -> None:
        label = self.doc.redo()
        if label:
            self.messages.setPlainText(f"Redone: {label}")

    def open_block(self, bid: str) -> None:
        b = self.doc.block(bid)
        if b["type"] != "Subsystem":
            return
        child = ModelDocument.from_json(json.dumps(b["params"]["model"]))
        child.model["name"] = bid
        self.stack.append((self.doc, bid))
        self.current_block = None
        self._bind(child)

    def go_up(self) -> None:
        if not self.stack:
            return
        parent, bid = self.stack.pop()
        inner = self.doc.model
        if inner != parent.block(bid)["params"]["model"]:
            ok = self._try(lambda: parent.set_subsystem_model(bid, inner))
            if not ok:
                self.stack.append((parent, bid))
                return
        self.current_block = None
        self._bind(parent)

    def _commit_to_root(self) -> ModelDocument:
        while self.stack:
            self.go_up()
        return self.doc

    def new_model(self) -> None:
        self.stack.clear()
        self.current_block = None
        self._bind(ModelDocument())
        self.messages.setPlainText("New model. Drag blocks from the palette, or double-click them.")

    def load_document(self, doc: ModelDocument) -> None:
        self.stack.clear()
        self.current_block = None
        self._bind(doc)

    def open_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open ECSim model", "", "ECSim models (*.json)")
        if path:
            self._try(lambda: self.load_document(ModelDocument.from_json(Path(path).read_text(encoding="utf-8"), path)))

    def save(self) -> None:
        doc = self._commit_to_root()
        path = doc.path
        if not path:
            path, _ = QFileDialog.getSaveFileName(self, "Save model", f"{doc.model['name']}.json", "ECSim models (*.json)")
            if not path:
                return
        self.save_to(path)

    def save_to(self, path: str) -> None:
        doc = self._commit_to_root()
        Path(path).write_text(doc.to_json(), encoding="utf-8")
        doc.path, doc.dirty = path, False
        self._on_changed()
        self.messages.setPlainText(f"Saved {path}")

    def import_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import Simulink model", "", "Simulink (*.slx)")
        if not path:
            return
        vars_path, _ = QFileDialog.getOpenFileName(self, "Optional: variables .m file (Cancel to skip)", "",
                                                   "MATLAB scripts (*.m)")
        self.import_slx(path, vars_path or None)

    def import_slx(self, path: str, vars_path: str | None = None) -> bool:
        def run():
            variables = parse_m_variables(Path(vars_path).read_text(encoding="utf-8")) if vars_path else {}
            model, warnings = import_slx(path, variables)
            self.load_document(ModelDocument.from_json(json.dumps(model)))
            self.messages.setPlainText(f"Imported {path}\n" + "\n".join(f"warning: {w}" for w in warnings))
        return self._try(run)

    def export_dialog(self) -> None:
        doc = self._commit_to_root()
        path, _ = QFileDialog.getSaveFileName(self, "Export Simulink build script", f"build_{doc.model['name']}.m",
                                              "MATLAB script (*.m)")
        if path:
            Path(path).write_text(export_build_script(doc.model), encoding="utf-8")
            self.messages.setPlainText(f"Wrote {path}. Run it in MATLAB to create the model in Simulink.")

    def check_model(self) -> bool:
        doc = self._commit_to_root()
        try:
            c = doc.compile()
        except ModelError as exc:
            self.messages.setPlainText(f"✗ {exc}")
            self.tabs.setCurrentIndex(0)
            return False
        lines = [f"✓ Model '{c.name}' compiles: {len(c.blocks)} blocks, step {c.step:g} s",
                 f"  inports {c.inport_names}  outports {c.outport_names}",
                 "  execution order: " + " → ".join(c.order)]
        lines += [f"  warning: {w}" for w in c.warnings]
        from ecsim.model.guidelines import check_model
        findings = [f for f in check_model(doc.model) if f.rule not in ("ECS-SIG-01", "ECS-RT-01")]
        lines += ["", f"Modelling guidelines: {len(findings)} finding(s)"] + [f"  {f}" for f in findings]
        self.messages.setPlainText("\n".join(lines))
        self.tabs.setCurrentIndex(0)
        return True

    def simulate(self) -> bool:
        doc = self._commit_to_root()
        try:
            c = doc.compile()
            sigs = {self.sim_inputs.item(r, 0).text(): parse_signal(self.sim_inputs.item(r, 1).text())
                    for r in range(self.sim_inputs.rowCount())}
            dur = self.sim_dur.value()
            n = int(round(dur / c.step)) + 1
            sim = Simulator(c)
            t, series = [], {name: [] for name in c.outport_names}
            closed = self.sim_closed.isChecked()
            if closed:
                if len(c.inports) != 1 or len(c.outports) < 1:
                    raise ModelError("closed loop needs exactly 1 inport (speed) and an outport (volts)")
                from ecsim.model.mil import V_MAX, V_MIN, DiscreteMotor
                from ecsim.plants.dc_motor import RPM_PER_RADS
                plant, w = DiscreteMotor.at(c.step), 0.0
                series["speed_rpm"] = []
            for k in range(n):
                tk = k * c.step
                if closed:
                    rpm = w * RPM_PER_RADS
                    y = sim.step([rpm])
                    series["speed_rpm"].append(rpm)
                    w = plant.a * w + plant.bv * min(max(y[0], V_MIN), V_MAX)
                else:
                    y = sim.step([signal_value(sigs[name], tk) for name in c.inport_names])
                t.append(tk * 1000)
                for name, v in zip(c.outport_names, y):
                    series[name].append(v)
        except ModelError as exc:
            self.messages.setPlainText(f"✗ {exc}")
            self.tabs.setCurrentIndex(0)
            return False
        palette = ["#2f6fde", "#1b8a5a", "#a1460d", "#8a2b8f"]
        self.sim_plot.set_data(t, [Series(k, v, palette[i % 4]) for i, (k, v) in enumerate(series.items())])
        self.last_sim = (t, series)
        self.tabs.setCurrentIndex(1)
        return True

    def submit_to_exercise(self) -> None:
        doc = self._commit_to_root()
        ex_id = self.grade_ex.currentData()
        if not ex_id:
            return
        rec = self.ctx.learning.submit(self.learner_id, ex_id, doc.to_json())
        head = "PASS" if rec.passed else "FAIL"
        if rec.id > 0 and rec.passed:
            head += " — " + ("independent evidence" if rec.independent else "recorded (not independent)")
        self.grade_out.setPlainText(f"{head}\n\n{rec.feedback}")
        self.tabs.setCurrentIndex(2)

    def refresh(self) -> None:
        pass
