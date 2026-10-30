"""CAN Network Analyzer: DBC browser, trace decoding (candump, J1939), UDS console with ISO-TP view."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPlainTextEdit,
                               QPushButton, QTableWidget, QTableWidgetItem, QTabWidget, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from ecsim.app.services import AppContext
from ecsim.comms.dbc import Database, DbcError, parse_dbc
from ecsim.comms.trace import decode_frames, parse_candump
from ecsim.comms.uds import SESSIONS, UdsServer, describe, isotp_frames, parse_hex, training_key
from ecsim.gui.widgets import heading, mono_font

SAMPLE_DBC = Path(__file__).resolve().parents[2] / "comms" / "motor_ecu.dbc"
SAMPLE_TRACE = """(0.000) can0 100#7017726906FF0000
(0.010) can0 100#7017827906FF0000
(0.012) can0 200#09C4800000000000
(0.020) can0 100#7017927906FF0000
(0.030) can0 0CF004FE#FFFFFF8016FFFFFF
(0.040) can0 18FEF100#FF0080FFFFFFFFFF
"""


class CanAnalyzerScreen(QWidget):
    def __init__(self, ctx: AppContext, learner_id: int) -> None:
        super().__init__()
        self.ctx = ctx
        self.db: Database | None = None
        self.server = UdsServer()
        self.seed: int | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.addWidget(heading("CAN Network Analyzer"))
        bar = QHBoxLayout()
        self.db_label = QLabel()
        ld = QPushButton("Load DBC…"); ld.setObjectName("secondary"); ld.clicked.connect(self._load_dbc_dialog)
        sample = QPushButton("Use sample motor ECU DBC"); sample.setObjectName("secondary"); sample.clicked.connect(self.load_sample)
        bar.addWidget(self.db_label, 1); bar.addWidget(sample); bar.addWidget(ld)
        lay.addLayout(bar)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._trace_tab(), "Trace")
        self.tabs.addTab(self._dbc_tab(), "Database")
        self.tabs.addTab(self._uds_tab(), "UDS console")
        lay.addWidget(self.tabs, 1)
        self.load_sample()

    # ---- DBC ------------------------------------------------------------------------
    def _dbc_tab(self) -> QWidget:
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Message / signal", "ID / layout", "Scaling", "Unit", "Comment"])
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeToContents)
        return self.tree

    def load_dbc_text(self, text: str, name: str) -> bool:
        try:
            self.db = parse_dbc(text)
        except DbcError as exc:
            self.db_label.setText(f"✗ {name}: {exc}")
            return False
        self.db_label.setText(f"DBC: {name} — {len(self.db.messages)} messages, nodes {', '.join(self.db.nodes)}")
        self.tree.clear()
        for m in self.db.messages:
            mi = QTreeWidgetItem([m.name, f"0x{m.frame_id:X}{' (ext)' if m.extended else ''} · {m.length} B · {m.sender}", "", "", m.comment])
            for s in m.signals:
                order = "Intel" if s.little_endian else "Motorola"
                vals = ", ".join(f"{k}={v}" for k, v in s.values.items())
                mi.addChild(QTreeWidgetItem([s.name, f"{s.start}|{s.length} {order}{' signed' if s.signed else ''}",
                                             f"×{s.factor:g} +{s.offset:g}", s.unit, (s.comment + ("  " + vals if vals else "")).strip()]))
            self.tree.addTopLevelItem(mi)
        self.tree.expandAll()
        return True

    def load_sample(self) -> bool:
        return self.load_dbc_text(SAMPLE_DBC.read_text(encoding="utf-8"), "motor_ecu.dbc (sample)")

    def _load_dbc_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load DBC", "", "DBC (*.dbc)")
        if path:
            self.load_dbc_text(Path(path).read_text(encoding="utf-8", errors="replace"), Path(path).name)

    # ---- trace ----------------------------------------------------------------------
    def _trace_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        row = QHBoxLayout()
        self.j1939 = QCheckBox("Decode 29-bit IDs as J1939 (PGN, source address)"); self.j1939.setChecked(True)
        dec = QPushButton("Decode"); dec.clicked.connect(self.decode)
        op = QPushButton("Open candump log…"); op.setObjectName("secondary"); op.clicked.connect(self._open_trace)
        self.filter = QLineEdit(); self.filter.setPlaceholderText("filter: ID or name contains…")
        self.filter.textChanged.connect(self.decode)
        for x in (self.j1939, self.filter, op, dec):
            row.addWidget(x)
        v.addLayout(row)
        self.trace_in = QPlainTextEdit(SAMPLE_TRACE); self.trace_in.setFont(mono_font()); self.trace_in.setMaximumHeight(140)
        v.addWidget(self.trace_in)
        self.trace_table = QTableWidget(0, 6)
        self.trace_table.setHorizontalHeaderLabels(["Time [s]", "ID", "DLC", "Data", "Message", "Signals"])
        self.trace_table.verticalHeader().setVisible(False)
        self.trace_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.trace_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        v.addWidget(self.trace_table, 1)
        self.trace_msg = QLabel()
        v.addWidget(self.trace_msg)
        return w

    def _open_trace(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "candump log", "", "Logs (*.log *.txt);;All (*)")
        if path:
            self.trace_in.setPlainText(Path(path).read_text(encoding="utf-8", errors="replace"))
            self.decode()

    def decode(self) -> int:
        try:
            frames = parse_candump(self.trace_in.toPlainText())
        except ValueError as exc:
            self.trace_msg.setText(f"✗ {exc}")
            return 0
        rows = decode_frames(frames, self.db, self.j1939.isChecked())
        flt = self.filter.text().strip().lower()
        if flt:
            rows = [r for r in rows if flt in r["id"].lower() or flt in r["name"].lower()]
        self.trace_table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            for j, k in enumerate(("t", "id", "dlc", "data", "name", "signals")):
                self.trace_table.setItem(i, j, QTableWidgetItem(f"{r[k]:.3f}" if k == "t" else str(r[k])))
        self.trace_msg.setText(f"{len(rows)} of {len(frames)} frame(s) shown")
        return len(rows)

    # ---- UDS ------------------------------------------------------------------------
    def _uds_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        row = QHBoxLayout()
        self.req = QLineEdit(); self.req.setPlaceholderText("request bytes, e.g. 22 F1 90   ($KEY = key for the last seed)")
        self.req.returnPressed.connect(self.send)
        send = QPushButton("Send"); send.clicked.connect(self.send)
        tp = QPushButton("Tester present"); tp.setObjectName("secondary"); tp.clicked.connect(lambda: self.send("3E 00"))
        wait = QPushButton("Wait 6 s"); wait.setObjectName("secondary"); wait.clicked.connect(lambda: self.wait(6000))
        rst = QPushButton("Power-cycle ECU"); rst.setObjectName("secondary"); rst.clicked.connect(self.power_cycle)
        for x in (self.req, send, tp, wait, rst):
            row.addWidget(x)
        v.addLayout(row)
        self.state = QLabel(); v.addWidget(self.state)
        self.uds_log = QPlainTextEdit(); self.uds_log.setReadOnly(True); self.uds_log.setFont(mono_font())
        v.addWidget(self.uds_log, 1)
        self._state()
        return w

    def _state(self) -> None:
        s = self.server
        self.state.setText(f"Session: {SESSIONS[s.session]} (0x{s.session:02X}) · security: {'unlocked' if s.unlocked else 'locked'} · "
                           f"DTCs stored: {len(s.dtcs)} · t = {s.now_ms / 1000:.2f} s")

    def send(self, text: str | None = None) -> str:
        text = (text if text is not None else self.req.text()).strip()
        if "$KEY" in text.upper():
            if self.seed is None:
                self.uds_log.appendPlainText("✗ no seed yet: send 27 01 first")
                return ""
            k = training_key(self.seed)
            text = text.upper().replace("$KEY", f"{k >> 8:02X} {k & 0xFF:02X}")
        try:
            req = parse_hex(text)
        except ValueError as exc:
            self.uds_log.appendPlainText(f"✗ {exc}")
            return ""
        self.server.tick(10)
        resp = self.server.request(req)
        if resp and resp[:2] == b"\x67\x01" and len(resp) == 4:
            self.seed = (resp[2] << 8) | resp[3]
        out = f"→ {req.hex(' ').upper()}\n← {describe(resp)}"
        if resp and len(resp) > 7:
            out += "\n   ISO-TP frames: " + " | ".join(f.hex(" ").upper() for f in isotp_frames(resp))
        self.uds_log.appendPlainText(out)
        self._state()
        return describe(resp)

    def wait(self, ms: int) -> None:
        before = self.server.session
        self.server.tick(ms)
        note = " (S3 timeout: back to default session)" if before != self.server.session else ""
        self.uds_log.appendPlainText(f"… {ms} ms without requests{note}")
        self._state()

    def power_cycle(self) -> None:
        self.server, self.seed = UdsServer(), None
        self.uds_log.appendPlainText("— ECU power-cycled —")
        self._state()

    def refresh(self) -> None:
        pass
