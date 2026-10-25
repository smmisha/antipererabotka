import re
from pathlib import Path

import json

import pytest

from app.config import DEFAULT_TEXTS, Config, ConfigError, Settings, load, load_for_start, save, short_settings

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


def write_config(tmp_path, data):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "settings",
    [
        {"night_time": "02.00"},
        {"night_time": "25:00"},
        {"break_interval_min": "60"},
        {"break_interval_min": True},
        {"break_interval_min": 1},
        {"active_gap_s": 5},
        {"emergency_len": 4.5},
        {"flash_code_len": 100},
        {"report_time": "02:30", "reminder_time": "01:55"},
        {"night_time": "08:30"},
    ],
)
def test_invalid_settings_are_rejected(tmp_path, settings):
    with pytest.raises(ConfigError):
        load(write_config(tmp_path, {"settings": settings}))


def test_invalid_text_is_rejected(tmp_path):
    with pytest.raises(ConfigError):
        load(write_config(tmp_path, {"texts": {"break_title": 5}}))


def test_unknown_texts_are_ignored_and_missing_ones_use_defaults(tmp_path):
    cfg = load(write_config(tmp_path, {"texts": {"no_such_key": "x", "break_title": "Другой текст"}}))
    assert cfg.texts["break_title"] == "Другой текст"
    assert "no_such_key" not in cfg.texts
    assert cfg.texts["break_drunk"] == DEFAULT_TEXTS["break_drunk"]


def test_broken_json_at_start_uses_defaults_and_keeps_the_file(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{не json", encoding="utf-8")
    cfg, error = load_for_start(path)
    assert error is not None
    assert cfg.settings == Settings()
    assert path.read_text(encoding="utf-8") == "{не json"


def test_valid_file_starts_without_error(tmp_path):
    path = tmp_path / "config.json"
    save(path, Config())
    cfg, error = load_for_start(path)
    assert error is None
    assert cfg.settings == Settings()
