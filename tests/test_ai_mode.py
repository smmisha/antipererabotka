from dataclasses import replace
from datetime import datetime, timedelta

from app.config import Settings, short_settings
from app.core import Core, Event, bypass_event, flash_code, flash_window
from app.storage import Storage

NORMAL = Settings()
AI = ("claude.exe",)


class Clock:
    """Опрос ядра раз в step секунд: время, наличие ИИ и часы системы. Ввод не нужен."""

    def __init__(self, core, now, ai=False, wall=1_700_000_000.0):
        self.core = core
        self.now = now
        self.ai = ai
        self.ticks = 10_000
        self.wall = wall
        self._tick()

    def _tick(self):
        return self.core.tick(self.now, 0.0, self.ticks, self.wall, ai_active=self.ai, ai_names=AI if self.ai else ())

    def run(self, seconds, step=1.0, ai=None):
        if ai is not None:
            self.ai = ai
        events = []
        for _ in range(round(seconds / step)):
            self.now += timedelta(seconds=step)
            self.ticks += round(step * 1000)
            self.wall += step
            events += self._tick()
        return events


def of(events, kind):
    return [e for e in events if e.kind == kind]


def night_start():
    """Ядро в 01:50, когда галочку ещё можно поставить."""
    return Core(NORMAL, datetime(2026, 10, 26, 1, 50))


# Тест 3. Галочка и ИИ в 02:00

def test_request_and_ai_at_0200_starts_the_mode_and_sleep_waits_40_minutes():
    core = night_start()
    clock = Clock(core, datetime(2026, 10, 26, 1, 50), ai=True)
    core.set_ai_request(True, clock.now)
    events = clock.run(11 * 60)  # до 02:01
    started = of(events, "ai_mode_start")
    assert len(started) == 1
    assert started[0].at == datetime(2026, 10, 26, 2, 0)
    assert started[0].payload == {"processes": ["claude.exe"]}
    assert core.ai_mode and core.lock_visible
    clock.run(40 * 60)
    assert not core.take_sleep()  # за 40 минут режима сон ни разу не поставлен


# Тест 4. Галочка, а ИИ в 02:00 нет

def test_request_without_ai_at_0200_gives_a_normal_lock_that_sleeps_after_10_minutes():
    core = night_start()
    clock = Clock(core, datetime(2026, 10, 26, 1, 50), ai=True)
    core.set_ai_request(True, clock.now)
    clock.run(60, ai=False)  # ИИ закрыли в 01:51
    events = clock.run(9 * 60)
    assert [e.payload for e in of(events, "ai_mode_not_started")] == [{"reason": "no_ai"}]
    assert not core.ai_mode and core.lock_visible
    clock.run(599)
    assert not core.take_sleep()
    clock.run(1)  # 02:10:00: ровно sleep_after_min после блокировки
    assert core.take_sleep()


# Тест 5. Галочки нет, ИИ работает

def test_ai_without_a_request_changes_nothing():
    core = night_start()
    clock = Clock(core, datetime(2026, 10, 26, 1, 50), ai=True)
    events = clock.run(10 * 60 + 10)
    assert core.lock_visible and not core.ai_mode
    assert not [k for k in (e.kind for e in events) if k.startswith("ai_mode")]
    clock.run(590)
    assert core.take_sleep()


# Тест 6. ai_cap_time

def test_cap_ends_the_mode_even_while_ai_works():
    core = night_start()
    clock = Clock(core, datetime(2026, 10, 26, 1, 50), ai=True)
    core.set_ai_request(True, clock.now)
    clock.run(10 * 60 + 1)  # 02:00:01: режим идёт
    assert core.ai_mode
    clock.run(7198)  # 03:59:59
    assert core.ai_mode and not core.take_sleep()
    events = clock.run(1)  # 04:00:00
    assert [e.payload for e in of(events, "ai_mode_end")] == [{"reason": "cap"}]
    assert not core.ai_mode
    assert core.take_sleep()


# Тест 2. Тишина

