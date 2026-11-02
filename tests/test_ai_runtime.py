from datetime import date, datetime, timedelta

from app import ai_watch, winapi
from app.config import Config, Settings
from app.core import Core, Event
from app.failsafe import ErrorThrottle
from app.report import ai_minutes
from app.runtime import Runtime
from app.storage import Storage

NIGHT = datetime(2026, 10, 26, 2, 0)
DAY = datetime(2026, 10, 26, 14, 0)
ONE_SECOND = timedelta(seconds=1)


def tree(name, cpu_s):
    return ai_watch.Tree(key=(name.lower(), 10, 1.0), name=name, procs=(ai_watch.Proc((10, 1.0), cpu_s, 0.0),))


def night_runtime(tmp_path, core=None):
    """Runtime без окон: только то, что нужно опросу процессов и текстам."""
    rt = Runtime.__new__(Runtime)
    rt.data = tmp_path
    rt.core = core or Core(Settings(), NIGHT)
    rt.cfg = Config()
    rt.test = False
    rt.flash_secret = None
    rt._ai_watch = None
    rt._ai_next = 0.0
    rt._ai_last = (False, ())
    rt._throttle = ErrorThrottle()
    return rt


# Тест 11. Ошибка чтения процессов

def test_read_error_keeps_the_mode_off_and_writes_one_record(tmp_path, monkeypatch):
    rt = night_runtime(tmp_path)

    def broken(names):
        raise ImportError("нет psutil")

    monkeypatch.setattr(winapi, "ai_process_trees", broken)
    assert rt._poll_ai(NIGHT) == (False, ())
    rt._ai_next = 0.0  # следующий опрос сразу
    assert rt._poll_ai(NIGHT) == (False, ())
    assert (tmp_path / "error.log").read_text(encoding="utf-8").count("ImportError: нет psutil") == 1


def test_no_process_reading_outside_the_night(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(winapi, "ai_process_trees", lambda names: calls.append(names) or [])
    rt = night_runtime(tmp_path, Core(Settings(), DAY))
    assert rt._poll_ai(DAY) == (False, ())
    assert calls == []


def test_poll_reports_the_active_tree_names(tmp_path, monkeypatch):
    rt = night_runtime(tmp_path)
    cpu = iter([0.0, 12.0])  # 12 с из 60 — 20 % одного ядра
    monkeypatch.setattr(winapi, "ai_process_trees", lambda names: [tree("Code.exe", next(cpu))])
    rt._ai_next = 0.0
    assert rt._poll_ai(NIGHT) == (False, ())  # первый снимок, сравнивать не с чем
    rt._ai_next = 0.0
    assert rt._poll_ai(NIGHT) == (True, ("Code.exe",))


# Бодрствование Windows и вид блокировки

def test_keep_awake_follows_the_mode(monkeypatch):
    calls = []
    monkeypatch.setattr(winapi, "keep_awake", calls.append)
    core = Core(Settings(), datetime(2026, 10, 26, 1, 50))
    rt = night_runtime(None, core)
    now = datetime(2026, 10, 26, 1, 50)
    ticks, wall = 0, 1000.0
    core.tick(now, 0.0, ticks, wall, ai_active=True, ai_names=("claude.exe",))
    core.set_ai_request(True, now)
    for _ in range(10 * 60 + 1):  # до 02:00:01: режим включён
        now, ticks, wall = now + ONE_SECOND, ticks + 1000, wall + 1
        core.tick(now, 0.0, ticks, wall, ai_active=True, ai_names=("claude.exe",))
        rt._keep_awake()
    for _ in range(40 * 60):  # ИИ тихо, режим заканчивается через 10 минут
        now, ticks, wall = now + ONE_SECOND, ticks + 1000, wall + 1
        core.tick(now, 0.0, ticks, wall, ai_active=False)
        rt._keep_awake()
    changes = [value for i, value in enumerate(calls) if i == 0 or value != calls[i - 1]]
    assert changes == [False, True, False]


def test_dark_view_is_used_only_while_the_mode_runs():
    core = Core(Settings(), datetime(2026, 10, 26, 1, 50))
    core.tick(datetime(2026, 10, 26, 1, 50), 0.0, 0, 1000.0, ai_active=True, ai_names=("claude.exe",))
    core.set_ai_request(True, datetime(2026, 10, 26, 1, 50))
    core.tick(datetime(2026, 10, 26, 2, 0), 0.0, 600_000, 1600.0, ai_active=True, ai_names=("claude.exe",))
    view = night_runtime(None, core)._night_view(datetime(2026, 10, 26, 2, 0))
    assert view["ai_view"] is True
    assert view["title"] == "ИИ работает. Ты спишь. Блокировка до 08:00"

    plain = Core(Settings(), datetime(2026, 10, 26, 2, 0))
    plain.tick(datetime(2026, 10, 26, 2, 0), 0.0, 0, 1000.0)
    view = night_runtime(None, plain)._night_view(datetime(2026, 10, 26, 2, 0))
    assert view["ai_view"] is False
    assert view["title"] == "Пора спать. Разблокировка в 08:00"


# Окно отчёта: галочка и минуты ИИ

def test_checkbox_is_grey_without_ai_and_names_the_ai_when_seen(tmp_path):
    rt = night_runtime(tmp_path)
    night = datetime(2026, 10, 26, 2, 0)
    view = rt._ai_view(night)
    assert view["enabled"] is False and view["note"] == "ИИ сейчас не обнаружен"
    rt._ai_last = (True, ("Code.exe", "claude.exe", "node.exe", "python.exe"))
    view = rt._ai_view(night)
    assert view["enabled"] is True
    assert view["note"] == "ИИ обнаружен: VS Code, Claude, консольный агент"
    assert view["checked"] is False


def test_daytime_says_when_ai_is_checked_not_a_stale_night_result(tmp_path):
    rt = night_runtime(tmp_path)
    rt._ai_last = (True, ("Code.exe",))  # старое ночное значение не должно прорываться днём
    view = rt._ai_view(datetime(2026, 10, 26, 14, 0))
    assert view["enabled"] is False
    assert view["note"] == "Проверка ИИ идёт только ночью"


def test_ai_titles_are_short_and_unique():
    assert ai_watch.ai_titles(("Code.exe", "claude.exe", "node.exe", "python.exe")) == "VS Code, Claude, консольный агент"
    assert ai_watch.ai_titles(("Unknown.exe",)) == "Unknown.exe"


def test_report_counts_the_mode_of_the_night_before_the_day(tmp_path):
    db = Storage(tmp_path / "journal.sqlite3")
    db.log(
        [
            Event("ai_mode_start", datetime(2026, 10, 26, 2, 10), {"processes": ["claude.exe"]}),
            Event("ai_mode_end", datetime(2026, 10, 26, 2, 25), {"reason": "quiet"}),
            Event("ai_mode_start", datetime(2026, 10, 25, 2, 0), {"processes": []}),  # ночь перед предыдущим днём
            Event("ai_mode_end", datetime(2026, 10, 25, 2, 30), {"reason": "cap"}),
            Event("ai_mode_start", datetime(2026, 10, 26, 3, 0), {"processes": []}),  # журнал оборвался: не считаем
        ]
    )
    assert ai_minutes(db, date(2026, 10, 26), Settings()) == 15
    db.close()
