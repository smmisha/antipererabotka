"""Состояния и расписание. Без Windows и без UI: время и ввод передаются снаружи."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import string
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from .ai_watch import QuietTimer
from .config import Settings

# Без похожих знаков: 0/O, 1/l/I и строчная o (похожа на 0).
CODE_ALPHABET = "".join(c for c in string.ascii_letters + string.digits if c not in "0O1lIo")

MAX_STEP_S = 5.0  # больший шаг опроса значит сон или зависание: в активное время не идёт
CLOCK_TOLERANCE_S = 120.0  # расхождение часов больше этого считаем переводом часов
RESUME_GAP_S = 60.0  # опроса не было больше минуты: пробуждение
CALL_POSTPONE_MAX = timedelta(minutes=60)  # звонок откладывает перерыв не дольше этого, потом перерыв показывается и во время звонка
TEST_NIGHT_MIN = (3.0, 3.5, 4.0, 6.0)  # тест: отчёт, напоминание, блокировка, утро (минуты от старта)
TEST_AI_CAP_MIN = 8.0  # тест: режим «ИИ» заканчивается не позже этого (минуты от старта)
FINAL_WARNING_S = 60.0  # за столько секунд до блокировки окно отчёта показывается снова
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
    cap: datetime  # ai_cap_time: после него режим «ИИ» не работает


def _clock(hhmm: str) -> timedelta:
    hours, minutes = hhmm.split(":")
    return timedelta(hours=int(hours), minutes=int(minutes))


def logical_day_start(at: datetime, s: Settings) -> datetime:
    """Начало логического дня: morning_time того календарного дня, который сейчас идёт."""
    morning = _clock(s.morning_time)
    return datetime.combine((at - morning).date(), time()) + morning


def at_logical_day(day_start: datetime, hhmm: str, s: Settings) -> datetime:
    """Момент ЧЧ:ММ внутри логического дня, который начался в day_start. Время до полуночи тоже попадает в день."""
    offset = int((_clock(hhmm) - _clock(s.morning_time)) // timedelta(minutes=1)) % 1440
    return day_start + timedelta(minutes=offset)


def night_for(now: datetime, s: Settings, start: datetime | None = None) -> Night:
    """Ночь дня, который идёт или будет идти. День длится от morning_time до следующего morning_time."""
    if s.test_mode:
        base = start or now
        report, reminder, lock, morning = (base + timedelta(minutes=m) for m in TEST_NIGHT_MIN)
        return Night(report, reminder, lock, morning, base + timedelta(minutes=TEST_AI_CAP_MIN))
    day_start = logical_day_start(now, s)
    return Night(
        report=at_logical_day(day_start, s.report_time, s),
        reminder=at_logical_day(day_start, s.reminder_time, s),
        lock=at_logical_day(day_start, s.night_time, s),
        morning=day_start + timedelta(days=1),
        cap=at_logical_day(day_start, s.ai_cap_time, s),
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


def restored_active(active_s: float, beat_at: datetime, now: datetime, idle_reset_min: float) -> float:
    """Счётчик активного времени переносим через перезапуск, только если heartbeat свежий (моложе idle_reset_min)."""
    if now - beat_at < timedelta(minutes=idle_reset_min):
        return max(0.0, active_s)
    return 0.0


class Core:
    def __init__(self, s: Settings, start: datetime, make_code: Callable[[], str] | None = None) -> None:
        self.s = s
        self.start = start
        self._make_code = make_code or self._random_code
        self.active_s = 0.0
        self.break_since: datetime | None = None
        self.snoozed = False
        self.snooze_until: datetime | None = None
        self._postponed_since: datetime | None = None
        self.report_open = False
        self.lock_since: datetime | None = None
        self.unlock_from: datetime | None = None
        self.unlock_until: datetime | None = None
        self.wait_until: datetime | None = None
        self.emergency_code = self._make_code()
        self._emergency_prev: str | None = None
        self._emergency_window: int | None = None
        self.flash_used = 0
        self.emergency_used = 0
        self._unlock_method = ""
        self._warned = False
        self._warning_on = False
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
        self.ai_request = False  # галочка «Сейчас в фоне работает ИИ», живёт одну ночь
        self._ai_mode = False  # блокировка видна, но сон отложен
        self._ai_active = False
        self._ai_names: tuple[str, ...] = ()
        self._quiet: QuietTimer | None = None
        self._final_for: datetime | None = None

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

    @property
    def warning_visible(self) -> bool:
        return self._warning_on

    @property
    def ai_mode(self) -> bool:
        return self._ai_mode

    @property
    def ai_seen(self) -> bool:
        return self._ai_active

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
        in_call: bool = False,
        ai_active: bool = False,
        ai_names: tuple[str, ...] = (),
    ) -> list[Event]:
        """ticks_ms — GetTickCount64, wall — time.time(), idle_s — секунды без ввода, in_call — идёт звонок,
        ai_active — детектор видит ИИ, ai_names — процессы, которые он видит."""
        ev: list[Event] = self._events
        self._events = []
        self._apply_pending(now, ev)
        self._ai_active = ai_active
        self._ai_names = tuple(ai_names) if ai_active else ()
        in_night = self.in_night(now)
        dt, resumed = self._check_time(now, ticks_ms, wall, in_night, ev)
        if in_night:
            self._night_tick(now, resumed, wall, ev)
        elif self._night_key is not None:
            self._end_night(now, ev)
        if self._lock_shown:
            self._rotate_emergency(wall)
        self._warn_before_lock(now, ev)
        if idle_s >= self.s.idle_reset_min * 60 and self.break_since is None:
            # Перерыв на экране простой не закрывает: отметку «Вода выпита» ставят по возвращении.
            if self.active_s > 0:
                ev.append(Event("idle_break", now, {"stretch_s": round(self.active_s)}))
            self._end_break()
        elif not in_night and idle_s < self.s.active_gap_s:
            self.active_s += dt
            self._unflushed_s += dt
        self._schedule_break(now, in_night, in_call, ev)
        return ev

    def _schedule_break(self, now: datetime, in_night: bool, in_call: bool, ev: list[Event]) -> None:
        """Перерыв по расписанию. Звонок его откладывает, но на один перерыв не дольше CALL_POSTPONE_MAX."""
        if in_call and not in_night:
            if self.break_since is None and not self._break_due(now, in_night):
                return  # отсрочку держим, пока перерыв не закончился, в том числе отложенный «Отложить»
            if self._postponed_since is None:
                self._postponed_since = now
                ev.append(Event("break_postponed", now, {"reason": "call"}))
            if now - self._postponed_since < CALL_POSTPONE_MAX:
                self.break_since = None
            elif self.break_since is None:
                self.break_since = now
                self.snooze_until = None
                ev.append(Event("break_shown", now, {"reason": "call_limit"}))
            return
        self._postponed_since = None
        if self.break_since is None and self._break_due(now, in_night):
            self.break_since = now
            self.snooze_until = None
            ev.append(Event("break_shown", now))

    def _break_due(self, now: datetime, in_night: bool) -> bool:
        """Пора показать перерыв: набрано активное время, ночи нет, отложенный «Отложить» уже вернулся."""
        if in_night or self.active_s < self.s.break_interval_min * 60:
            return False
        return self.snooze_until is None or now >= self.snooze_until

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

    def _night_tick(self, now: datetime, resumed: bool, wall: float, ev: list[Event]) -> None:
        night = self.night(now)
        if night.report != self._night_key:
            self._night_key = night.report
            self._night_started = False
            self._slept = False
            self.flash_used = 0
            self.emergency_used = 0
            self.ai_request = False  # галочка живёт одну ночь
            self._ai_mode = False
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
            ev.append(Event("reminder_shown", now))
        if night.lock - timedelta(seconds=FINAL_WARNING_S) <= now < night.lock and self._final_for != night.report:
            self._final_for = night.report
            self.report_open = True  # окно отчёта снова на экране, если его закрыли
            ev.append(Event("final_warning_shown", now))
        lock_due = now >= night.lock
        if self.wait_until is not None and now >= self.wait_until:
            self._unlock(now, "emergency", self.s.unlock_min, ev)
        visible = lock_due and not self._unlocked(now)
        if visible and not self._lock_shown:
            self._lock_shown = True
            self.report_open = False
            self.lock_since = now
            self._slept = False
            self._rotate_emergency(wall, fresh=True)
            if not self._night_started:
                self._night_started = True
                ev.append(Event("night_lock_start", now))
            self._ai_on_lock(now, night, ev)
        elif not visible and self._lock_shown:
            self._lock_shown = False
            self.lock_since = None
        elif visible and resumed:
            self.lock_since = now
            self._slept = False
        self._ai_check(now, night, ev)
        if visible and not self._slept and not self.s.test_mode and self.lock_since is not None and not self._ai_mode:
            if now - self.lock_since >= timedelta(minutes=self.s.sleep_after_min):
                self._slept = True
                self.sleep_due = True

    def _unlocked(self, now: datetime) -> bool:
        return self.unlock_from is not None and self.unlock_from <= now < self.unlock_until

    def _unlock(self, start: datetime, method: str, minutes: float, ev: list[Event]) -> None:
        self._begin_unlock(start, method, minutes)
        ev.append(Event("unlock", start, {"method": method}))
        self._end_ai_mode(start, "unlock", ev)

    def _begin_unlock(self, start: datetime, method: str, minutes: float) -> None:
        self.unlock_from = start
        self.unlock_until = start + timedelta(minutes=minutes)
        self.wait_until = None
        self._unlock_method = method
        self._warned = False
        self._warning_on = False

    def _warn_before_lock(self, now: datetime, ev: list[Event]) -> None:
        """За warn_before_lock_s до конца разблокировки: предупреждение один раз, потом экран заблокируется."""
        if not self._unlocked(now):
            self._warning_on = False
            return
        if not self._warned and self.unlock_until - now <= timedelta(seconds=self.s.warn_before_lock_s):
            self._warned = True
            self._warning_on = True
            ev.append(Event("lock_warning", now, {"method": self._unlock_method}))

    def _end_night(self, now: datetime, ev: list[Event]) -> None:
        self._end_ai_mode(now, "morning", ev)
        if self._night_started:
            ev.append(Event("night_lock_end", now))
        self._night_key = None
        self._night_started = False
        self._lock_shown = False
        self.lock_since = None
        self.report_open = False
        self.unlock_from = self.unlock_until = self.wait_until = None
        self.ai_request = False

    # Режим «ИИ работает ночью»

    def set_ai_request(self, on: bool, now: datetime) -> None:
        """Галочка «Сейчас в фоне работает ИИ» в окне отчёта. Ставится только до блокировки и только когда ИИ виден."""
        if not self.s.ai_mode_enabled:
            return
        if not on:
            if self.ai_request:
                self.ai_request = False
                self._events.append(Event("ai_mode_requested", now, {"on": False}))
            return
        night = self.night(now)
        if self.ai_request or not night.report <= now < night.lock or not self._ai_active:
            return
        self.ai_request = True
        self._events.append(Event("ai_mode_requested", now, {"on": True, "processes": list(self._ai_names)}))

    def _ai_on_lock(self, now: datetime, night: Night, ev: list[Event]) -> None:
        """Появилась блокировка: режим «ИИ» включается, только если галочка стоит, ИИ виден и ai_cap_time ещё не настал."""
        if not self.ai_request:
            return
        if now >= night.cap:
            ev.append(Event("ai_mode_not_started", now, {"reason": "cap"}))
        elif self._ai_active:
            self._ai_mode = True
            self._quiet = QuietTimer(now)
            ev.append(Event("ai_mode_start", now, {"processes": list(self._ai_names)}))
        else:
            ev.append(Event("ai_mode_not_started", now, {"reason": "no_ai"}))

    def _ai_check(self, now: datetime, night: Night, ev: list[Event]) -> None:
        """Пока режим «ИИ» идёт: тишина ai_quiet_min или ai_cap_time заканчивают его."""
        if not self._ai_mode or self._quiet is None:
            return
        if self._quiet.update(now, self._ai_active) >= self.s.ai_quiet_min * 60:
            self._end_ai_mode(now, "quiet", ev)
        elif now >= night.cap:
            self._end_ai_mode(now, "cap", ev)

    def _end_ai_mode(self, at: datetime, reason: str, ev: list[Event]) -> None:
        """Режим «ИИ» закончился. После тишины или ai_cap_time сон наступает сразу, без sleep_after_min."""
        if not self._ai_mode:
            return
        self._ai_mode = False
        self._quiet = None
        ev.append(Event("ai_mode_end", at, {"reason": reason}))
        if reason in ("quiet", "cap") and not self.s.test_mode:
            self._slept = True
            self.sleep_due = True

    def _end_break(self) -> None:
        self.active_s = 0.0
        self.snoozed = False
        self.snooze_until = None
        self.break_since = None
        self._postponed_since = None

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

    def submit(self, now: datetime, wall: float, typed: str, secret: str | None) -> str:
        """Введённый код: сначала флешка, потом резервный. '' — принят. Иначе причина: usb_limit, emergency_limit или wrong."""
        if not self._lock_shown:
            return "wrong"
        if secret and self._flash_matches(wall, typed, secret):
            if self.flash_used >= self.s.flash_unlocks_per_night:
                return "usb_limit"
            self.flash_used += 1
            self._begin_unlock(now, "usb_code", self.s.usb_unlock_min)
            self._events.append(Event("unlock", now, {"method": "usb_code"}))
            self._end_ai_mode(now, "unlock", self._events)
            return ""
        if self.wait_until is None and self._emergency_matches(typed):
            if self.emergency_used >= self.s.emergency_unlocks_per_night:
                return "emergency_limit"
            self.emergency_used += 1
            self.wait_until = now + timedelta(minutes=self.s.emergency_wait_min)
            self._rotate_emergency(wall, fresh=True)
            self._events.append(Event("emergency_accepted", now))
            return ""
        return "wrong"

    def _flash_matches(self, wall: float, typed: str, secret: str) -> bool:
        """Принимаются текущее и предыдущее окно."""
        window = flash_window(wall, self.s.flash_code_period_min * 60)
        given = typed.strip().encode()
        codes = (flash_code(secret, w, self.s.flash_code_len) for w in (window, window - 1))
        return any(hmac.compare_digest(given, code.encode()) for code in codes)

    def _emergency_matches(self, typed: str) -> bool:
        """Принимаются текущий и предыдущий резервный код, как у флешки."""
        given = typed.strip().encode()
        codes = (self.emergency_code, self._emergency_prev)
        return any(code is not None and hmac.compare_digest(given, code.encode()) for code in codes)

    def _rotate_emergency(self, wall: float, fresh: bool = False) -> None:
        """Резервный код меняется каждые emergency_code_period_min минут. fresh — новый показ блокировки или принятый код."""
        window = flash_window(wall, self.s.emergency_code_period_min * 60)
        if fresh or window != self._emergency_window:
            self._emergency_prev = None if fresh else self.emergency_code
            self.emergency_code = self._make_code()
            self._emergency_window = window

    def restore_night(self, night_key: datetime, flash_used: int, emergency_used: int, accepted_at: datetime | None) -> None:
        """После перезапуска счётчики ночи и ожидание резервного кода берутся из журнала: перезапуск не даёт новых попыток."""
        self._night_key = night_key
        self.flash_used = flash_used
        self.emergency_used = emergency_used
        if accepted_at is not None:
            self.wait_until = accepted_at + timedelta(minutes=self.s.emergency_wait_min)

    def dismiss_warning(self) -> None:
        self._warning_on = False

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
