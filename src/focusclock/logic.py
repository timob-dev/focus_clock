from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Set

DEFAULT_REMIND_AT = {40 * 60, 20 * 60, 0}


@dataclass
class ClockState:
    # Settings
    focus_min: int = 50
    break_min: int = 10
    micro_sec: int = 60
    session_goal: int = 7
    screen_breaks_enabled: bool = True

    # profile switch
    profile: str = "study"  # "study" or "worklog"
    work_elapsed_sec: int = 0

    # Worklog persistence / flushing
    last_export_date: str = ""  # "YYYY-MM-DD"
    flushed_log_idx: int = 0  # index into log[] that is already written to
    # disk

    # Runtime state
    mode: str = "focus"  # focus / break / lunch
    remaining: int = 50 * 60
    completed_units: int = 0
    microbreak_active: bool = False
    microbreak_remaining: int = 0
    after_micro: str = ""  # resume_focus / go_break / go_focus
    finished: bool = False
    running: bool = False
    pre_lunch_mode: str = "focus"
    pre_lunch_remaining: int = 0
    pre_lunch_was_running: bool = False

    reminded_this_focus: Set[int] = field(default_factory=set)
    remind_at: Set[int] = field(default_factory=lambda: set(DEFAULT_REMIND_AT))

    # Stats
    total_open_sec: int = 0
    paused_sec: int = 0
    microbreak_sec: int = 0
    focus_work_sec: int = 0

    # Logging
    log: list[LogEntry] = field(default_factory=list)
    _segment_kind: str = ""
    _segment_start: datetime | None = None


@dataclass
class LogEntry:
    kind: str  # "FOCUS", "BREAK", "LUNCH", "SCREEN_BREAK", "PAUSED"
    start: datetime
    end: datetime

    @property
    def duration_sec(self) -> int:
        return max(0, int((self.end - self.start).total_seconds()))


def _now() -> datetime:
    return datetime.now()


