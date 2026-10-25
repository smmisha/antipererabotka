"""Состояния и расписание. Без Windows и без UI: время и ввод передаются снаружи."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import string
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from .config import Settings

# Без похожих знаков: 0/O, 1/l/I и строчная o (похожа на 0).
CODE_ALPHABET = "".join(c for c in string.ascii_letters + string.digits if c not in "0O1lIo")

MAX_STEP_S = 5.0  # больший шаг опроса значит сон или зависание: в активное время не идёт
CLOCK_TOLERANCE_S = 120.0  # расхождение часов больше этого считаем переводом часов
RESUME_GAP_S = 60.0  # опроса не было больше минуты: пробуждение
TEST_NIGHT_MIN = (3.0, 3.5, 4.0, 6.0)  # тест: отчёт, напоминание, блокировка, утро (минуты от старта)
LOCK_STATES = ("break", "night")


@dataclass(frozen=True)
class Event:
    kind: str
    at: datetime
    payload: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Night:
    report: datetime
    reminder: datetime
    lock: datetime
    morning: datetime


def _clock(hhmm: str) -> timedelta:
    hours, minutes = hhmm.split(":")
    return timedelta(hours=int(hours), minutes=int(minutes))


def night_for(now: datetime, s: Settings, start: datetime | None = None) -> Night:
    """Ночь дня, который идёт или будет идти. День длится от morning_time до следующего morning_time."""
    if s.test_mode:
        base = start or now
        return Night(*(base + timedelta(minutes=m) for m in TEST_NIGHT_MIN))
    day = (now - _clock(s.morning_time)).date()
    base = datetime.combine(day, time()) + timedelta(days=1)
    return Night(
        report=base + _clock(s.report_time),
        reminder=base + _clock(s.reminder_time),
        lock=base + _clock(s.night_time),
        morning=base + _clock(s.morning_time),
    )


def usb_key_allowed(now: datetime, s: Settings) -> bool:
    """Ключ нельзя создать с 01:45 до 08:00."""
    if s.test_mode:
        return True
    night = night_for(now, s)
    return not night.report <= now < night.morning


def new_usb_secret() -> str:
    return secrets.token_hex(32)


def key_hash(secret: str) -> str:
    return hashlib.sha256(secret.strip().encode("utf-8")).hexdigest()


def key_matches(secret: str, expected_hash: str) -> bool:
    if not expected_hash:
        return False
    return hmac.compare_digest(key_hash(secret).encode(), expected_hash.encode())


def bypass_event(now: datetime, prev_state: str | None, ended_since_prev: bool, rebooted: bool = False) -> Event | None:
    """Приложение пропало во время блокировки без штатного конца. После перезагрузки это restart_during_lock, а не обход."""
    if prev_state not in LOCK_STATES or ended_since_prev:
        return None
    if rebooted:
        return Event("restart_during_lock", now, {"was": prev_state})
    return Event("bypass", now, {"reason": "restart", "was": prev_state})


def flash_window(wall: float, period_s: float) -> int:
    """Номер окна времени. Код меняется на границе окна."""
    return int(wall // period_s)


def flash_code(secret: str, window: int, length: int) -> str:
    """Код из секрета флешки и номера окна: без флешки его не получить."""
    key = secret.strip().encode("utf-8")
    chars = []
    for i in range(length):
        digest = hmac.new(key, f"{window}:{i}".encode(), hashlib.sha256).digest()
        chars.append(CODE_ALPHABET[int.from_bytes(digest, "big") % len(CODE_ALPHABET)])
    return "".join(chars)


class Core:
    def __init__(self, s: Settings, start: datetime, make_code: Callable[[], str] | None = None) -> None:
        self.s = s
        self.start = start
        self._make_code = make_code or self._random_code
        self.active_s = 0.0
        self.break_since: datetime | None = None
        self.snoozed = False
        self.snooze_until: datetime | None = None
        self.report_open = False
        self.lock_since: datetime | None = None
        self.unlock_from: datetime | None = None
        self.unlock_until: datetime | None = None
        self.wait_until: datetime | None = None
        self.emergency_code = self._make_code()
        self.sleep_due = False
        self._lock_shown = False
        self._slept = False
        self._night_key: datetime | None = None
        self._night_started = False
        self._report_for: datetime | None = None
        self._reminder_for: datetime | None = None
        self._pending: tuple[Settings, datetime] | None = None
        self._last: tuple[int, float] | None = None
        self._unflushed_s = 0.0
        self._events: list[Event] = []

    def _random_code(self) -> str:
        return "".join(secrets.choice(CODE_ALPHABET) for _ in range(self.s.emergency_len))

    # Что показывать. Читается после каждого tick().

    def night(self, now: datetime) -> Night:
        return night_for(now, self.s, self.start)

    def in_night(self, now: datetime) -> bool:
        night = self.night(now)
        return night.report <= now < night.morning

    @property
    def lock_visible(self) -> bool:
        return self._lock_shown

    @property
    def break_visible(self) -> bool:
        return self.break_since is not None

    def break_wait_left(self, now: datetime) -> float:
        if self.break_since is None:
            return 0.0
        return max(0.0, self.s.break_min_lock_s - (now - self.break_since).total_seconds())

    def snooze_available(self, now: datetime) -> bool:
        return self.break_visible and not self.snoozed and self.break_wait_left(now) == 0.0

    def minutes_to_break(self, now: datetime) -> float | None:
        if self.in_night(now):
            return None
        return max(0.0, (self.s.break_interval_min * 60 - self.active_s) / 60)

    def heartbeat_state(self) -> str:
        if self._lock_shown:
            return "night"
        if self.break_since is not None:
            return "break"
        return "ok"

    # Время и ввод. Вызывается раз в 1–2 секунды.

    def tick(
        self,
        now: datetime,
        idle_s: float,
        ticks_ms: int,
        wall: float,
    ) -> list[Event]:
        """ticks_ms — GetTickCount64, wall — time.time(), idle_s — секунды без ввода."""
        ev: list[Event] = self._events
        self._events = []
        self._apply_pending(now, ev)
        in_night = self.in_night(now)
        dt, resumed = self._check_time(now, ticks_ms, wall, in_night, ev)
        if in_night:
            self._night_tick(now, resumed, ev)
        elif self._night_key is not None:
            self._end_night(now, ev)
        if idle_s >= self.s.idle_reset_min * 60:
            if self.active_s > 0:
                ev.append(Event("idle_break", now, {"stretch_s": round(self.active_s)}))
            self._end_break()
        elif not in_night and idle_s < self.s.active_gap_s:
            self.active_s += dt
            self._unflushed_s += dt
        due = self.s.break_interval_min * 60
        if not in_night and self.break_since is None and self.active_s >= due:
            if self.snooze_until is None or now >= self.snooze_until:
                self.break_since = now
                self.snooze_until = None
                ev.append(Event("break_shown", now))
        return ev

    def _check_time(self, now: datetime, ticks_ms: int, wall: float, in_night: bool, ev: list[Event]) -> tuple[float, bool]:
        if self._last is None:
            self._last = (ticks_ms, wall)
            return 0.0, False
        last_ticks, last_wall = self._last
        self._last = (ticks_ms, wall)
        d_ticks = (ticks_ms - last_ticks) / 1000
        d_wall = wall - last_wall
        dt = min(max(d_ticks, 0.0), MAX_STEP_S)
        if abs(d_wall - d_ticks) > CLOCK_TOLERANCE_S:
            ev.append(Event("clock_change", now, {"delta_s": round(d_wall - d_ticks)}))
            if in_night:
                ev.append(Event("bypass", now, {"reason": "clock"}))
            return dt, False
        return dt, d_wall > RESUME_GAP_S

    def _night_tick(self, now: datetime, resumed: bool, ev: list[Event]) -> None:
        night = self.night(now)
        if night.report != self._night_key:
            self._night_key = night.report
            self._night_started = False
            self._slept = False
            if self.break_since is not None:
                ev.append(Event("break_cancelled", now, {"reason": "night"}))
            self._end_break()
        if now >= night.report and self._report_for != night.report:
            self._report_for = night.report
            if now < night.lock:
                self.report_open = True
                ev.append(Event("report_shown", now))
        if night.reminder <= now < night.lock and self._reminder_for != night.reminder:
            self._reminder_for = night.reminder
            self.report_open = True
        lock_due = now >= night.lock
        if self.wait_until is not None and now >= self.wait_until:
            self._unlock(now, "emergency", self.s.unlock_min, ev)
        visible = lock_due and not self._unlocked(now)
        if visible and not self._lock_shown:
            self._lock_shown = True
            self.report_open = False
            self.lock_since = now
            self._slept = False
            self.emergency_code = self._make_code()
            if not self._night_started:
                self._night_started = True
                ev.append(Event("night_lock_start", now))
        elif not visible and self._lock_shown:
            self._lock_shown = False
            self.lock_since = None
        elif visible and resumed:
            self.lock_since = now
            self._slept = False
        if visible and not self._slept and not self.s.test_mode and self.lock_since is not None:
            if now - self.lock_since >= timedelta(minutes=self.s.sleep_after_min):
                self._slept = True
                self.sleep_due = True

    def _unlocked(self, now: datetime) -> bool:
        return self.unlock_from is not None and self.unlock_from <= now < self.unlock_until

    def _unlock(self, start: datetime, method: str, minutes: float, ev: list[Event]) -> None:
        self.unlock_from = start
        self.unlock_until = start + timedelta(minutes=minutes)
        self.wait_until = None
        ev.append(Event("unlock", start, {"method": method}))

    def _end_night(self, now: datetime, ev: list[Event]) -> None:
        if self._night_started:
            ev.append(Event("night_lock_end", now))
        self._night_key = None
        self._night_started = False
        self._lock_shown = False
        self.lock_since = None
        self.report_open = False
        self.unlock_from = self.unlock_until = self.wait_until = None

    def _end_break(self) -> None:
        self.active_s = 0.0
        self.snoozed = False
        self.snooze_until = None
        self.break_since = None

    def _apply_pending(self, now: datetime, ev: list[Event]) -> None:
        if self._pending is not None and now >= self._pending[1]:
            self.s = self._pending[0]
            self._pending = None
            ev.append(Event("settings_changed", now))

    def _next_morning(self, now: datetime) -> datetime:
        at = datetime.combine(now.date(), time()) + _clock(self.s.morning_time)
        return at if at > now else at + timedelta(days=1)

    # Действия пользователя и служебные вызовы.

    def water_drunk(self, now: datetime) -> list[Event]:
        if self.break_since is None or (now - self.break_since).total_seconds() < self.s.break_min_lock_s:
            return []
        ev = [Event("water_drunk", now, {"stretch_s": round(self.active_s)})]
        self._end_break()
        return ev

    def snooze(self, now: datetime) -> list[Event]:
        if not self.snooze_available(now):
            return []
        self.snoozed = True
        self.snooze_until = now + timedelta(minutes=self.s.snooze_min)
        self.break_since = None
        return [Event("break_snoozed", now)]

    def report_closed(self) -> None:
        self.report_open = False

    def open_report(self) -> None:
        self.report_open = True

    def emergency_submit(self, now: datetime, typed: str) -> bool:
        """True — код верный, начат отсчёт. Новый код появится при следующем показе блокировки."""
        if not self._lock_shown or self.wait_until is not None:
            return False
        if not hmac.compare_digest(typed.strip().encode(), self.emergency_code.encode()):
            return False
        self.wait_until = now + timedelta(minutes=self.s.emergency_wait_min)
        self.emergency_code = self._make_code()
        return True

    def flash_submit(self, now: datetime, wall: float, typed: str, secret: str | None) -> bool:
        """Код с флешки: принимаются текущее и предыдущее окно. Без секрета (флешки в порту) не принимается."""
        if not self._lock_shown or not secret:
            return False
        window = flash_window(wall, self.s.flash_code_period_min * 60)
        given = typed.strip().encode()
        for w in (window, window - 1):
            if hmac.compare_digest(given, flash_code(secret, w, self.s.flash_code_len).encode()):
                self.unlock_from = now
                self.unlock_until = now + timedelta(minutes=self.s.usb_unlock_min)
                self.wait_until = None
                self._events.append(Event("unlock", now, {"method": "usb_code"}))
                return True
        return False

    def test_escape(self, now: datetime) -> list[Event]:
        """Ctrl+Shift+F12 в тестовом режиме: снимает текущую блокировку."""
        if not self.s.test_mode:
            return []
        ev: list[Event] = []
        if self.break_since is not None:
            self._end_break()
            ev.append(Event("test_exit", now, {"what": "break"}))
        if self._lock_shown:
            self._unlock(now, "test", self.s.unlock_min, ev)
        return ev

    def request_settings(self, new: Settings, now: datetime) -> None:
        """Новые настройки вступят только утром, в morning_time. В тестовом режиме игнорируются."""
        if self.s.test_mode:
            return
        if new == self.s:
            self._pending = None
            return
        self._pending = (new, self._next_morning(now))

    def take_sleep(self) -> bool:
        due, self.sleep_due = self.sleep_due, False
        return due

    def take_active_s(self) -> float:
        seconds, self._unflushed_s = self._unflushed_s, 0.0
        return seconds

    def restore_active(self, active_s: float) -> None:
        self.active_s = max(0.0, active_s)
