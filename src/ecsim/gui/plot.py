"""Minimal time-series plot (QPainter). No external plotting dependency."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget


@dataclass
class Series:
    name: str
    values: list[float]
    color: str
    dashed: bool = False


@dataclass
class Marker:
    t: float
    label: str
    color: str


class SignalPlot(QWidget):
    def __init__(self, title: str, unit: str, parent=None) -> None:
        super().__init__(parent)
        self.title, self.unit = title, unit
        self.t: list[float] = []
        self.series: list[Series] = []
        self.markers: list[Marker] = []
        self.band: tuple[float, float] | None = None
        self.y_range: tuple[float, float] | None = None
        self.setMinimumHeight(170)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_data(self, t, series, markers=(), band=None, y_range=None) -> None:
        self.t, self.series, self.markers = list(t), list(series), list(markers)
        self.band, self.y_range = band, y_range
        self.update()

    def _ranges(self):
        if not self.t:
            return 0.0, 1.0, 0.0, 1.0
        x0, x1 = self.t[0], self.t[-1] if self.t[-1] > self.t[0] else self.t[0] + 1
        if self.y_range:
            y0, y1 = self.y_range
        else:
            vals = [v for s in self.series for v in s.values] or [0.0, 1.0]
            y0, y1 = min(vals), max(vals)
            pad = (y1 - y0) * 0.08 or 1.0
            y0, y1 = y0 - pad, y1 + pad
        return x0, x1, y0, y1

    def paintEvent(self, _ev) -> None:  # noqa: N802 - Qt API
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("white"))
        m_l, m_r, m_t, m_b = 58, 12, 40, 26   # title row + legend row above the plot area
        area = QRectF(m_l, m_t, max(10, self.width() - m_l - m_r), max(10, self.height() - m_t - m_b))
        x0, x1, y0, y1 = self._ranges()

        def X(x):
            return area.left() + (x - x0) / (x1 - x0) * area.width()

        def Y(y):
            return area.bottom() - (y - y0) / (y1 - y0) * area.height()

        p.setPen(QColor("#1d2430"))
        p.drawText(QRectF(m_l, 2, area.width(), 20), Qt.AlignLeft | Qt.AlignVCenter, f"{self.title} [{self.unit}]")
        grid = QPen(QColor("#e6e9ee"))
        for k in range(5):
            yv = y0 + (y1 - y0) * k / 4
            p.setPen(grid)
            p.drawLine(QPointF(area.left(), Y(yv)), QPointF(area.right(), Y(yv)))
            p.setPen(QColor("#5b6573"))
            p.drawText(QRectF(0, Y(yv) - 8, m_l - 6, 16), Qt.AlignRight | Qt.AlignVCenter, f"{yv:.0f}")
        nx = max(2, min(6, int(area.width() // 80)))       # avoid crowded labels in narrow plots
        for k in range(nx + 1):
            xv = x0 + (x1 - x0) * k / nx
            p.setPen(grid)
            p.drawLine(QPointF(X(xv), area.top()), QPointF(X(xv), area.bottom()))
            p.setPen(QColor("#5b6573"))
            p.drawText(QRectF(X(xv) - 30, area.bottom() + 4, 60, 16), Qt.AlignCenter, f"{xv/1000:.1f} s")
        if self.band:
            p.fillRect(QRectF(area.left(), Y(self.band[1]), area.width(), Y(self.band[0]) - Y(self.band[1])),
                       QColor(47, 111, 222, 28))
        for i, mk in enumerate(sorted(self.markers, key=lambda m: m.t)):
            pen = QPen(QColor(mk.color)); pen.setStyle(Qt.DashLine)
            p.setPen(pen)
            p.drawLine(QPointF(X(mk.t), area.top()), QPointF(X(mk.t), area.bottom()))
            p.drawText(QPointF(X(mk.t) + 3, area.top() + 12 + 14 * (i % 3)), mk.label)  # staggered labels
        p.setClipRect(area)
        for s in self.series:
            if not s.values:
                continue
            path = QPainterPath()
            step = max(1, len(self.t) // 1500)
            path.moveTo(X(self.t[0]), Y(s.values[0]))
            for i in range(step, len(self.t), step):
                path.lineTo(X(self.t[i]), Y(s.values[i]))
            pen = QPen(QColor(s.color), 1.6)
            if s.dashed:
                pen.setStyle(Qt.DashLine)
            p.setPen(pen)
            p.drawPath(path)
        p.setClipping(False)
        lx = area.left()
        for s in self.series:                       # legend on its own row under the title
            p.setPen(QPen(QColor(s.color), 2))
            p.drawLine(QPointF(lx, 30), QPointF(lx + 16, 30))
            p.setPen(QColor("#1d2430"))
            p.drawText(QPointF(lx + 20, 34), s.name)
            lx += p.fontMetrics().horizontalAdvance(s.name) + 34
        p.end()
