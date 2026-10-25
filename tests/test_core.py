from dataclasses import replace
from datetime import datetime, timedelta

from app.config import Settings, short_settings
from app.core import CODE_ALPHABET, Core, bypass_event, flash_code, flash_window, key_hash, key_matches, restored_active, usb_key_allowed

NORMAL = Settings()


class Ticker:
    """Имитирует опрос раз в step секунд: локальное время, ввод и часы системы."""

    def __init__(self, core, now, ticks=10_000, wall=1_700_000_000.0):
        self.core = core
        self.now = now
        self.ticks = ticks
        self.wall = wall
        self.events = core.tick(now, 0.0, ticks, wall)

    def run(self, seconds, idle=0.0, step=1.0):
        events = []
        for _ in range(round(seconds / step)):
            self.now += timedelta(seconds=step)
            self.ticks += round(step * 1000)
            self.wall += step
            events += self.core.tick(self.now, idle, self.ticks, self.wall)
        return events


def kinds(events):
    return [e.kind for e in events]


# День и ночь

def test_day_runs_from_morning_to_morning():
    core = Core(NORMAL, datetime(2026, 10, 25, 12, 0))
    assert not core.in_night(datetime(2026, 10, 25, 23, 59))
    assert not core.in_night(datetime(2026, 10, 26, 1, 44))
    assert core.in_night(datetime(2026, 10, 26, 1, 45))
    assert core.in_night(datetime(2026, 10, 26, 7, 59))
    assert not core.in_night(datetime(2026, 10, 26, 8, 0))
    assert core.night(datetime(2026, 10, 26, 8, 0)).report == datetime(2026, 10, 27, 1, 45)


def test_usb_key_cannot_be_made_at_night():
    assert usb_key_allowed(datetime(2026, 10, 25, 12, 0), NORMAL)
    assert not usb_key_allowed(datetime(2026, 10, 26, 3, 0), NORMAL)
    assert usb_key_allowed(datetime(2026, 10, 26, 3, 0), short_settings())


# Перерыв на воду

def test_break_after_60_minutes_of_activity():
    core = Core(NORMAL, datetime(2026, 10, 25, 10, 0))
    t = Ticker(core, datetime(2026, 10, 25, 10, 0))
    t.run(3540)
    assert not core.break_visible
    events = t.run(60)
    assert core.break_visible
    assert "break_shown" in kinds(events)


def test_idle_is_not_counted_and_resets_after_5_minutes():
    core = Core(NORMAL, datetime(2026, 10, 25, 10, 0))
    t = Ticker(core, datetime(2026, 10, 25, 10, 0))
    t.run(600)
    t.run(120, idle=150)
    assert core.active_s == 600
    events = t.run(1, idle=300)
    assert core.active_s == 0
    assert [(e.kind, e.payload) for e in events] == [("idle_break", {"stretch_s": 600})]


def test_input_every_30_seconds_gives_a_break_after_60_minutes():
    core = Core(NORMAL, datetime(2026, 10, 25, 10, 0))
    t = Ticker(core, datetime(2026, 10, 25, 10, 0))
    t.run(3599, idle=29)
    assert not core.break_visible
    t.run(1, idle=29)
    assert core.break_visible


def test_four_minutes_without_input_count_only_two():
    core = Core(NORMAL, datetime(2026, 10, 25, 10, 0))
    t = Ticker(core, datetime(2026, 10, 25, 10, 0))
    for s in range(240):
        t.now += timedelta(seconds=1)
        t.ticks += 1000
        t.wall += 1
        core.tick(t.now, float(s), t.ticks, t.wall)
    assert core.active_s == 120


def test_test_profile_counts_activity_with_a_30_second_gap():
    assert short_settings().active_gap_s == 30


def test_water_button_waits_60_seconds_and_snooze_is_used_once():
    core = Core(NORMAL, datetime(2026, 10, 25, 10, 0))
    t = Ticker(core, datetime(2026, 10, 25, 10, 0))
    t.run(3600)
    assert core.break_visible
    assert core.water_drunk(t.now) == []
    assert not core.snooze_available(t.now)
    t.run(60)
    assert core.snooze_available(t.now)
    assert kinds(core.snooze(t.now)) == ["break_snoozed"]
    assert not core.break_visible
    assert core.snooze(t.now) == []
    t.run(600)
    assert core.break_visible
    assert not core.snooze_available(t.now)
    t.run(60)
    assert kinds(core.water_drunk(t.now)) == ["water_drunk"]
    assert core.active_s == 0


