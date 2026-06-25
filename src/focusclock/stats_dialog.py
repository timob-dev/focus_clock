from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QPalette
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QLabel,
    QVBoxLayout
    )

from .util import format_hm


class StatsDialog(QDialog):
    def __init__(
        self, parent, focus_work_sec: int, paused_sec: int,
        microbreak_sec: int, total_open_sec: int
        ):
        super().__init__(parent)
        self.setWindowTitle("Statistics")
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)

        font_family = QApplication.font().family()

        running = int(total_open_sec)  # only running if running==True
        paused = int(paused_sec)  # manual pause
        den = max(1, running + paused)
        eff = int(round((running / den) * 100))

        text = (
            f"Focus Active: {format_hm(focus_work_sec)}\n"
            f"Paused: {format_hm(paused_sec)}\n"
            f"Screen Break: {format_hm(microbreak_sec)}\n"
            f"\nEfficiency: {eff}%"
        )

        lbl = QLabel(text)
        lbl.setFont(QFont(font_family, 10))

        app = QApplication.instance()
        bg = app.palette().color(QPalette.Window)
        dark = bg.lightness() < 128
        lbl.setStyleSheet("color: #eee;" if dark else "color: #111;")

        btns = QDialogButtonBox(QDialogButtonBox.Ok)
        btns.accepted.connect(self.accept)

        lay = QVBoxLayout(self)
        lay.addWidget(lbl)
        lay.addWidget(btns)