class FocusClockLogic:
    def __init__(
        self,
        state: ClockState,
        on_change: Callable[[], None],
        on_beep: Callable[[], None],
        ):
        self.s = state
        self._on_change = on_change
        self._beep = on_beep

    # ---------- Derived ----------
    def current_unit(self) -> int:
        if self.s.finished:
            return self.s.session_goal
        return min(self.s.completed_units + 1, self.s.session_goal)

    def calc_focus_progress(self):
        focus_block = self.s.focus_min * 60
        total = self.s.session_goal * focus_block

        done = self.s.completed_units * focus_block
        if (self.s.mode == "focus") and (not self.s.finished):
            done += (focus_block - self.s.remaining)

        done = min(max(done, 0), total)
        left = total - done
        pct = int(round((done / total) * 100)) if total > 0 else 0
        return done, left, total, pct

    # ---------- State transitions ----------
    def mark_finished(self):
        if self.s.profile == "worklog":
            return
        self.s.finished = True
        self.s.running = False
        self.s.microbreak_active = False
        self.s.microbreak_remaining = 0
        self.s.after_micro = ""

    def reconcile_state_on_load(self) -> None:
        """Fix inconsistent persisted state after app restart."""
        if self.s.profile == "worklog":
            return

        if self.s.completed_units >= self.s.session_goal:
            self.mark_finished()
            return

        if self.s.finished:
            return

        if self.s.microbreak_active and self.s.microbreak_remaining <= 0:
            self.s.microbreak_active = False
            self.s.microbreak_remaining = 0
            self.s.after_micro = ""

        if self.s.remaining <= 0:
            if self.s.mode == "focus":
                self.s.remaining = self.s.focus_min * 60
            elif self.s.mode == "break":
                self.s.remaining = self.s.break_min * 60
            elif self.s.mode == "lunch":
                self.s.remaining = 60 * 60

    def switch_to_break(self):
        self.s.mode = "break"
        self.s.remaining = self.s.break_min * 60
        self._beep()
        self._roll_segment_if_needed()

    def switch_to_focus(self):
        self.s.mode = "focus"
        self.s.remaining = self.s.focus_min * 60
        self.s.reminded_this_focus.clear()
        self._beep()
        self._roll_segment_if_needed()

    def start_lunch_break(self):
        if self.s.finished:
            return
        self.s.pre_lunch_mode = self.s.mode
        self.s.pre_lunch_remaining = self.s.remaining
        self.s.pre_lunch_was_running = self.s.running

        self.s.microbreak_active = False
        self.s.microbreak_remaining = 0
        self.s.after_micro = ""
        self.s.mode = "lunch"
        self.s.remaining = 60 * 60
        self.s.running = True
        self._on_change()
        self._roll_segment_if_needed()

    # ---------- microbreak ----------
    def start_microbreak(self, after_micro: str):
        # no text, only internal timer + beep
        if self.s.micro_sec <= 0 or not self.s.screen_breaks_enabled:
            self.s.after_micro = after_micro
            self.end_microbreak()
            return

        self.s.microbreak_active = True
        self._on_change()
        self.s.microbreak_remaining = self.s.micro_sec
        self.s.after_micro = after_micro
        self._beep()
        self._on_change()
        self._roll_segment_if_needed()

    def end_microbreak(self):
        self._beep()
        self.s.microbreak_active = False
        self.s.microbreak_remaining = 0

        if self.s.after_micro == "resume_focus":
            pass
        elif self.s.after_micro == "go_break":
            self.switch_to_break()
        elif self.s.after_micro == "go_focus":
            self.switch_to_focus()

        self.s.after_micro = ""
        self._on_change()
        self._roll_segment_if_needed()

    # ---------- Completion ----------
    def finish_focus_unit(self, use_microbreak_before_break: bool = True):
        # finish unit
        self.s.completed_units += 1

        if self.s.completed_units >= self.s.session_goal:
            self.mark_finished()
            self._on_change()
            return

        # after focus: microbreak and break
        if use_microbreak_before_break:
            self.start_microbreak(after_micro="go_break")
        else:
            self.switch_to_break()
            self._on_change()

    # ---------- Controls ----------
    def start(self):
        if self.s.finished:
            return
        if not self.s.running:
            self.s.running = True
            self._roll_segment_if_needed()
            self._on_change()

    def pause(self):
        self.s.running = False
        self._roll_segment_if_needed()
        self._on_change()

    def toggle_play_pause(self):
        if self.s.running:
            self.pause()
        else:
            self.start()

    def reset_all(self):
        # Worklog reset: reset stopwatch + clear log (optional)
        if self.s.profile == "worklog":
            self.s.running = False
            self.s.work_elapsed_sec = 0

            # log optional: entweder behalten oder löschen
            self.s.log.clear()
            self.s._segment_kind = ""
            self.s._segment_start = None

            self._on_change()
            return

        # --- running condition ---
        self.s.running = False
        self.s.mode = "focus"
        self.s.remaining = self.s.focus_min * 60
        self.s.completed_units = 0
        self.s.finished = False

        # --- Microbreak ---
        self.s.microbreak_active = False
        self.s.microbreak_remaining = 0
        self.s.after_micro = ""
        self.s.reminded_this_focus.clear()

        # --- Statistics RESET ---
        self.s.total_open_sec = 0
        self.s.paused_sec = 0
        self.s.microbreak_sec = 0
        self.s.focus_work_sec = 0

        # Log reset
        self.s.log.clear()
        self.s._segment_kind = ""
        self.s._segment_start = None
        self._on_change()

    def skip_phase(self):
        if self.s.profile == "worklog":
            return

        if self.s.finished:
            return

        # Microbreak: skip = terminate
        if self.s.microbreak_active:
            self.end_microbreak()
            return

        if self.s.mode == "focus":
            # MANUAL SKIP: direct pause, no microbreak
            self.finish_focus_unit(use_microbreak_before_break=False)
            return

        # break/lunch -> focus
        self.switch_to_focus()
        self._on_change()

    def rewind_phase(self):
        if self.s.profile == "worklog":
            return

        if self.s.finished:
            return

        # Spotify-like threshold
        THRESHOLD_SEC = 10

        # Microbreak: back = cancel microbreak, continue in phase
        if self.s.microbreak_active:
            self.s.microbreak_active = False
            self.s.microbreak_remaining = 0
            self.s.after_micro = ""
            self._on_change()
            return

        # Helper: elapsed seconds in current phase
        def elapsed_in_phase(total_sec: int) -> int:
            return max(0, total_sec - int(self.s.remaining))

        if self.s.mode == "focus":
            total = self.s.focus_min * 60
            elapsed = elapsed_in_phase(total)

            # If we're not near the start: rewind to phase start
            if elapsed > THRESHOLD_SEC:
                self.s.remaining = total
                self._on_change()
                return

            # Near start: go to previous phase if possible
            if self.s.completed_units == 0:
                # no previous break exists -> just stay at start of focus
                self.s.remaining = total
                self._on_change()
                return

            # go to previous break (and decrement unit)
            self.s.completed_units -= 1
            self.switch_to_break()
            self._on_change()
            return

        if self.s.mode == "break":
            total = self.s.break_min * 60
            elapsed = elapsed_in_phase(total)

            if elapsed > THRESHOLD_SEC:
                self.s.remaining = total
                self._on_change()
                return

            # Near start: go to focus
            self.switch_to_focus()
            self._on_change()
            return

        if self.s.mode == "lunch":
            total = 60 * 60
            elapsed = elapsed_in_phase(total)

            if elapsed > THRESHOLD_SEC:
                self.s.remaining = total
                self._on_change()
                return

            # Near start: restore previous phase
            self.s.mode = self.s.pre_lunch_mode
            self.s.remaining = self.s.pre_lunch_remaining
            self.s.running = self.s.pre_lunch_was_running
            self._on_change()
            return

    # ---------- Tick handlers ----------
    def on_tick(self):
        """Called once per second, but only if running==True (window
        starts/stops the QTimer)."""
        if self.s.finished or (
                not self.s.running and self.s.profile == "study"):
            return

        self._roll_segment_if_needed()

        # Worklog: zählt hoch, keine Phasenwechsel
        if self.s.profile == "worklog":
            if self.s.running:
                self.s.total_open_sec += 1
                self.s.focus_work_sec += 1  # kannst du wiederverwenden als
                # "work"
                self.s.work_elapsed_sec += 1
                self._on_change()
            return

        self.s.total_open_sec += 1

        # Microbreak tick
        if self.s.microbreak_active:
            self.s.microbreak_sec += 1
            self.s.microbreak_remaining -= 1

            if self.s.microbreak_remaining <= 0:
                self.end_microbreak()  # calls _on_change() already
                return

            self._on_change()  # update UI each second during screen break
            return

        # Focus-Stats
        if self.s.mode == "focus":
            self.s.focus_work_sec += 1

        # normal countdown
        prev_remaining = self.s.remaining
        self.s.remaining -= 1

        # Reminder in Focus
        if (self.s.mode == "focus"
                and self.s.remaining in self.s.remind_at
                and self.s.remaining not in self.s.reminded_this_focus):
            self.s.reminded_this_focus.add(self.s.remaining)

            if self.s.remaining in (40 * 60, 20 * 60):
                self.start_microbreak(after_micro="resume_focus")
                return

            if self.s.remaining == 0:
                # Focus ends: Complete unit immediately (with microbreak,
                # then break)
                self.finish_focus_unit()
                return

        # Phase end without reminder branch (guard: only if we crossed zero)
        if prev_remaining > 0 and self.s.remaining <= 0:
            if self.s.mode == "focus":
                self.finish_focus_unit()
            else:
                if self.s.mode == "lunch":
                    # restore previous phase after lunch
                    self.s.mode = self.s.pre_lunch_mode
                    self.s.remaining = self.s.pre_lunch_remaining
                    self.s.running = self.s.pre_lunch_was_running
                    self._on_change()
                    return

                self.switch_to_focus()
                self._on_change()
                return

        self._on_change()

    def on_pause_count_tick(self):
        """Called once per second (always), counts ‘user paused’."""
        if (not self.s.running) and (not self.s.microbreak_active) and (
                not self.s.finished):
            self.s.paused_sec += 1

    # ---------- Settings apply ----------
    def apply_settings(
        self, focus_min: int, break_min: int, micro_sec: int, goal: int,
        start_unit: int, screen_breaks_enabled: bool = True
        ):
        self.s.focus_min = int(focus_min)
        self.s.break_min = int(break_min)
        self.s.micro_sec = int(micro_sec)
        self.s.session_goal = int(goal)
        self.s.screen_breaks_enabled = bool(screen_breaks_enabled)

        start_unit = max(1, min(int(start_unit), self.s.session_goal))
        self.s.completed_units = start_unit - 1
        self.s.finished = False

        if (not self.s.running) and (not self.s.microbreak_active):
            self.s.remaining = (
                    self.s.focus_min * 60) if self.s.mode == "focus" else (
                    self.s.break_min * 60)

        self._on_change()

    def _current_kind(self) -> str:
        if self.s.profile == "worklog":
            return "WORK" if self.s.running else "PAUSE"

        # study-mode wie bisher:
        if self.s.microbreak_active:
            return "SCREEN_BREAK"
        if not self.s.running:
            return "PAUSED"
        if self.s.mode == "focus":
            return "FOCUS"
        if self.s.mode == "break":
            return "BREAK"
        if self.s.mode == "lunch":
            return "LUNCH"
        return self.s.mode.upper()

    def _close_segment(self, end: datetime | None = None):
        if self.s._segment_start is None or not self.s._segment_kind:
            return
        end = end or _now()
        self.s.log.append(
            LogEntry(self.s._segment_kind, self.s._segment_start, end)
            )
        self.s._segment_kind = ""
        self.s._segment_start = None

    def _open_segment(self, kind: str, start: datetime | None = None):
        self.s._segment_kind = kind
        self.s._segment_start = start or _now()

    def _roll_segment_if_needed(self):
        """Call whenever state changes in a way that should start a new log
        segment."""
        now = _now()
        new_kind = self._current_kind()

        if self.s._segment_start is None:
            self._open_segment(new_kind, now)
            return

        if new_kind != self.s._segment_kind:
            self._close_segment(now)
            self._open_segment(new_kind, now)