def test_no_water_breaks_between_0145_and_0800():
    core = Core(NORMAL, datetime(2026, 10, 26, 1, 44))
    core.restore_active(3600)
    t = Ticker(core, datetime(2026, 10, 26, 1, 44))
    assert core.break_visible
    t.run(120)
    assert not core.break_visible
    core = Core(NORMAL, datetime(2026, 10, 26, 5, 0))
    core.restore_active(3600)
    Ticker(core, datetime(2026, 10, 26, 5, 0))
    assert not core.break_visible


# Ночь: отчёт, блокировка, сон

def test_start_at_0300_locks_at_once_without_report():
    core = Core(NORMAL, datetime(2026, 10, 26, 3, 0))
    t = Ticker(core, datetime(2026, 10, 26, 3, 0))
    assert core.lock_visible
    assert not core.report_open
    assert "night_lock_start" in kinds(t.events)


def test_wake_at_0150_shows_report_at_once_without_lock():
    core = Core(NORMAL, datetime(2026, 10, 26, 1, 50))
    Ticker(core, datetime(2026, 10, 26, 1, 50))
    assert core.report_open
    assert not core.lock_visible


def test_report_can_be_closed_and_comes_back_at_0155():
    core = Core(NORMAL, datetime(2026, 10, 26, 1, 40))
    t = Ticker(core, datetime(2026, 10, 26, 1, 40))
    t.run(5 * 60)
    assert core.report_open
    core.report_closed()
    t.run(9 * 60)
    assert not core.report_open
    t.run(60)
    assert core.report_open


def test_night_lock_at_0200_and_sleep_after_10_minutes_once():
    core = Core(NORMAL, datetime(2026, 10, 26, 1, 59, 30))
    t = Ticker(core, datetime(2026, 10, 26, 1, 59, 30))
    t.run(30)
    assert core.lock_visible
    assert not core.take_sleep()
    t.run(9 * 60)
    assert not core.take_sleep()
    t.run(60)
    assert core.take_sleep()
    assert not core.take_sleep()


def test_wake_restarts_sleep_countdown():
    core = Core(NORMAL, datetime(2026, 10, 26, 2, 0))
    t = Ticker(core, datetime(2026, 10, 26, 2, 0))
    t.run(9 * 60)
    t.now += timedelta(hours=1)
    t.ticks += 3_600_000
    t.wall += 3600
    core.tick(t.now, 0.0, t.ticks, t.wall)
    assert core.lock_visible
    assert not core.take_sleep()
    t.run(10 * 60)
    assert core.take_sleep()


def test_night_ends_at_morning():
    core = Core(NORMAL, datetime(2026, 10, 26, 7, 59))
    t = Ticker(core, datetime(2026, 10, 26, 7, 59))
    assert core.lock_visible
    events = t.run(60)
    assert not core.lock_visible
    assert "night_lock_end" in kinds(events)


# USB-ключ и экстренный код

def test_inserting_the_flash_alone_does_not_unlock():
    core = Core(NORMAL, datetime(2026, 10, 26, 3, 0))
    t = Ticker(core, datetime(2026, 10, 26, 3, 0))
    t.run(60)
    assert core.lock_visible
    events = t.run(60)
    assert core.lock_visible
    assert "unlock" not in kinds(events)


def test_flash_code_is_40_chars_from_the_alphabet_and_changes_per_window():
    code = flash_code("ab" * 32, 10, 40)
    assert len(code) == 40
    assert set(code) <= set(CODE_ALPHABET)
    assert code == flash_code("ab" * 32, 10, 40)
    assert code != flash_code("ab" * 32, 11, 40)
    assert code != flash_code("cd" * 32, 10, 40)


def test_flash_code_unlocks_for_current_and_previous_window_only():
    secret = "ab" * 32
    core = Core(NORMAL, datetime(2026, 10, 26, 3, 0))
    t = Ticker(core, datetime(2026, 10, 26, 3, 0))
    window = flash_window(t.wall, 120)
    assert core.lock_visible
    assert not core.flash_submit(t.now, t.wall, flash_code(secret, window - 2, 40), secret)
    assert not core.flash_submit(t.now, t.wall, flash_code(secret, window, 40), None)
    assert not core.flash_submit(t.now, t.wall, flash_code("cd" * 32, window, 40), secret)
    assert core.flash_submit(t.now, t.wall, flash_code(secret, window - 1, 40), secret)
    events = t.run(1)
    assert [(e.kind, e.payload) for e in events if e.kind == "unlock"] == [("unlock", {"method": "usb_code"})]
    assert not core.lock_visible