def test_ten_quiet_minutes_end_the_mode_and_sleep_at_once():
    core = night_start()
    clock = Clock(core, datetime(2026, 10, 26, 1, 50), ai=True)
    core.set_ai_request(True, clock.now)
    clock.run(40 * 60)  # 02:30:00, ИИ работал до этого
    clock.run(599, ai=False)
    assert core.ai_mode and not core.take_sleep()  # 9 мин 59 с тишины
    events = clock.run(1, ai=False)  # ровно 10 минут тишины
    assert [e.payload for e in of(events, "ai_mode_end")] == [{"reason": "quiet"}]
    assert core.take_sleep()


def test_activity_on_the_ninth_minute_resets_the_silence():
    core = night_start()
    clock = Clock(core, datetime(2026, 10, 26, 1, 50), ai=True)
    core.set_ai_request(True, clock.now)
    clock.run(40 * 60)  # 02:30:00
    clock.run(539, ai=False)  # 02:38:59
    clock.run(1, ai=True)  # 02:39:00: активность на 9-й минуте тишины
    clock.run(599, ai=False)  # 02:48:59
    assert core.ai_mode
    events = clock.run(1, ai=False)  # 02:49:00
    assert [e.payload for e in of(events, "ai_mode_end")] == [{"reason": "quiet"}]


# Тест 7. Разблокировка кодом в режиме

def test_emergency_unlock_during_the_mode_ends_it():
    core = night_start()
    clock = Clock(core, datetime(2026, 10, 26, 1, 50), ai=True)
    core.set_ai_request(True, clock.now)
    clock.run(10 * 60 + 1)
    assert core.submit(clock.now, clock.wall, core.emergency_code, None) == ""
    clock.run(59 * 60)
    assert core.ai_mode  # ожидание ещё идёт, экран закрыт
    events = clock.run(60)
    assert [e.payload for e in of(events, "ai_mode_end")] == [{"reason": "unlock"}]
    assert not core.ai_mode


def test_flash_unlock_during_the_mode_ends_it():
    secret = "ab" * 32
    core = night_start()
    clock = Clock(core, datetime(2026, 10, 26, 1, 50), ai=True)
    core.set_ai_request(True, clock.now)
    clock.run(10 * 60 + 1)
    window = flash_window(clock.wall, 120)
    assert core.submit(clock.now, clock.wall, flash_code(secret, window, 40), secret) == ""
    events = clock.run(1)
    assert [e.payload for e in of(events, "ai_mode_end")] == [{"reason": "unlock"}]
    assert not core.ai_mode


# Тест 8. Галочка живёт одну ночь

def test_request_does_not_carry_over_to_the_next_night():
    core = night_start()
    clock = Clock(core, datetime(2026, 10, 26, 1, 50), ai=True)
    core.set_ai_request(True, clock.now)
    clock.run(22200, step=60)  # до 08:00
    assert not core.ai_request
    clock.run(64200, step=60)  # до 01:50 следующей ночи, ИИ по-прежнему работает
    events = clock.run(10 * 60 + 1)  # 02:00:01
    assert not of(events, "ai_mode_start")
    assert core.lock_visible and not core.ai_mode


# Тест 9. Перезапуск в режиме «ИИ»

def test_restart_in_the_mode_is_a_bypass_only_without_a_regular_end(tmp_path):
    db = Storage(tmp_path / "journal.sqlite3")
    beat = datetime(2026, 10, 26, 2, 20, 0)
    db.beat(beat, "night", 120.0)
    now = datetime(2026, 10, 26, 2, 30, 0)
    assert bypass_event(now, "night", db.ended_since(beat)).kind == "bypass"
    db.log([Event("ai_mode_end", datetime(2026, 10, 26, 2, 25), {"reason": "quiet"})])
    assert bypass_event(now, "night", db.ended_since(beat)) is None
    db.close()


# Галочка: когда её можно поставить

def test_request_needs_ai_seen_right_now():
    core = night_start()
    clock = Clock(core, datetime(2026, 10, 26, 1, 50), ai=False)
    core.set_ai_request(True, clock.now)
    assert not core.ai_request


