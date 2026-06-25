import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication


def resource_path(name: str) -> Path:
    """Resolve bundled assets for dev and PyInstaller builds."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = Path(sys._MEIPASS)
    else:
        base = Path(__file__).resolve().parent / "assets"
    return base / name


def app_data_dir() -> Path:
    base = Path.home() / "Documents" / "FocusClock"
    base.mkdir(parents=True, exist_ok=True)
    return base


def format_time_mmss(sec: int) -> str:
    sec = max(0, int(sec))
    m = sec // 60
    s = sec % 60
    return f"{m:02d}:{s:02d}"


def format_hm(sec: int) -> str:
    sec = max(0, int(sec))
    h = sec // 3600
    m = (sec % 3600) // 60
    return f"{h:d}:{m:02d}"


def beep():
    if sys.platform == "win32":
        import winsound
        winsound.MessageBeep(winsound.MB_ICONINFORMATION)
    else:
        QApplication.beep()


def tint_icon(
    icon: QIcon, size: int = 18, color: QColor = QColor("white")
    ) -> QIcon:
    pm = icon.pixmap(size, size)
    if pm.isNull():
        return icon

    tinted = QPixmap(pm.size())
    tinted.fill(Qt.transparent)

    painter = QPainter(tinted)
    painter.setCompositionMode(QPainter.CompositionMode_Source)
    painter.drawPixmap(0, 0, pm)
    painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
    painter.fillRect(tinted.rect(), color)
    painter.end()

    return QIcon(tinted)


def text_icon(
    symbol: str,
    size: int = 18,
    color: QColor = QColor("white"),
    font_family: str | None = None,
) -> QIcon:
    """Render a text/emoji symbol centered on a transparent pixmap."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)

    painter = QPainter(pm)
    font = QFont(font_family or QApplication.font().family())
    font.setPixelSize(max(10, int(size * 0.85)))
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(color)
    painter.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, symbol)
    painter.end()

    return QIcon(pm)
