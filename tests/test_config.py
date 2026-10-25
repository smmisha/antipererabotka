import re
from pathlib import Path

from app.config import DEFAULT_TEXTS, Settings, short_settings

APP = Path(__file__).resolve().parents[1] / "app"


def test_defaults_match_the_spec():
    s = Settings()
    assert s.break_interval_min == 60
    assert s.break_min_lock_s == 60
    assert s.snooze_min == 10
    assert s.idle_reset_min == 5
    assert (s.report_time, s.reminder_time, s.night_time, s.morning_time) == ("01:45", "01:55", "02:00", "08:00")
    assert s.sleep_after_min == 10
    assert s.emergency_len == 40
    assert s.emergency_wait_min == 10
    assert s.unlock_min == 30
    assert s.usb_unlock_min == 30
    assert s.flash_code_len == 40
    assert s.flash_code_period_min == 2
    assert s.test_mode is False


def test_test_profile_uses_short_intervals():
    s = short_settings()
    assert s.test_mode is True
    assert s.break_interval_min == 2
    assert s.break_min_lock_s == 15
    assert s.idle_reset_min == 1


def test_every_text_the_ui_uses_is_defined():
    used = set()
    for name in ("runtime.py", "__main__.py"):
        source = (APP / name).read_text(encoding="utf-8")
        used |= set(re.findall(r'\b(?:t|texts)\["(\w+)"\]', source))
    assert used
    assert used <= set(DEFAULT_TEXTS), used - set(DEFAULT_TEXTS)
