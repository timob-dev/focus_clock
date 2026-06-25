from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import QApplication, QWidget


def build_window_flags() -> Qt.WindowType:
    """Always-on-top frameless window visible in the taskbar/Dock."""
    return (
        Qt.Window
        | Qt.FramelessWindowHint
        | Qt.WindowStaysOnTopHint
        | Qt.WindowMinimizeButtonHint
    )


def clamp_to_available_geometry(
    pos: QPoint, width: int, height: int
) -> QPoint:
    """Keep the window fully inside the primary screen's available area."""
    app = QApplication.instance()
    if app is None:
        return pos

    screen = app.primaryScreen()
    if screen is None:
        return pos

    available = screen.availableGeometry()
    max_x = available.right() - width + 1
    max_y = available.bottom() - height + 1
    x = max(available.left(), min(pos.x(), max_x))
    y = max(available.top(), min(pos.y(), max_y))
    return QPoint(x, y)


def load_window_position(
    settings, default: QPoint, width: int, height: int
) -> QPoint:
    """Restore saved window position or return default clamped to screen."""
    if settings.contains("window_x") and settings.contains("window_y"):
        pos = QPoint(
            int(settings.value("window_x")),
            int(settings.value("window_y")),
        )
    else:
        pos = default
    return clamp_to_available_geometry(pos, width, height)


def save_window_position(settings, window: QWidget) -> None:
    pos = window.frameGeometry().topLeft()
    settings.setValue("window_x", pos.x())
    settings.setValue("window_y", pos.y())


def ensure_on_top(window: QWidget) -> None:
    """Re-apply always-on-top flags after focus loss or restore."""
    flags = build_window_flags()
    was_visible = window.isVisible()
    window.setWindowFlags(flags)
    if was_visible:
        window.show()
    window.raise_()
    window.activateWindow()
