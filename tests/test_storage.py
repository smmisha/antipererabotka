from datetime import date, datetime

from app.config import Settings
from app.core import Event
from app.report import summary, week
from app.storage import Storage


def make_store(tmp_path):
    return Storage(tmp_path / "test.sqlite3")


def test_events_round_trip(tmp_path):
    db = make_store(tmp_path)
    db.log([Event("water_drunk", datetime(2026, 10, 25, 11, 0), {"stretch_s": 3600})])
    rows = db.events(datetime(2026, 10, 25, 8, 0), datetime(2026, 10, 26, 2, 0))
    assert rows == [(datetime(2026, 10, 25, 11, 0), "water_drunk", {"stretch_s": 3600})]
    db.close()


def test_heartbeat_and_ending_after_it(tmp_path):
    db = make_store(tmp_path)
    assert db.last_beat() is None
    db.beat(datetime(2026, 10, 26, 2, 0, 10), "night", 120.0)
    assert db.last_beat() == (datetime(2026, 10, 26, 2, 0, 10), "night", 120.0)
    assert not db.ended_since(datetime(2026, 10, 26, 2, 0, 10))
    db.log([Event("unlock", datetime(2026, 10, 26, 2, 5), {"method": "usb_code"})])
    assert db.ended_since(datetime(2026, 10, 26, 2, 0, 10))
    db.close()


def test_night_lock_end_after_a_heartbeat_is_an_ending(tmp_path):
    db = make_store(tmp_path)
    db.beat(datetime(2026, 10, 26, 0, 42, 19), "night", 50.0)
    db.log([Event("night_lock_end", datetime(2026, 10, 26, 0, 42, 25))])
    assert db.ended_since(datetime(2026, 10, 26, 0, 42, 19))
    db.close()


def test_summary_counts_the_day_from_0800_to_0200(tmp_path):
    db = make_store(tmp_path)
    db.add_active(datetime(2026, 10, 25, 9, 30), 1800)
    db.add_active(datetime(2026, 10, 26, 1, 30), 600)
    db.add_active(datetime(2026, 10, 26, 2, 30), 999)
    db.log([
        Event("water_drunk", datetime(2026, 10, 25, 10, 0), {"stretch_s": 3600}),
        Event("idle_break", datetime(2026, 10, 25, 12, 0), {"stretch_s": 5400}),
        Event("unlock", datetime(2026, 10, 26, 1, 0), {"method": "emergency"}),
        Event("bypass", datetime(2026, 10, 26, 1, 10), {"reason": "restart"}),
    ])
    result = summary(db, date(2026, 10, 25), Settings())
    assert result.active_h == 0.7
    assert result.water == 1
    assert result.longest_min == 90
    assert result.emergency == 1
    assert result.bypass == 1
    db.close()


def test_summary_counts_flash_unlocks_under_both_names(tmp_path):
    db = make_store(tmp_path)
    db.log([
        Event("unlock", datetime(2026, 10, 25, 22, 0), {"method": "usb"}),
        Event("unlock", datetime(2026, 10, 25, 23, 0), {"method": "usb_code"}),
    ])
    assert summary(db, date(2026, 10, 25), Settings()).usb == 2
    db.close()


def test_week_splits_activity_by_logical_day(tmp_path):
    db = make_store(tmp_path)
    db.add_active(datetime(2026, 10, 25, 9, 30), 3600)
    db.add_active(datetime(2026, 10, 26, 1, 30), 1800)
    days = dict(week(db, date(2026, 10, 25), Settings(), days=2))
    assert days == {date(2026, 10, 24): 0.0, date(2026, 10, 25): 1.5}
    db.close()