def test_request_is_refused_in_the_day_after_the_lock_and_when_disabled():
    day = Core(NORMAL, datetime(2026, 10, 26, 14, 0))
    Clock(day, datetime(2026, 10, 26, 14, 0), ai=True)
    day.set_ai_request(True, datetime(2026, 10, 26, 14, 0))
    assert not day.ai_request

    locked = Core(NORMAL, datetime(2026, 10, 26, 2, 0, 5))
    Clock(locked, datetime(2026, 10, 26, 2, 0, 5), ai=True)
    locked.set_ai_request(True, datetime(2026, 10, 26, 2, 0, 5))
    assert not locked.ai_request

    off = Core(replace(NORMAL, ai_mode_enabled=False), datetime(2026, 10, 26, 1, 50))
    clock = Clock(off, datetime(2026, 10, 26, 1, 50), ai=True)
    off.set_ai_request(True, clock.now)
    assert not off.ai_request


def test_request_is_logged_and_can_be_taken_back():
    core = night_start()
    clock = Clock(core, datetime(2026, 10, 26, 1, 50), ai=True)
    core.set_ai_request(True, clock.now)
    events = clock.run(1)
    assert [e.payload for e in of(events, "ai_mode_requested")] == [{"on": True, "processes": ["claude.exe"]}]
    core.set_ai_request(False, clock.now)
    events = clock.run(1)
    assert not core.ai_request
    assert [e.payload for e in of(events, "ai_mode_requested")] == [{"on": False}]


# Окно отчёта за минуту до блокировки

def test_final_warning_reopens_a_closed_report_once_per_night():
    core = Core(NORMAL, datetime(2026, 10, 26, 1, 58, 30))
    clock = Clock(core, datetime(2026, 10, 26, 1, 58, 30))
    core.report_closed()
    events = clock.run(30)  # 01:59:00
    assert "final_warning_shown" in [e.kind for e in events]
    assert core.report_open
    core.report_closed()
    events = clock.run(20)  # 01:59:20: второй раз не открывается
    assert not core.report_open
    assert not of(events, "final_warning_shown")


def test_final_warning_is_not_shown_before_the_last_minute():
    core = night_start()
    clock = Clock(core, datetime(2026, 10, 26, 1, 50))
    events = clock.run(8 * 60 + 59)  # 01:58:59
    assert not of(events, "final_warning_shown")


# Тестовый профиль

def test_test_profile_mode_ends_on_quiet_without_sleep():
    core = Core(short_settings(), datetime(2026, 10, 25, 10, 0))
    clock = Clock(core, datetime(2026, 10, 25, 10, 0), ai=True)
    clock.run(3 * 60 + 10)  # 10:03:10: окно отчёта, галочка
    core.set_ai_request(True, clock.now)
    events = clock.run(60)  # 10:04:10: блокировка
    assert [e.payload for e in of(events, "ai_mode_start")] == [{"processes": ["claude.exe"]}]
    clock.run(30, ai=False)
    events = clock.run(60, ai=False)  # минута тишины
    assert [e.payload for e in of(events, "ai_mode_end")] == [{"reason": "quiet"}]
    assert not core.take_sleep()  # в тесте сон не включается


def test_test_profile_mode_ends_with_the_morning():
    core = Core(short_settings(), datetime(2026, 10, 25, 10, 0))
    clock = Clock(core, datetime(2026, 10, 25, 10, 0), ai=True)
    clock.run(3 * 60 + 10)
    core.set_ai_request(True, clock.now)
    events = clock.run(3 * 60)  # до 10:06, утро
    assert [e.payload for e in of(events, "ai_mode_end")] == [{"reason": "morning"}]
    assert not core.ai_mode and not core.lock_visible


def test_night_cap_is_at_ai_cap_time():
    core = Core(NORMAL, datetime(2026, 10, 26, 1, 50))
    assert core.night(datetime(2026, 10, 26, 1, 50)).cap == datetime(2026, 10, 26, 4, 0)
    assert core.heartbeat_state() == "ok"
