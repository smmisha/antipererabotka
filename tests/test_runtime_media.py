from datetime import datetime
from types import SimpleNamespace

from app import runtime, winapi
from app.config import Settings
from app.core import Core
from app.runtime import Runtime

NOW = datetime(2026, 10, 29, 2, 0)
DAY = datetime(2026, 10, 29, 14, 0)


class DayClock(datetime):
    """datetime.now() всегда DAY: проверка не зависит от того, когда запущены тесты."""

    @classmethod
    def now(cls, tz=None):
        return DAY


class Quiet:
    """Окна, трей и хук заменены заглушками: любой вызов ничего не делает."""

    active = False

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class Journal:
    def __init__(self):
        self.events = []

    def log(self, events):
        self.events.extend(events)

    def pairs(self):
        return [(e.kind, e.payload) for e in self.events]


def runtime_with(core, journal):
    """Runtime без окон: _sync_ui трогает блокировку, трей, хук и журнал."""
    rt = Runtime.__new__(Runtime)
    rt.root = Quiet()
    rt.core = core
    rt.storage = journal
    rt.lock = Quiet()
    rt.report = Quiet()
    rt.tray = Quiet()
    rt.guard = Quiet()
    rt._lock_kind = None
    rt._break_view = lambda now: {}
    rt._night_view = lambda now: {}
    rt._tray_title = lambda now: ""
    return rt


def screen(break_visible=False, lock_visible=False):
    return SimpleNamespace(break_visible=break_visible, lock_visible=lock_visible, report_open=False)


def test_break_lock_pauses_media_once(monkeypatch):
    pauses = []
    monkeypatch.setattr(winapi, "call_in_progress", lambda: False)
    monkeypatch.setattr(winapi, "pause_media", lambda: pauses.append(NOW) or 2)
    journal = Journal()
    rt = runtime_with(screen(break_visible=True), journal)

    rt._sync_ui(NOW)
    rt._sync_ui(NOW)  # тот же перерыв на экране: второй раз не ставим

    assert len(pauses) == 1
    assert journal.pairs() == [("media_pause", {"paused": 2})]


def test_call_skips_media_pause(monkeypatch):
    pauses = []
    monkeypatch.setattr(winapi, "call_in_progress", lambda: True)
    monkeypatch.setattr(winapi, "pause_media", lambda: pauses.append(NOW))
    journal = Journal()
    rt = runtime_with(screen(lock_visible=True), journal)

    rt._sync_ui(NOW)

    assert pauses == []
    assert journal.pairs() == [("media_skipped", {"reason": "call"})]


def test_each_new_lock_pauses_again(monkeypatch):
    pauses = []
    monkeypatch.setattr(winapi, "call_in_progress", lambda: False)
    monkeypatch.setattr(winapi, "pause_media", lambda: pauses.append(NOW) or 0)
    core = screen(break_visible=True)
    rt = runtime_with(core, Journal())

    rt._sync_ui(NOW)  # перерыв появился
    core.break_visible = False
    rt._sync_ui(NOW)  # перерыв отложили или закрыли
    core.break_visible = True
    rt._sync_ui(NOW)  # перерыв вернулся: это новая блокировка
    core.break_visible = False
    core.lock_visible = True
    rt._sync_ui(NOW)  # ночная блокировка

    assert len(pauses) == 3


def test_step_postpones_break_while_a_call_runs(monkeypatch):
    monkeypatch.setattr(runtime, "datetime", DayClock)
    monkeypatch.setattr(winapi, "idle_seconds", lambda: 0.0)
    monkeypatch.setattr(winapi, "tick_ms", lambda: 0)
    monkeypatch.setattr(winapi, "call_in_progress", lambda: True)
    journal = Journal()
    core = Core(Settings(), DAY)
    core.active_s = 3600
    rt = runtime_with(core, journal)
    rt._reload_config = lambda now: None
    rt._process_actions = lambda now: None
    rt._persist = lambda now: None

    rt._step()

    assert not core.break_visible
    assert journal.pairs() == [("break_postponed", {"reason": "call"})]
