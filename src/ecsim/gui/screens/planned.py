from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ecsim.gui.widgets import card, heading


class PlannedScreen(QWidget):
    """Honest placeholder: names the phase that delivers this screen and its scope."""

    def __init__(self, title: str, phase: int, scope: list[str]) -> None:
        super().__init__()
        self.phase = phase
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.addWidget(heading(title))
        status = QLabel(f"Not yet implemented — scheduled for roadmap Phase {phase}.")
        status.setObjectName("banner")
        lay.addWidget(status)
        body = QLabel("Planned scope:\n" + "\n".join(f"  •  {s}" for s in scope))
        body.setWordWrap(True)
        lay.addWidget(card(heading("What this screen will do", "h2"), body))
        lay.addStretch(1)

    def refresh(self) -> None:
        pass
