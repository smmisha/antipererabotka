"""Главный цикл: опрос раз в секунду, журнал, heartbeat, блокировки, трей и сон. Всё UI — в главном потоке."""

from __future__ import annotations

import math
import queue
import subprocess
import time
import tkinter as tk
import traceback
from datetime import datetime
from pathlib import Path

from . import ai_watch, config, presence, report, winapi
from .config import Config, Settings
from .core import Core, Event, bypass_event, flash_code as make_flash_code, flash_window, key_matches, restored_active
from .failsafe import ErrorThrottle, FailCounter
from .storage import Storage
from .tray import Tray
from .ui_lock import LockScreen
from .ui_report import ReportWindow, WarningWindow

TICK_MS = 1000
BEAT_S = 10.0
FLUSH_S = 30.0
CONFIG_CHECK_S = 5.0
NIGHT_CODE_KINDS = ("emergency_accepted", "unlock")  # по ним после перезапуска восстанавливаем попытки ночи
NIGHT_MESSAGES = {"wrong": "night_code_wrong", "usb_limit": "night_limit_usb", "emergency_limit": "night_limit_emergency"}


def _num(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def _mmss(seconds: float) -> str:
    seconds = max(0, math.ceil(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


class Runtime:
    def __init__(
        self,
        root: tk.Tk,
        *,
        data: Path,
        cfg: Config,
        storage: Storage,
        test: bool,
        test_key_dir: str | None,
        config_error: str | None = None,
    ) -> None:
        self.root = root
        self.data = data
        self.cfg = cfg
        self.storage = storage
        self.test = test
        self.test_key_dir = test_key_dir
        self._config_error = config_error
        self.cfg_path = data / config.CONFIG_NAME
        self.settings: Settings = config.short_settings() if test else cfg.settings
        self.core: Core | None = None
        self.actions: queue.Queue[str] = queue.Queue()
        self.lock = LockScreen(
            root,
            on_water=self.water_drunk,
            on_snooze=self.snooze,
            on_code=self.emergency,
            on_test_exit=self.test_exit if test else None,
        )
        self.report = ReportWindow(root, on_close=self.report_closed, on_ai=self.ai_request)
        self.warn = WarningWindow(root, on_close=self.warning_closed)
        self.presence = presence.Presence()
        self.tray = Tray(
            title="Антипереработка",
            stats_label=cfg.texts["tray_stats"],
            settings_label=cfg.texts["tray_settings"],
            on_stats=lambda: self.actions.put("stats"),
            on_settings=lambda: self.actions.put("settings"),
        )
        self.guard = winapi.KeyboardGuard()
        self.roots: set[str] = set()
        self.flash_secret: str | None = None
        self._flash_root: str | None = None
        self._lock_kind: str | None = None
        self._cfg_mtime = self._mtime()
        self._cfg_checked = 0.0
        self._beat_at = 0.0
        self._beat_state: str | None = None
        self._flush_at = 0.0
        self._unflushed = 0.0
        self._report_day = None
        self._report_data = None
        self._failsafe = FailCounter()
        self._throttle = ErrorThrottle()
        self._ai_watch: ai_watch.AiWatch | None = None
        self._ai_next = 0.0  # когда опрашивать процессы ИИ снова (time.monotonic)
        self._ai_last: tuple[bool, tuple[str, ...]] = (False, ())

    def start(self) -> None:
        now = datetime.now()
        events = [Event("app_start", now, {"test": self.test})]
        if self._config_error:
            events.append(Event("config_invalid", now, {"reason": self._config_error}))
            self._log_error_text(self._config_error)
        last = self.storage.last_beat()
        self.core = Core(self.settings, now)
        if last is not None:
            beat_at, state, active_s = last
            bypass = bypass_event(now, state, self.storage.ended_since(beat_at), self._rebooted_since(beat_at))
            if bypass is not None:
                events.append(bypass)
            self.core.restore_active(restored_active(active_s, beat_at, now, self.settings.idle_reset_min))
        if self.core.in_night(now):
            self._restore_night(now)
        self.storage.log(events)
        self.roots = self._roots()
        self.tray.start()
        self.root.after(TICK_MS, self._loop)

    def _rebooted_since(self, beat_at: datetime) -> bool:
        """Windows загрузилась позже последнего heartbeat: время загрузки = time.time() - GetTickCount64."""
        boot_at = time.time() - winapi.tick_ms() / 1000
        return boot_at > beat_at.timestamp() + 60

    def _restore_night(self, now: datetime) -> None:
        """Попытки ночи берём из журнала: перезапуск не должен давать новые. Разблокировки до появления событий принятия тоже считаем."""
        night = self.core.night(now)
        flash = emergency = 0
        accepted_at = None
        for at, kind, payload in self.storage.events_since(NIGHT_CODE_KINDS, night.report):
            method = payload.get("method")
            if kind == "emergency_accepted":
                emergency += 1
                accepted_at = at
            elif method == "usb_code":
                flash += 1
                accepted_at = None
            elif method == "emergency":
                if accepted_at is None:  # принятого кода в журнале нет: это старая запись, считаем разблокировку
                    emergency += 1
                accepted_at = None
        self.core.restore_night(night.report, flash, emergency, accepted_at)

    def shutdown(self) -> None:
        now = datetime.now()
        if self.core is not None:
            self._unflushed += self.core.take_active_s()
            self.storage.beat(now, "stopped", self.core.active_s)
        self.storage.add_active(now, self._unflushed)
        self.storage.log([Event("app_stop", now)])
        winapi.keep_awake(False)
        self.guard.stop()
        self.tray.stop()

    # Цикл

    def _loop(self) -> None:
        try:
            self._step()
            self._failsafe.ok()
        except Exception:
            self._record_error()
            if self._failsafe.fail():
                self._trip_failsafe()
        self.root.after(TICK_MS, self._loop)

    def _trip_failsafe(self) -> None:
        """Десять сбоев подряд: блокировка снята, хук клавиатуры остановлен, в трее сообщение."""
        self.lock.hide()
        self.report.hide()
        winapi.keep_awake(False)
        if self.guard.active:
            self.guard.stop()
        self.tray.set_title(self.cfg.texts["tray_failsafe"])
        try:
            self.storage.log([Event("failsafe", datetime.now(), {"reason": "10 сбоев подряд"})])
        except Exception:
            pass  # журнал тоже мог сломаться: причина уже записана в error.log

    def _step(self) -> None:
        now = datetime.now()
        self._reload_config(now)
        self._process_actions(now)
        ai_active, ai_names = self._poll_ai(now)
        # Своя камера на экране перерыва — не звонок: иначе перерыв сам себя спрячет.
        in_call = winapi.call_in_progress() and not self.presence.active
        events = self.core.tick(
            now,
            winapi.idle_seconds(),
            winapi.tick_ms(),
            time.time(),
            in_call=in_call,
            ai_active=ai_active,
            ai_names=ai_names,
            present=self.presence.present,
        )
        self._keep_awake()  # до сна: после конца режима Windows должна снова уметь уснуть
        if self.core.lock_visible:  # съёмные диски и файл ключа ищем только на экране ночной блокировки
            self._update_flash(self._roots())
        else:
            self._flash_root = None
            self.flash_secret = None
        self.storage.log(events)
        if self.core.take_sleep():
            self._sleep(now)
        self._sync_ui(now)
        self._persist(now)

    def _keep_awake(self) -> None:
        """В режиме «ИИ» Windows сама не засыпает. Экран гасить можно: это не запрещено."""
        winapi.keep_awake(self.core.ai_mode)

    def _sync_ui(self, now: datetime) -> None:
        core = self.core
        kind = "break" if core.break_visible else "night" if core.lock_visible else None
        if kind == "break":
            self.lock.show("break", self._break_view(now))
        elif kind == "night":
            self.lock.show("night", self._night_view(now))
        else:
            self.lock.hide()
        appeared = kind is not None and kind != self._lock_kind
        self._lock_kind = kind
        if core.report_open:
            self.report.show(self._report_view(now))
        else:
            self.report.hide()
            self._report_data = None
        if core.warning_visible:
            self.warn.show(self._warning_view(now))
        else:
            self.warn.hide()
        wanted = core.break_visible or core.lock_visible
        if wanted and not self.guard.active:
            self.guard.start()
        elif not wanted and self.guard.active:
            self.guard.stop()
        self.tray.set_title(self._tray_title(now))
        if appeared:
            self._pause_media(now)  # блокировка уже на экране и с перехватом клавиш: теперь музыка и видео
        self._sync_presence(kind)

    def _sync_presence(self, kind: str | None) -> None:
        """Камера только на экране перерыва. Включается после паузы медиа и гаснет, как только перерыв ушёл."""
        if kind == "break" and self.core.s.break_presence:
            self.presence.start()
        else:
            self.presence.stop()

    def _persist(self, now: datetime) -> None:
        mono = time.monotonic()
        self._unflushed += self.core.take_active_s()
        if mono >= self._flush_at:
            self._flush_at = mono + FLUSH_S
            self.storage.add_active(now, self._unflushed)
            self._unflushed = 0.0
        state = self.core.heartbeat_state()
        if state != self._beat_state or mono >= self._beat_at:
            self._beat_state = state
            self._beat_at = mono + BEAT_S
            self.storage.beat(now, state, self.core.active_s)

    # Действия пользователя (главный поток)

    def ai_request(self, on: bool) -> None:
        """Галочка «Сейчас в фоне работает ИИ» из окна отчёта."""
        self.core.set_ai_request(on, datetime.now())

    def water_drunk(self) -> None:
        self.storage.log(self.core.water_drunk(datetime.now()))

    def snooze(self) -> None:
        self.storage.log(self.core.snooze(datetime.now()))

    def emergency(self, typed: str) -> str:
        """Код с флешки или резервный. Пустая строка — код принят, иначе текст для поля ввода."""
        result = self.core.submit(datetime.now(), time.time(), typed, self.flash_secret)
        return self.cfg.texts[NIGHT_MESSAGES[result]] if result else ""

    def test_exit(self) -> None:
        self.storage.log(self.core.test_escape(datetime.now()))

    def report_closed(self) -> None:
        self.core.report_closed()

    def warning_closed(self) -> None:
        self.core.dismiss_warning()

    def _process_actions(self, now: datetime) -> None:
        while True:
            try:
                action = self.actions.get_nowait()
            except queue.Empty:
                return
            if action == "stats":
                self.core.open_report()
            elif action == "settings":
                subprocess.Popen(["notepad.exe", str(self.cfg_path)])

    # Процессы ИИ

    def _poll_ai(self, now: datetime) -> tuple[bool, tuple[str, ...]]:
        """Процессы ИИ раз в ai_poll_s, только ночью и только если режим включён. Ошибка чтения — ИИ не виден:
        блокировку она не снимает, а режим «ИИ» не включается. Ошибку пишем в error.log не чаще раза в минуту."""
        s = self.core.s
        if not s.ai_mode_enabled or not self.core.in_night(now):
            return False, ()
        mono = time.monotonic()
        if mono < self._ai_next:
            return self._ai_last
        self._ai_next = mono + s.ai_poll_s
        try:
            if self._ai_watch is None or (self._ai_watch.cpu_pct, self._ai_watch.io_bytes) != (s.ai_cpu_pct, s.ai_io_kb * 1024):
                self._ai_watch = ai_watch.AiWatch(s.ai_cpu_pct, s.ai_io_kb)
            state = self._ai_watch.observe(ai_watch.Snapshot(mono, tuple(winapi.ai_process_trees(s.ai_processes))))
            self._ai_last = (state.active, state.names)
        except Exception:
            self._ai_last = (False, ())
            self._record_error()
        return self._ai_last

    # Устройства и файлы

    def _roots(self) -> set[str]:
        return winapi.removable_roots([self.test_key_dir] if self.test_key_dir else [])

    def _update_flash(self, roots: set[str]) -> None:
        """Секрет есть, пока флешка с ключом в порту: из него считается код на экране."""
        if self._flash_root is not None and self._flash_root not in roots:
            self._flash_root = None
            self.flash_secret = None
        if self.flash_secret is None:
            for root in sorted(roots):
                text = winapi.read_key(root)
                if text is not None and key_matches(text, self.cfg.usb_key_sha256):
                    self._flash_root = root
                    self.flash_secret = text.strip()
                    return

    def _pause_media(self, now: datetime) -> None:
        """Музыку и видео на паузу. Во время звонка не трогаем: пауза сорвёт встречу."""
        self.root.update_idletasks()  # окно блокировки сначала нарисуется, потом PowerShell (около 2 с)
        if winapi.call_in_progress():
            self.storage.log([Event("media_skipped", now, {"reason": "call"})])
            return
        self.storage.log([Event("media_pause", now, {"paused": winapi.pause_media()})])

    def _sleep(self, now: datetime) -> None:
        ok = winapi.suspend()
        method = "suspend"
        if not ok:
            winapi.monitor_off()
            method = "monitor_off"
        self.storage.log([Event("sleep", now, {"method": method, "ok": ok})])

    def _mtime(self) -> int:
        try:
            return self.cfg_path.stat().st_mtime_ns
        except OSError:
            return 0

    def _reload_config(self, now: datetime) -> None:
        mono = time.monotonic()
        if mono < self._cfg_checked:
            return
        self._cfg_checked = mono + CONFIG_CHECK_S
        mtime = self._mtime()
        if mtime == self._cfg_mtime:
            return
        try:
            fresh = config.load(self.cfg_path)
        except OSError as exc:  # файл занят, например антивирусом: на следующей проверке прочитаем снова
            write, _ = self._throttle.should_write(f"config.json: {exc}", mono)
            if write:
                self._log_error_text(f"config.json: {exc}")
            return
        except ValueError as exc:  # неверные значения или битый JSON: остаёмся на прежних настройках
            self._cfg_mtime = mtime
            self.storage.log([Event("config_invalid", now, {"reason": str(exc)})])
            self._log_error_text(f"config.json: {exc}")
            return
        self._cfg_mtime = mtime
        self.cfg = fresh
        if not self.test:
            self.core.request_settings(fresh.settings, now)

    def _record_error(self) -> None:
        text = traceback.format_exc()
        write, repeats = self._throttle.should_write(text, time.monotonic())
        if write:
            suffix = f"\n(повторов за минуту: {repeats})" if repeats else ""
            self._log_error_text(text + suffix)

    def _log_error_text(self, text: str) -> None:
        with open(self.data / "error.log", "a", encoding="utf-8") as log:
            log.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}\n{text}\n")

    # Тексты для окон

    def _banner(self) -> str:
        return self.cfg.texts["test_banner"] if self.test else ""

    def _break_view(self, now: datetime) -> dict:
        t = self.cfg.texts
        core = self.core
        left = core.break_wait_left(now)
        if left <= 0:
            countdown = ""
        elif self.presence.present:
            countdown = t["break_wait_present"].format(sec=math.ceil(left))
        else:
            countdown = t["break_wait"].format(sec=math.ceil(left))
        return {
            "title": t["break_title"],
            "countdown": countdown,
            "water_enabled": left <= 0,
            "water_text": t["break_drunk"],
            "snooze": t["break_snooze"].format(minutes=_num(core.s.snooze_min)) if core.snooze_available(now) else "",
            "banner": self._banner(),
        }

    def _night_view(self, now: datetime) -> dict:
        t = self.cfg.texts
        core = self.core
        wall = time.time()
        morning = core.night(now).morning.strftime("%H:%M")
        title = t["ai_dark"].format(morning=morning) if core.ai_mode else t["night_title"].format(morning=morning)
        flash_label, flash_code, flash_timer = t["flash_missing"], "", ""
        if self.flash_secret is not None:
            period = core.s.flash_code_period_min * 60
            flash_label = t["flash_label"]
            flash_code = make_flash_code(self.flash_secret, flash_window(wall, period), core.s.flash_code_len)
            flash_timer = t["flash_timer"].format(left=_mmss(period - wall % period))
        code_period = core.s.emergency_code_period_min * 60
        wait = ""
        if core.wait_until is not None:
            wait = t["night_wait"].format(left=_mmss((core.wait_until - now).total_seconds()))
        return {
            "title": title,
            "ai_view": core.ai_mode,
            "now": t["night_now"].format(now=now.strftime("%H:%M:%S")),
            "flash_label": flash_label,
            "flash_code": flash_code,
            "flash_timer": flash_timer,
            "code_label": t["night_code_label"],
            "code": core.emergency_code,
            "code_timer": t["night_code_timer"].format(left=_mmss(code_period - wall % code_period)),
            "hint": t["night_code_hint"],
            "button": t["night_code_button"],
            "wait": wait,
            "banner": self._banner(),
        }

    def _warning_view(self, now: datetime) -> dict:
        t = self.cfg.texts
        left = (self.core.unlock_until - now).total_seconds()
        return {
            "title": t["warn_title"],
            "text": t["warn_text"],
            "countdown": t["warn_countdown"].format(left=_mmss(left)),
            "button": t["warn_button"],
        }

    def _report_view(self, now: datetime) -> dict:
        t = self.cfg.texts
        core = self.core
        day = report.day_of(now, core.s)
        if self._report_data is None or self._report_day != day:
            self._report_day = day
            self._report_data = (report.summary(self.storage, day, core.s), report.week(self.storage, day, core.s))
        summary, week = self._report_data
        warning = countdown = ""
        if core.in_night(now):
            night = core.night(now)
            warning = t["report_warning"].format(night=night.lock.strftime("%H:%M"))
            left = (night.lock - now).total_seconds()
            if left > 0:
                countdown = t["report_left"].format(left=_mmss(left))
        return {
            "title": t["report_title"],
            "date": f"{t['report_day']}: {day:%d.%m.%Y}",
            "warning": warning,
            "lines": [
                t["stat_active"].format(hours=summary.active_h),
                t["stat_water"].format(n=summary.water),
                t["stat_snoozed"].format(n=summary.snoozed),
                t["stat_longest"].format(minutes=summary.longest_min),
                t["stat_emergency"].format(n=summary.emergency),
                t["stat_usb"].format(n=summary.usb),
                t["stat_bypass"].format(n=summary.bypass),
                t["stat_restart"].format(n=summary.restart),
                t["stat_ai"].format(n=summary.ai_min),
            ],
            "week_title": t["stat_week"],
            "week": [(d.strftime("%d.%m"), hours) for d, hours in week],
            "countdown": countdown,
            "ai": self._ai_view(now) if core.s.ai_mode_enabled else None,
            "button": t["report_ok"],
        }

    def _ai_view(self, now: datetime) -> dict:
        t = self.cfg.texts
        if not self.core.in_night(now):
            # Днём ИИ не ищем: не показываем ни старое ночное значение, ни «не обнаружен», а говорим, когда проверка идёт.
            return {
                "checkbox": t["ai_checkbox"],
                "checked": self.core.ai_request,
                "enabled": False,
                "note": t["ai_by_night"],
            }
        active, names = self._ai_last
        note = t["ai_detected"].format(names=ai_watch.ai_titles(names)) if active else t["ai_not_detected"]
        return {
            "checkbox": t["ai_checkbox"],
            "checked": self.core.ai_request,
            "enabled": active,
            "note": note,
        }

    def _tray_title(self, now: datetime) -> str:
        t = self.cfg.texts
        minutes = self.core.minutes_to_break(now)
        if minutes is None:
            return t["tray_night"].format(morning=self.core.night(now).morning.strftime("%H:%M"))
        return t["tray_break"].format(min=math.ceil(minutes))
