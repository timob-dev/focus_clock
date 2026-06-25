from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import QApplication, QWidget

MIN_VISIBLE_PX = 32


def build_window_flags() -> Qt.WindowType:
    """Always-on-top frameless window visible in the taskbar/Dock."""
    return (
        Qt.Window
        | Qt.FramelessWindowHint
        | Qt.WindowStaysOnTopHint
        | Qt.WindowMinimizeButtonHint
    )


def virtual_desktop_rect() -> QRect:
    """Union of all monitor geometries (includes taskbar/dock areas)."""
    app = QApplication.instance()
    if app is None:
        return QRect()

    rect = QRect()
    for screen in app.screens():
        rect = rect.united(screen.geometry())
    return rect


def clamp_to_virtual_desktop(
    pos: QPoint, width: int, height: int
) -> QPoint:
    """Soft-clamp position so the window stays partially on-screen."""
    desktop = virtual_desktop_rect()
    if desktop.isNull():
        return pos

    margin = MIN_VISIBLE_PX
    min_x = desktop.left() - width + margin
    max_x = desktop.right() - margin + 1
    min_y = desktop.top() - height + margin
    max_y = desktop.bottom() - margin + 1

    x = max(min_x, min(pos.x(), max_x))
    y = max(min_y, min(pos.y(), max_y))
    return QPoint(x, y)


def load_window_position(
    settings, default: QPoint, width: int, height: int
) -> QPoint:
    """Restore saved window position or return default clamped to desktop."""
    if settings.contains("window_x") and settings.contains("window_y"):
        pos = QPoint(
            int(settings.value("window_x")),
            int(settings.value("window_y")),
        )
    else:
        pos = default
    return clamp_to_virtual_desktop(pos, width, height)


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
