from __future__ import annotations

import re

from PySide6.QtGui import QColor, QFont, QFontDatabase, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import QFrame, QLabel, QPlainTextEdit, QVBoxLayout, QWidget

THEME = """
QWidget { font-size: 13px; color: #1d2430; background: #f5f6f8; }
QListWidget#nav { background: #1f2a37; color: #d8dee6; border: none; padding-top: 8px; }
QListWidget#nav::item { padding: 9px 14px; }
QListWidget#nav::item:selected { background: #2f6fde; color: white; }
QLabel#h1 { font-size: 20px; font-weight: 600; background: transparent; }
QLabel#h2 { font-size: 15px; font-weight: 600; background: transparent; }
QLabel#muted { color: #5b6573; background: transparent; }
QLabel#banner { background: #fff4d6; color: #5c4400; border: 1px solid #f0d58a; padding: 6px 10px; border-radius: 4px; }
QFrame#card { background: white; border: 1px solid #dde1e7; border-radius: 6px; }
QFrame#card QLabel { background: transparent; }
QPushButton { background: #2f6fde; color: white; border: none; padding: 6px 14px; border-radius: 4px; }
QPushButton:disabled { background: #b9c3d3; }
QPushButton#secondary { background: #e4e8ee; color: #1d2430; }
QPlainTextEdit, QTextBrowser, QLineEdit, QTreeWidget, QTableWidget, QComboBox {
    background: white; border: 1px solid #d0d5dd; border-radius: 4px; }
QHeaderView::section { background: #eef1f5; border: none; padding: 4px; font-weight: 600; }
"""


def heading(text: str, kind: str = "h1") -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName(kind)
    lbl.setWordWrap(True)
    return lbl


def card(*widgets: QWidget) -> QFrame:
    f = QFrame()
    f.setObjectName("card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(14, 12, 14, 12)
    for w in widgets:
        lay.addWidget(w)
    return f


def mono_font() -> QFont:
    f = QFontDatabase.systemFont(QFontDatabase.FixedFont)
    f.setPointSize(max(f.pointSize(), 10))
    return f


class CHighlighter(QSyntaxHighlighter):
    KEYWORDS = (
        "if else for while do return switch case default break continue struct typedef "
        "const volatile static extern void float double int char unsigned signed sizeof "
        "uint8_t uint16_t uint32_t uint64_t int8_t int16_t int32_t int64_t bool NULL"
    ).split()

    def __init__(self, doc) -> None:
        super().__init__(doc)
        self.rules: list[tuple[re.Pattern, QTextCharFormat]] = []
        kw = QTextCharFormat(); kw.setForeground(QColor("#1f4fb3")); kw.setFontWeight(QFont.Bold)
        num = QTextCharFormat(); num.setForeground(QColor("#a1460d"))
        com = QTextCharFormat(); com.setForeground(QColor("#6a7a5a")); com.setFontItalic(True)
        pre = QTextCharFormat(); pre.setForeground(QColor("#8a2b8f"))
        self.rules.append((re.compile(r"\b(" + "|".join(self.KEYWORDS) + r")\b"), kw))
        self.rules.append((re.compile(r"\b(0x[0-9A-Fa-f]+|\d+\.?\d*[fFuUlL]*)\b"), num))
        self.rules.append((re.compile(r"^\s*#\w+"), pre))
        self.rules.append((re.compile(r"//.*$|/\*.*?\*/"), com))

    def highlightBlock(self, text: str) -> None:  # noqa: N802 - Qt API
        for pat, fmt in self.rules:
            for m in pat.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), fmt)


class CodeEditor(QPlainTextEdit):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFont(mono_font())
        self.setTabStopDistance(4 * self.fontMetrics().horizontalAdvance(" "))
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self._hl = CHighlighter(self.document())