def test_emergency_code_waits_then_unlocks_for_30_minutes():
    core = Core(NORMAL, datetime(2026, 10, 26, 2, 0))
    t = Ticker(core, datetime(2026, 10, 26, 2, 0))
    code = core.emergency_code
    assert len(code) == 40
    assert set(code) <= set(CODE_ALPHABET)
    assert not core.emergency_submit(t.now, "wrong")
    assert core.emergency_submit(t.now, " " + code + " ")
    assert core.lock_visible
    events = t.run(10 * 60)
    assert [(e.kind, e.payload) for e in events if e.kind == "unlock"] == [("unlock", {"method": "emergency"})]
    t.run(30 * 60 - 1)
    assert not core.lock_visible
    t.run(1)
    assert core.lock_visible
    assert core.emergency_code != code


def test_code_alphabet_has_no_confusable_characters():
    for ch in "0O1lIo":
        assert ch not in CODE_ALPHABET


# Обход и перевод часов

def test_bypass_only_when_the_lock_has_no_ending():
    now = datetime(2026, 10, 26, 3, 0)
    assert bypass_event(now, "night", ended_since_prev=False).kind == "bypass"
    assert bypass_event(now, "break", ended_since_prev=False).kind == "bypass"
    assert bypass_event(now, "night", ended_since_prev=True) is None
    assert bypass_event(now, "ok", ended_since_prev=False) is None
    assert bypass_event(now, None, ended_since_prev=False) is None


def test_reboot_during_lock_is_not_a_bypass():
    now = datetime(2026, 10, 26, 3, 0)
    assert bypass_event(now, "night", ended_since_prev=False, rebooted=True).kind == "restart_during_lock"


def test_clock_jump_at_night_is_bypass():
    core = Core(NORMAL, datetime(2026, 10, 26, 3, 0))
    t = Ticker(core, datetime(2026, 10, 26, 3, 0))
    events = core.tick(t.now + timedelta(seconds=1 - 3600), 0.0, t.ticks + 1000, t.wall + 3601)
    assert kinds(events) == ["clock_change", "bypass"]


def test_clock_jump_in_daytime_is_not_bypass():
    core = Core(NORMAL, datetime(2026, 10, 25, 10, 0))
    t = Ticker(core, datetime(2026, 10, 25, 10, 0))
    events = core.tick(t.now + timedelta(seconds=1 - 3600), 0.0, t.ticks + 1000, t.wall + 3601)
    assert kinds(events) == ["clock_change"]


def test_autumn_dst_switch_is_not_a_clock_change():
    core = Core(NORMAL, datetime(2026, 10, 26, 2, 59, 50))
    core.tick(datetime(2026, 10, 26, 2, 59, 50), 0.0, 10_000, 1_000.0)
    events = core.tick(datetime(2026, 10, 26, 2, 0, 20), 0.0, 40_000, 1_030.0)
    assert events == []


# Настройки

def test_settings_take_effect_only_in_the_morning():
    core = Core(NORMAL, datetime(2026, 10, 25, 12, 0))
    t = Ticker(core, datetime(2026, 10, 25, 12, 0))
    core.request_settings(replace(NORMAL, break_interval_min=30), t.now)
    t.run(19 * 3600 + 58 * 60, step=60)
    assert core.s.break_interval_min == 60
    events = t.run(120, step=60)
    assert core.s.break_interval_min == 30
    assert "settings_changed" in kinds(events)


def test_test_mode_ignores_settings_changes():
    core = Core(short_settings(), datetime(2026, 10, 25, 12, 0))
    core.request_settings(replace(NORMAL, break_interval_min=30), datetime(2026, 10, 25, 12, 0))
    assert core.s.break_interval_min == 2


def test_key_matches_only_the_right_secret():
    expected = key_hash("abc123")
    assert key_matches("abc123\n", expected)
    assert not key_matches("other", expected)
    assert not key_matches("abc123", "")


# Тестовый режим

