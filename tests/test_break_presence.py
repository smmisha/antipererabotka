"""Отсчёт перерыва по времени отсутствия у экрана и модуль присутствия без настоящей камеры."""

import time
from datetime import datetime, timedelta

from app.config import Settings
from app.core import Core
from app.presence import Presence

START = datetime(2026, 11, 2, 12, 0)
SETTINGS = Settings(break_interval_min=60, break_min_lock_s=300)
STEP_S = 5  # больше MAX_STEP_S в core не даём, иначе время урежется


def start_break():
    core = Core(SETTINGS, START)
    now, ticks, wall = START, 10_000, 1_700_000_000.0
    core.tick(now, 0.0, ticks, wall)  # первый вызов только запоминает отметку времени
    core.active_s = 60 * 60  # час активной работы: перерыв пора показывать на следующем шаге
    now, ticks, wall = advance(core, now, ticks, wall, STEP_S, present=None)
    assert core.break_visible
    return core, now, ticks, wall


def advance(core, now, ticks, wall, seconds, present):
    for _ in range(int(seconds // STEP_S)):
        now += timedelta(seconds=STEP_S)
        ticks += STEP_S * 1000
        wall += STEP_S
        core.tick(now, 0.0, ticks, wall, present=present)
    return now, ticks, wall


def test_countdown_stops_while_person_is_at_screen():
    core, now, ticks, wall = start_break()
    now, ticks, wall = advance(core, now, ticks, wall, 100, present=True)
    assert core.break_wait_left(now) == 300


def test_countdown_runs_while_person_is_away():
    core, now, ticks, wall = start_break()
    now, ticks, wall = advance(core, now, ticks, wall, 100, present=True)
    now, ticks, wall = advance(core, now, ticks, wall, 60, present=False)
    assert core.break_wait_left(now) == 240


def test_unknown_presence_counts_time_as_before():
    core, now, ticks, wall = start_break()
    now, ticks, wall = advance(core, now, ticks, wall, 100, present=None)
    assert core.break_wait_left(now) == 200


def test_water_button_waits_for_time_away_not_wall_clock():
    core, now, ticks, wall = start_break()
    now, ticks, wall = advance(core, now, ticks, wall, 400, present=True)
    assert core.water_drunk(now) == []  # прошло 400 с, но все у экрана
    assert core.break_visible
    now, ticks, wall = advance(core, now, ticks, wall, 300, present=False)
    assert core.water_drunk(now) != []


class FakeCamera:
    def __init__(self):
        self.released = False

    def read(self):
        return True, object()

    def release(self):
        self.released = True


def test_presence_is_unknown_without_camera():
    presence = Presence(open_camera=lambda: None)
    presence.start()
    time.sleep(0.1)
    assert presence.present is None
    presence.stop()


def test_presence_reports_face_and_releases_camera():
    camera = FakeCamera()
    presence = Presence(open_camera=lambda: camera, has_face=lambda frame: True)
    presence.start()
    deadline = time.monotonic() + 2
    while presence.present is not True and time.monotonic() < deadline:
        time.sleep(0.05)
    assert presence.present is True
    presence.stop()
    assert presence.present is None
    assert camera.released
