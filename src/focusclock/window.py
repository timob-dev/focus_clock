from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QSettings, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QCursor, QFont, QPalette
from PySide6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QHBoxLayout,
    QLabel, QMenu, QMessageBox, QPushButton, QStyle, QSystemTrayIcon,
    QVBoxLayout, QWidget,
)

from .logic import ClockState, FocusClockLogic
from .settings_dialog import SettingsDialog
from .stats_dialog import StatsDialog
from .util import (
    app_data_dir,
    beep,
    format_hm,
    format_time_mmss,
    text_icon,
    tint_icon,
)
from .window_manager import (
    build_window_flags,
    clamp_to_virtual_desktop,
    ensure_on_top,
    load_window_position,
    save_window_position,
)


def worklog_path() -> Path:
    return app_data_dir() / "worklog.csv"


def _read_last_day_from_csv(path) -> str:
    """Returns last DAY date in file as 'dd.mm.yyyy', or '' if none."""
    try:
        if not path.exists():
            return ""
        last_day = ""
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split(";")
                if len(parts) >= 2 and parts[0] == "DAY":
                    last_day = parts[1].strip()
        return last_day
    except Exception:
        return ""


def append_to_worklog_csv(rows: list[list[str]]) -> None:
    path = worklog_path()
    file_exists = path.exists()

    try:
        with open(path, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            if not file_exists:
                w.writerow(["Datum", "Beginn", "Ende", "Stunden"])

            for r in rows:
                w.writerow(r)
    except OSError as exc:
        QMessageBox.warning(
            None,
            "FocusClock",
            f"Could not write worklog CSV:\n{exc}",
        )


class FocusClockWindow(QWidget):
    WINDOW_WIDTH = 200
    WINDOW_HEIGHT = 200
    CTRL_BTN_W = 40
    CTRL_BTN_H = 32
    TITLE_BTN_SIZE = 28
    TITLE_ICON_BTN_SIZE = 26

    def _configure_title_button(
        self, btn: QPushButton, label: str, tooltip: str, *, emoji: bool = True
    ) -> None:
        btn.setObjectName("titleButton")
        btn.setText(label)
        btn.setToolTip(tooltip)
        btn.setFixedSize(self.TITLE_BTN_SIZE, self.TITLE_BTN_SIZE)
        if emoji:
            btn.setFont(QFont(self._ui_font.family(), 11))

    def __init__(self):
        super().__init__()

        self._ui_ready = False
        self._icon_color = QColor("#d0d0d0")
        self._theme_subtle = "#777"
        self._ui_font = QFont(QApplication.font().family())

        self.setWindowFlags(build_window_flags())

        # ---------- Settings store ----------
        self.qs = QSettings("FocusClock", "FocusClockApp")

        # ---------- Load persistent config ----------
        focus_min = int(self.qs.value("focus_min", 50))
        break_min = int(self.qs.value("break_min", 10))
        micro_sec = int(self.qs.value("micro_sec", 60))
        goal = int(self.qs.value("session_goal", 7))
        screen_breaks_enabled = bool(int(self.qs.value("screen_breaks_enabled", 1)))

        # ---------- Load runtime state ----------
        mode = self.qs.value("mode", "focus")
        remaining = int(
            self.qs.value(
                "remaining",
                focus_min * 60 if mode == "focus" else break_min * 60
                )
            )

        completed_units = int(self.qs.value("completed_units", 0))
        microbreak_active = bool(int(self.qs.value("microbreak_active", 0)))
        microbreak_remaining = int(self.qs.value("microbreak_remaining", 0))
        after_micro = self.qs.value("after_micro", "")
        finished = bool(int(self.qs.value("finished", 0)))

        total_open_sec = int(self.qs.value("total_open_sec", 0))
        paused_sec = int(self.qs.value("paused_sec", 0))
        microbreak_sec = int(self.qs.value("microbreak_sec", 0))
        focus_work_sec = int(self.qs.value("focus_work_sec", 0))

        # ---------- Build state + logic ----------
        state = ClockState(
            focus_min=focus_min,
            break_min=break_min,
            micro_sec=micro_sec,
            session_goal=goal,
            screen_breaks_enabled=screen_breaks_enabled,
            mode=mode,
            remaining=remaining,
            completed_units=completed_units,
            microbreak_active=microbreak_active,
            microbreak_remaining=microbreak_remaining,
            after_micro=after_micro,
            finished=finished,
            running=False,  # start paused
            total_open_sec=total_open_sec,
            paused_sec=paused_sec,
            microbreak_sec=microbreak_sec,
            focus_work_sec=focus_work_sec,
            )

        self.logic = FocusClockLogic(
            state=state, on_change=self.update_ui, on_beep=beep
            )
        self.logic.reconcile_state_on_load()

        # ---------- Window flags / style ----------
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        self.wrapper = QWidget(self)
        self.wrapper.setObjectName("wrapper")

        # ---------- Tray ----------
        tray_icon = QApplication.instance().windowIcon()
        self.tray = QSystemTrayIcon(tray_icon, self)  # parent setzen

        self.tray_menu = QMenu(self)  # <-- wichtig: self.* + parent

        self.restore_action = QAction("Open", self)
        self.quit_action = QAction("End", self)
        self.export_action = QAction("Export to CSV...", self)

        self.restore_action.triggered.connect(self.restore_window)
        self.quit_action.triggered.connect(QApplication.quit)
        self.export_action.triggered.connect(self.export_to_csv)

        self.tray_menu.addAction(self.restore_action)
        self.tray_menu.addAction(self.export_action)
        self.tray_menu.addSeparator()
        self.tray_menu.addAction(self.quit_action)

        self.tray.setContextMenu(self.tray_menu)
        self.tray.setToolTip("FocusClock")
        self.tray.activated.connect(self.on_tray_activated)
        self.tray.show()

        # ---------- Title bar ----------
        self.btn_settings = QPushButton()
        self.btn_stats = QPushButton()
        self._configure_title_button(self.btn_settings, "⚙", "Settings")
        self._configure_title_button(self.btn_stats, "📊", "Statistics")

        self.btn_lunch = QPushButton()
        self._configure_title_button(
            self.btn_lunch,
            "L",
            "Lunch Break (60 Min) — Shift+click: Worklog",
            emoji=False,
        )
        self.btn_lunch.setFont(QFont(self._ui_font.family(), 10, QFont.Bold))

        self.btn_min = QPushButton()
        self.btn_close = QPushButton()
        for btn in (self.btn_min, self.btn_close):
            btn.setObjectName("titleIconButton")
            btn.setFixedSize(self.TITLE_ICON_BTN_SIZE, self.TITLE_ICON_BTN_SIZE)
            btn.setIconSize(QSize(14, 14))

        self.btn_min.setToolTip("Minimize to tray")
        self.btn_close.setToolTip("Quit")

        top_row = QHBoxLayout()
        top_row.setContentsMargins(8, 6, 8, 0)
        top_row.setSpacing(6)
        top_row.addWidget(self.btn_settings)
        top_row.addWidget(self.btn_stats)
        top_row.addWidget(self.btn_lunch)
        top_row.addStretch(1)
        top_row.addWidget(self.btn_min)
        top_row.addWidget(self.btn_close)

        # ---------- Labels ----------
        self.focustime_label = QLabel("")
        self.focustime_label.setFont(QFont(self._ui_font.family(), 9))
        self.focustime_label.setAlignment(Qt.AlignCenter)
        self.focustime_label.setStyleSheet("color: #999;")

        self.mode_label = QLabel("")
        self.mode_label.setAlignment(Qt.AlignCenter)
        self.mode_label.setFont(QFont(self._ui_font.family(), 9, QFont.Bold))
        self.mode_label.setStyleSheet("color: #777;")

        self.timer_label = QLabel("")
        self.timer_label.setFont(QFont(self._ui_font.family(), 26, QFont.Bold))
        self.timer_label.setAlignment(Qt.AlignCenter)

        self.counter_label = QLabel("")
        self.counter_label.setFont(QFont(self._ui_font.family(), 10))
        self.counter_label.setAlignment(Qt.AlignCenter)

        # (Info label remains optional, but invisible by default)
        self.info_label = QLabel("")
        self.info_label.setFont(QFont(self._ui_font.family(), 9))
        self.info_label.setAlignment(Qt.AlignCenter)
        self.info_label.setStyleSheet("color: #999;")
        self.info_label.hide()

        # ---------- Controls ----------
        self.play_pause_btn = QPushButton()
        self.rewind_btn = QPushButton()
        self.skip_btn = QPushButton()
        self.reset_btn = QPushButton()

        for b in (
            self.play_pause_btn,
            self.rewind_btn,
            self.skip_btn,
            self.reset_btn,
        ):
            b.setObjectName("ctrlButton")
            b.setFixedSize(self.CTRL_BTN_W, self.CTRL_BTN_H)

        self.play_pause_btn.setIconSize(QSize(18, 18))
        self.rewind_btn.setIconSize(QSize(18, 18))
        self.skip_btn.setIconSize(QSize(18, 18))
        self.reset_btn.setIconSize(QSize(18, 18))

        self.play_pause_btn.setToolTip("Start / Pause")
        self.rewind_btn.setToolTip("Back (Phase)")
        self.skip_btn.setToolTip("Skip (Phase)")
        self.reset_btn.setToolTip("Reset")

        ctrl_row = QHBoxLayout()
        ctrl_row.setContentsMargins(8, 4, 8, 10)
        ctrl_row.setSpacing(6)
        ctrl_row.addWidget(self.play_pause_btn)
        ctrl_row.addWidget(self.rewind_btn)
        ctrl_row.addWidget(self.skip_btn)
        ctrl_row.addWidget(self.reset_btn)

        # ---------- Wrapper layout ----------
        wrap_layout = QVBoxLayout(self.wrapper)
        wrap_layout.setContentsMargins(0, 0, 0, 0)
        wrap_layout.setSpacing(7)
        wrap_layout.addLayout(top_row)
        wrap_layout.addWidget(self.focustime_label)
        wrap_layout.addWidget(self.mode_label)
        wrap_layout.addWidget(self.timer_label)
        wrap_layout.addWidget(self.counter_label)
        wrap_layout.addWidget(self.info_label)
        wrap_layout.addLayout(ctrl_row)

        # ---------- Timers ----------
        self.tick_timer = QTimer(self)
        self.tick_timer.setInterval(1000)
        self.tick_timer.timeout.connect(self.logic.on_tick)

        self.pause_count_timer = QTimer(self)
        self.pause_count_timer.setInterval(1000)
        self.pause_count_timer.timeout.connect(self.logic.on_pause_count_tick)
        self.pause_count_timer.start()

        # ---------- Signals ----------
        self.btn_close.clicked.connect(QApplication.quit)
        self.btn_min.clicked.connect(self.hide_to_tray)
        self.btn_settings.clicked.connect(self.open_settings)
        self.btn_stats.clicked.connect(self.open_stats)
        self.btn_lunch.clicked.connect(self.on_lunch_or_toggle_mode)

        self.play_pause_btn.clicked.connect(self.on_toggle_play_pause)
        self.rewind_btn.clicked.connect(self.logic.rewind_phase)
        self.skip_btn.clicked.connect(self.logic.skip_phase)
        self.reset_btn.clicked.connect(self.on_reset)

        # ---------- Dragging ----------
        self._dragging = False
        self._drag_offset = QPoint(0, 0)

        # Size + position
        self.resize(self.WINDOW_WIDTH, self.WINDOW_HEIGHT)
        default_pos = QPoint(100, 100)
        self.move(
            load_window_position(
                self.qs, default_pos, self.WINDOW_WIDTH, self.WINDOW_HEIGHT
            )
        )
        self.update_layout_geometry()

        self.on_top_timer = QTimer(self)
        self.on_top_timer.setInterval(5000)
        self.on_top_timer.timeout.connect(self._on_top_timer_tick)
        self.on_top_timer.start()

        # initial UI
        self._ui_ready = True
        self.apply_theme()
        self.update_ui()
        self._ensure_on_top()

    # ---------- Window management ----------
    def _ensure_on_top(self) -> None:
        ensure_on_top(self)

    def _on_top_timer_tick(self) -> None:
        if self.isVisible() and not self.isMinimized():
            self.raise_()

    def hide_to_tray(self) -> None:
        self.hide()
        if self.tray.isVisible():
            self.tray.showMessage(
                "FocusClock",
                "Minimized to tray — click the icon to restore.",
                QSystemTrayIcon.Information,
                3000,
            )

    def restore_window(self) -> None:
        self.showNormal()
        self.setWindowState(self.windowState() & ~Qt.WindowMinimized)
        self._ensure_on_top()

    # ---------- Geometry ----------
    def update_layout_geometry(self):
        self.wrapper.setGeometry(0, 0, self.width(), self.height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_layout_geometry()

    # ---------- UI update ----------
    def update_ui(self):
        s = self.logic.s

        # ---- WORKLOG UI ----
        if s.profile == "worklog":
            # Top progress + units hidden
            self.focustime_label.setText("")  # kein 0:00/5:50
            self.counter_label.setText("")  # keine Units

            # Mode label neutral (oder grün)
            if s.running:
                self.mode_label.setText("WORK")
                self.mode_label.setStyleSheet(f"color: {self._theme_subtle};")
                self.timer_label.setStyleSheet("color: #7CFC98;")
            else:
                self.mode_label.setText("PAUSED")
                self.mode_label.setStyleSheet(f"color: {self._theme_subtle};")
                self.timer_label.setStyleSheet("color: #ff6b6b;")

            # Stopwatch
            self.timer_label.setText(format_time_mmss(s.work_elapsed_sec))

            # Buttons: disable rewind/skip/lunch in worklog
            self.rewind_btn.hide()
            self.skip_btn.hide()
            # self.btn_lunch.setEnabled(False)

            # Play/Pause icon + tick timer
            if s.running:
                self.play_pause_btn.setIcon(
                    tint_icon(
                        self.style().standardIcon(QStyle.SP_MediaPause),
                        color=self._icon_color
                        )
                    )
                if not self.tick_timer.isActive():
                    self.tick_timer.start()
            else:
                self.play_pause_btn.setIcon(
                    tint_icon(
                        self.style().standardIcon(QStyle.SP_MediaPlay),
                        color=self._icon_color
                        )
                    )
                if self.tick_timer.isActive():
                    self.tick_timer.stop()

            return

        # progress
        self.rewind_btn.show()
        self.skip_btn.show()
        done, left, total, pct = self.logic.calc_focus_progress()
        self.focustime_label.setText(
            f"{format_hm(done)}/{format_hm(total)} ({pct}%)"
            )
        self.counter_label.setText(
            f"Unit: {self.logic.current_unit()}/{s.session_goal}"
            )

        # finished
        if s.finished:
            self.mode_label.setText("Finished")
            self.mode_label.setStyleSheet(f"color: {self._theme_subtle};")
            self.timer_label.setText("Finished")
            self.timer_label.setStyleSheet("color: #7CFC98;")
            self.play_pause_btn.setIcon(
                tint_icon(
                    self.style().standardIcon(QStyle.SP_MediaPlay),
                    color=self._icon_color
                    )
                )
            if self.tick_timer.isActive():
                self.tick_timer.stop()
            return

        # microbreak display (optional)
        if s.microbreak_active:
            self.mode_label.setText("SCREEN BREAK")
            self.mode_label.setStyleSheet(f"color: {self._theme_subtle};")
            self.timer_label.setText(
                format_time_mmss(max(1, s.microbreak_remaining))
                )
            self.timer_label.setStyleSheet("color: #FFD27C;")

            if s.running:
                self.play_pause_btn.setIcon(
                    tint_icon(
                        self.style().standardIcon(QStyle.SP_MediaPause),
                        color=self._icon_color
                        )
                    )
                if not self.tick_timer.isActive():
                    self.tick_timer.start()
            else:
                self.play_pause_btn.setIcon(
                    tint_icon(
                        self.style().standardIcon(QStyle.SP_MediaPlay),
                        color=self._icon_color
                        )
                    )
                if self.tick_timer.isActive():
                    self.tick_timer.stop()

            return

        # timer text
        self.timer_label.setText(format_time_mmss(s.remaining))

        # running visuals
        if not s.running:
            self.mode_label.setText("PAUSED")
            self.mode_label.setStyleSheet(f"color: {self._theme_subtle};")
            self.timer_label.setStyleSheet("color: #ff6b6b;")
            self.play_pause_btn.setIcon(
                tint_icon(
                    self.style().standardIcon(QStyle.SP_MediaPlay),
                    color=self._icon_color
                    )
                )
            if self.tick_timer.isActive():
                self.tick_timer.stop()
        else:
            if s.mode == "focus":
                self.mode_label.setText("FOCUS")
                self.timer_label.setStyleSheet("color: #7CFC98;")
            elif s.mode == "break":
                self.mode_label.setText("PAUSE")
                self.timer_label.setStyleSheet("color: #7CC7FF;")
            else:
                self.mode_label.setText("LUNCH")
                self.timer_label.setStyleSheet("color: #7CC7FF;")

            self.mode_label.setStyleSheet(f"color: {self._theme_subtle};")
            self.play_pause_btn.setIcon(
                tint_icon(
                    self.style().standardIcon(QStyle.SP_MediaPause),
                    color=self._icon_color
                    )
                )
            if not self.tick_timer.isActive():
                self.tick_timer.start()

    def changeEvent(self, event):
        super().changeEvent(event)
        if not getattr(self, "_ui_ready", False):
            return

        t = event.type()
        if t in (
                QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange
                ):
            self.apply_theme()
            self.update_ui()
            return

        if hasattr(
                QEvent.Type, "ThemeChange"
                ) and t == QEvent.Type.ThemeChange:
            self.apply_theme()
            self.update_ui()
            return

        if t == QEvent.Type.WindowStateChange:
            if self.isVisible() and not self.isMinimized():
                self._ensure_on_top()

    def apply_theme(self):
        app = QApplication.instance()
        pal = app.palette()
        bg = pal.color(QPalette.Window)
        dark = bg.lightness() < 128

        if dark:
            muted = "#999"
            subtle = "#777"
            title_hover = "#2a2a2a"
            wrapper_css = """
                QWidget#wrapper {
                    background: #1a1a1a;
                    border: 1px solid #333;
                    border-radius: 14px;
                }
                QLabel { color: #d0d0d0; }
                QPushButton {
                    background: transparent;
                    color: #d0d0d0;
                    border: none;
                    border-radius: 6px;
                }
                QPushButton:focus { outline: none; }
            """
            title_btn_css = f"""
                QPushButton#titleButton {{
                    padding: 0;
                    margin: 0;
                    min-width: {self.TITLE_BTN_SIZE}px;
                    max-width: {self.TITLE_BTN_SIZE}px;
                    min-height: {self.TITLE_BTN_SIZE}px;
                    max-height: {self.TITLE_BTN_SIZE}px;
                }}
                QPushButton#titleButton:hover {{
                    background: {title_hover};
                }}
                QPushButton#titleIconButton {{
                    padding: 0;
                    margin: 0;
                }}
                QPushButton#titleIconButton:hover {{
                    background: {title_hover};
                }}
            """
            ctrl_css = """
                QPushButton#ctrlButton {
                    background: #2a2a2a;
                    border: 1px solid #3a3a3a;
                    border-radius: 10px;
                    color: #d0d0d0;
                    padding: 0;
                }
                QPushButton#ctrlButton:hover {
                    background: #353535;
                    border: 1px solid #4a4a4a;
                }
                QPushButton#ctrlButton:pressed { background: #242424; }
                QPushButton#ctrlButton:focus { outline: none; }
            """
            icon_color = QColor("#d0d0d0")

        else:
            muted = "#444"
            subtle = "#666"
            title_hover = "#e9e9e9"
            wrapper_css = """
                QWidget#wrapper {
                    background: #f5f5f5;
                    border: 1px solid #cfcfcf;
                    border-radius: 14px;
                }
                QLabel { color: #111; }
                QPushButton {
                    background: transparent;
                    color: #111;
                    border: none;
                    border-radius: 6px;
                }
                QPushButton:focus { outline: none; }
            """
            title_btn_css = f"""
                QPushButton#titleButton {{
                    padding: 0;
                    margin: 0;
                    min-width: {self.TITLE_BTN_SIZE}px;
                    max-width: {self.TITLE_BTN_SIZE}px;
                    min-height: {self.TITLE_BTN_SIZE}px;
                    max-height: {self.TITLE_BTN_SIZE}px;
                }}
                QPushButton#titleButton:hover {{
                    background: {title_hover};
                }}
                QPushButton#titleIconButton {{
                    padding: 0;
                    margin: 0;
                }}
                QPushButton#titleIconButton:hover {{
                    background: {title_hover};
                }}
            """
            ctrl_css = """
                QPushButton#ctrlButton {
                    background: #ffffff;
                    border: 1px solid #cfcfcf;
                    border-radius: 10px;
                    color: #111;
                    padding: 0;
                }
                QPushButton#ctrlButton:hover {
                    background: #f0f0f0;
                    border: 1px solid #bdbdbd;
                }
                QPushButton#ctrlButton:pressed { background: #e2e2e2; }
                QPushButton#ctrlButton:focus { outline: none; }
            """
            icon_color = QColor("#111")

        self._icon_color = icon_color
        self._theme_subtle = subtle

        self.wrapper.setStyleSheet(wrapper_css + title_btn_css + ctrl_css)

        self.focustime_label.setStyleSheet(f"color: {muted};")
        self.mode_label.setStyleSheet(f"color: {subtle};")
        self.info_label.setStyleSheet(f"color: {muted};")

        play_icon = (
            QStyle.SP_MediaPause
            if self.logic.s.running
            else QStyle.SP_MediaPlay
        )
        self.play_pause_btn.setIcon(
            tint_icon(
                self.style().standardIcon(play_icon),
                color=self._icon_color,
            )
        )
        self.rewind_btn.setIcon(
            tint_icon(
                self.style().standardIcon(QStyle.SP_MediaSeekBackward),
                color=self._icon_color,
            )
        )
        self.skip_btn.setIcon(
            tint_icon(
                self.style().standardIcon(QStyle.SP_MediaSeekForward),
                color=self._icon_color,
            )
        )
        self.reset_btn.setIcon(
            text_icon(
                "⟲",
                size=18,
                color=self._icon_color,
                font_family=self._ui_font.family(),
            )
        )
        self.reset_btn.setText("")

        close_color = QColor("#ff6b6b")
        self.btn_min.setIcon(
            tint_icon(
                self.style().standardIcon(QStyle.SP_TitleBarMinButton),
                color=icon_color,
            )
        )
        self.btn_close.setIcon(
            tint_icon(
                self.style().standardIcon(QStyle.SP_TitleBarCloseButton),
                color=close_color,
            )
        )

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, "_ui_ready", False):
            return
        self.apply_theme()
        self.update_ui()
        self._ensure_on_top()

    # ---------- Button handlers ----------
    def on_toggle_play_pause(self):
        self.logic.toggle_play_pause()
        # update_ui is already called via on_change, but it's okay here.
        # redundant:
        self.update_ui()

    def on_reset(self):
        self.logic.reset_all()
        self.update_ui()

    def on_lunch(self):
        self.logic.start_lunch_break()
        self.update_ui()

    def on_lunch_or_toggle_mode(self):
        mods = QApplication.keyboardModifiers()

        # SHIFT always toggles
        if mods & Qt.ShiftModifier:
            self.toggle_profile()
            return

        # Normal click: only lunch in study mode
        if self.logic.s.profile == "worklog":
            return

        self.on_lunch()

    def toggle_profile(self):
        s = self.logic.s

        # close current segment cleanly
        self.logic._close_segment()

        # stop running timers
        if self.tick_timer.isActive():
            self.tick_timer.stop()

        if s.profile == "study":
            s.profile = "worklog"
            # reset study-specific runtime (optional)
            s.finished = False
            s.microbreak_active = False
            s.microbreak_remaining = 0
            s.after_micro = ""
            # worklog display: show elapsed (start at 0)
            s.work_elapsed_sec = 0
            s.running = False
        else:
            s.profile = "study"
            self.rewind_btn.setEnabled(True)
            self.skip_btn.setEnabled(True)
            self.btn_lunch.setEnabled(True)
            # restore normal countdown display
            s.mode = "focus"
            s.remaining = s.focus_min * 60
            s.running = False

        self.update_ui()

    # ---------- Dialogs ----------
    def open_settings(self):
        s = self.logic.s
        dlg = SettingsDialog(
            self,
            s.focus_min,
            s.break_min,
            s.micro_sec,
            s.session_goal,
            self.logic.current_unit(),
            s.screen_breaks_enabled,
            )
        if dlg.exec() == QDialog.Accepted:
            focus_min, break_min, micro_sec, goal, start_unit, screen_breaks_enabled = dlg.values()
            self.logic.apply_settings(
                focus_min, break_min, micro_sec, goal, start_unit, screen_breaks_enabled
                )

            # persist config immediately
            self.qs.setValue("focus_min", self.logic.s.focus_min)
            self.qs.setValue("break_min", self.logic.s.break_min)
            self.qs.setValue("micro_sec", self.logic.s.micro_sec)
            self.qs.setValue("session_goal", self.logic.s.session_goal)
            self.qs.setValue("start_unit", int(start_unit))
            self.qs.setValue("screen_breaks_enabled", int(self.logic.s.screen_breaks_enabled))

            self.update_ui()

    def open_stats(self):
        s = self.logic.s
        dlg = StatsDialog(
            self,
            focus_work_sec=s.focus_work_sec,
            paused_sec=s.paused_sec,
            microbreak_sec=s.microbreak_sec,
            total_open_sec=s.total_open_sec,
            )
        dlg.exec()

    # ---------- Tray ----------
    def on_tray_activated(self, reason):
        # Right click -> menu
        if reason == QSystemTrayIcon.Context:
            if self.tray.contextMenu():
                self.tray.contextMenu().popup(QCursor.pos())
            return

        # Left click -> show window
        if reason in (
            QSystemTrayIcon.Trigger,
            QSystemTrayIcon.DoubleClick,
        ):
            self.restore_window()
            return

    # ---------- Close: persist state ----------
    def closeEvent(self, event):
        save_window_position(self.qs, self)
        s = self.logic.s
        self.qs.setValue("mode", s.mode)
        self.qs.setValue("remaining", s.remaining)
        self.qs.setValue("completed_units", s.completed_units)
        self.qs.setValue("finished", int(s.finished))

        self.qs.setValue("microbreak_active", int(s.microbreak_active))
        self.qs.setValue("microbreak_remaining", s.microbreak_remaining)
        self.qs.setValue("after_micro", s.after_micro)

        self.qs.setValue("total_open_sec", s.total_open_sec)
        self.qs.setValue("paused_sec", s.paused_sec)
        self.qs.setValue("microbreak_sec", s.microbreak_sec)
        self.qs.setValue("focus_work_sec", s.focus_work_sec)

        event.accept()

    # ---------- Dragging ----------
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._drag_offset = (event.globalPosition().toPoint() -
                                 self.frameGeometry().topLeft())
            event.accept()

    def mouseMoveEvent(self, event):
        if self._dragging:
            pos = event.globalPosition().toPoint() - self._drag_offset
            clamped = clamp_to_virtual_desktop(
                pos, self.width(), self.height()
            )
            self.move(clamped)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._dragging = False
        save_window_position(self.qs, self)
        event.accept()

    def export_to_csv(self):
        if self.logic.s.profile == "worklog":
            self.flush_worklog_to_csv()
            return

        # Close current segment so the export is complete up to "now"
        self.logic._close_segment()  # closes at now

        entries = list(self.logic.s.log)
        if not entries:
            QMessageBox.information(self, "Export", "No data to export yet.")
            return

        default_name = datetime.now().strftime("focusclock_%d.%m.%Y.csv")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export to CSV", default_name, "CSV Files (*.csv)"
            )
        if not path:
            return

        def fmt_day(dt: datetime) -> str:
            return dt.strftime("%d.%m.%y")

        def fmt_clock(dt: datetime) -> str:
            if dt.minute == 0:
                return f"{dt.hour} Uhr"
            return f"{dt.hour}:{dt.minute:02d} Uhr"

        def fmt_hours_minutes(total_minutes: int) -> str:
            total_minutes = max(0, int(total_minutes))
            h = total_minutes // 60
            m = total_minutes % 60
            if m == 0:
                return f"{h}h"
            return f"{h}h {m}m"

        # Rechnung-/Stundenabrechnung-Format: nur Arbeitsblöcke exportieren.
        work_entries = [e for e in entries if e.kind in {"FOCUS", "WORK"}]
        if not work_entries:
            QMessageBox.information(
                self, "Export", "No work entries to export yet."
                )
            return

        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f, delimiter=";")
                w.writerow(["Datum", "Beginn", "Ende", "Stunden"])

                for e in work_entries:
                    row_minutes = int(round(e.duration_sec / 60))
                    w.writerow(
                        [
                            fmt_day(e.start),
                            fmt_clock(e.start),
                            fmt_clock(e.end),
                            fmt_hours_minutes(row_minutes),
                        ]
                    )
        except OSError as exc:
            QMessageBox.warning(
                self, "Export", f"Could not write CSV file:\n{exc}"
            )
            return

        QMessageBox.information(self, "Export", "CSV export completed.")

    def flush_worklog_to_csv(self):
        # Segment sauber bis "jetzt" schließen
        self.logic._roll_segment_if_needed()
        self.logic._close_segment()

        entries = self.logic.s.log[self.logic.s.flushed_log_idx:]
        if not entries:
            return

        def fmt_day(dt: datetime) -> str:
            # wie in deiner Beispiel-CSV: dd.mm.yy
            return dt.strftime("%d.%m.%y")

        def fmt_clock(dt: datetime) -> str:
            # wie in deiner Beispiel-CSV: "10 Uhr" oder "12:30 Uhr"
            if dt.minute == 0:
                return f"{dt.hour} Uhr"
            return f"{dt.hour}:{dt.minute:02d} Uhr"

        def fmt_duration_words(total_minutes: int) -> str:
            h = total_minutes // 60
            m = total_minutes % 60
            if m == 0:
                return f"{h} Stunden"
            return f"{h} Stunden {m} Minuten"

        def fmt_hours_minutes(total_minutes: int) -> str:
            total_minutes = max(0, int(total_minutes))
            h = total_minutes // 60
            m = total_minutes % 60
            if m == 0:
                return f"{h}h"
            return f"{h}h {m}m"

        rows: list[list[str]] = []
        for e in entries:
            # Worklog-CSV soll nur Arbeitszeiten enthalten (keine Break-Zeilen)
            if e.kind != "WORK":
                continue

            minutes = int(round(e.duration_sec / 60))
            rows.append(
                [
                    fmt_day(e.start),
                    fmt_clock(e.start),
                    fmt_clock(e.end),
                    fmt_duration_words(minutes),
                    ]
                )

        if rows:
            append_to_worklog_csv(rows)

        self.logic.s.flushed_log_idx = len(self.logic.s.log)
        QMessageBox.information(self, "Export", "CSV export completed.")