def test_test_mode_schedule_is_short():
    core = Core(short_settings(), datetime(2026, 10, 25, 10, 0))
    t = Ticker(core, datetime(2026, 10, 25, 10, 0))
    t.run(3 * 60)
    assert core.report_open
    core.report_closed()
    t.run(60)
    assert core.lock_visible
    events = t.run(120)
    assert not core.lock_visible
    assert "night_lock_end" in kinds(events)


def test_test_mode_never_sleeps():
    core = Core(replace(short_settings(), sleep_after_min=0.05), datetime(2026, 10, 25, 10, 0))
    t = Ticker(core, datetime(2026, 10, 25, 10, 0))
    t.run(4 * 60)
    assert core.lock_visible
    t.run(60)
    assert not core.take_sleep()


def test_break_on_screen_is_cancelled_when_the_night_starts():
    core = Core(NORMAL, datetime(2026, 10, 26, 1, 44, 30))
    core.restore_active(3600)
    t = Ticker(core, datetime(2026, 10, 26, 1, 44, 30))
    assert core.break_visible
    events = t.run(60)
    assert not core.break_visible
    assert [(e.kind, e.payload) for e in events if e.kind == "break_cancelled"] == [
        ("break_cancelled", {"reason": "night"})
    ]


def test_no_break_right_after_the_morning():
    core = Core(NORMAL, datetime(2026, 10, 26, 1, 40))
    core.restore_active(3550)
    t = Ticker(core, datetime(2026, 10, 26, 1, 40))
    t.run(6 * 3600 + 21 * 60)
    assert not core.break_visible
    assert core.active_s < 3600


def test_report_is_closed_when_the_night_lock_appears():
    core = Core(NORMAL, datetime(2026, 10, 26, 1, 44, 50))
    t = Ticker(core, datetime(2026, 10, 26, 1, 44, 50))
    t.run(60)
    assert core.report_open
    t.run(14 * 60)
    assert core.report_open
    t.run(10)
    assert core.lock_visible
    assert not core.report_open


def test_night_that_starts_before_midnight():
    s = replace(NORMAL, report_time="23:15", reminder_time="23:25", night_time="23:30", morning_time="07:30")
    core = Core(s, datetime(2026, 10, 25, 12, 0))
    assert not core.in_night(datetime(2026, 10, 25, 23, 14))
    assert core.in_night(datetime(2026, 10, 25, 23, 40))
    assert core.in_night(datetime(2026, 10, 26, 7, 29))
    assert not core.in_night(datetime(2026, 10, 26, 7, 30))
    assert not usb_key_allowed(datetime(2026, 10, 25, 23, 40), s)
    assert usb_key_allowed(datetime(2026, 10, 25, 12, 0), s)


def test_report_day_for_a_night_before_midnight():
    from app.report import bounds

    s = replace(NORMAL, report_time="23:15", reminder_time="23:25", night_time="23:30", morning_time="07:30")
    assert bounds(datetime(2026, 10, 25).date(), s) == (datetime(2026, 10, 25, 7, 30), datetime(2026, 10, 25, 23, 30))


def test_counter_is_restored_only_from_a_fresh_heartbeat():
    now = datetime(2026, 10, 26, 9, 0)
    assert restored_active(50.0, now - timedelta(minutes=2), now, 5) == 50.0
    assert restored_active(50.0, now - timedelta(hours=7), now, 5) == 0.0


def test_idle_during_a_break_does_not_close_it():
    core = Core(NORMAL, datetime(2026, 10, 25, 10, 0))
    t = Ticker(core, datetime(2026, 10, 25, 10, 0))
    t.run(3600)
    assert core.break_visible
    events = t.run(10 * 60, idle=600)
    assert core.break_visible
    assert "idle_break" not in kinds(events)
    t.run(60)
    assert kinds(core.water_drunk(t.now)) == ["water_drunk"]


def test_reminder_at_0155_is_logged():
    core = Core(NORMAL, datetime(2026, 10, 26, 1, 54, 50))
    t = Ticker(core, datetime(2026, 10, 26, 1, 54, 50))
    events = t.run(20)
    assert "reminder_shown" in kinds(events)


def test_test_escape_releases_the_night_lock():
    core = Core(short_settings(), datetime(2026, 10, 25, 10, 0))
    t = Ticker(core, datetime(2026, 10, 25, 10, 0))
    t.run(4 * 60)
    assert core.lock_visible
    events = core.test_escape(t.now)
    assert kinds(events) == ["unlock"]
    t.run(1)
    assert not core.lock_visible
