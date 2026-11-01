from datetime import datetime, timedelta

from app.ai_watch import AiWatch, Proc, QuietTimer, Snapshot, Tree, counted_root, watch


def make_tree(name="claude.exe", pid=10, cpu_s=0.0, io_bytes=0.0, procs=None):
    procs = procs if procs is not None else (Proc((pid, 1.0), cpu_s, io_bytes),)
    return Tree(key=(name.lower(), pid, 1.0), name=name, procs=procs)


def samples(detector, rows):
    """rows: (секунды, cpu_s, io_bytes) — одно дерево из одного процесса, снимки подряд."""
    return [detector.observe(Snapshot(at, (make_tree(cpu_s=cpu, io_bytes=io),))) for at, cpu, io in rows]


# Тест 1. Детектор

def test_tree_with_20_percent_cpu_is_active():
    states = samples(AiWatch(cpu_pct=5, io_kb=200), [(15 * i, 3.0 * i, 0.0) for i in range(5)])  # 3 с на 15 с = 20 %
    assert states[-1].active
    assert states[-1].names == ("claude.exe",)
    assert round(states[-1].stats[0].cpu_pct) == 20


def test_one_percent_cpu_and_10_kb_is_not_active():
    states = samples(AiWatch(cpu_pct=5, io_kb=200), [(15 * i, 0.15 * i, 2560.0 * i) for i in range(5)])
    assert not states[-1].active
    assert round(states[-1].stats[0].cpu_pct) == 1
    assert states[-1].stats[0].io_bytes == 10 * 1024


def test_one_tick_with_a_peak_does_not_count():
    rows = [(15 * i, 1.0 if i >= 2 else 0.0, 0.0) for i in range(9)]  # одна секунда CPU в одном тике, потом тишина до 120 с
    states = samples(AiWatch(cpu_pct=5, io_kb=200), rows)
    assert not any(state.active for state in states)


def test_sustained_small_load_inside_the_window_is_active():
    states = samples(AiWatch(cpu_pct=5, io_kb=200), [(15 * i, float(i), 0.0) for i in range(5)])  # по секунде за тик
    assert not states[1].active  # 1 с из 60 — 1,7 %
    assert states[-1].active  # 4 с из 60 — 6,7 %


def test_activity_leaves_the_window_after_a_minute():
    rows = [(0, 0.0, 0.0), (15, 15.0, 0.0), (30, 15.0, 0.0), (45, 15.0, 0.0), (60, 15.0, 0.0), (75, 15.0, 0.0)]
    states = samples(AiWatch(cpu_pct=5, io_kb=200), rows)
    assert states[4].active  # 15 с из 60 — 25 %
    assert not states[5].active  # первый тик выпал из окна


def test_io_of_200_kb_inside_the_window_is_active():
    states = samples(AiWatch(cpu_pct=5, io_kb=200), [(15 * i, 0.0, 51200.0 * i) for i in range(5)])
    assert states[-1].active
    assert states[-1].stats[0].cpu_pct == 0.0


def test_process_that_just_appeared_is_not_counted_until_the_next_tick():
    detector = AiWatch(cpu_pct=5, io_kb=200)
    first = Snapshot(0.0, (make_tree(procs=(Proc((10, 1.0), 0.0, 0.0),)),))
    second = Snapshot(15.0, (make_tree(procs=(Proc((10, 1.0), 0.0, 0.0), Proc((11, 2.0), 500.0, 0.0))),))
    detector.observe(first)
    assert not detector.observe(second).active


def test_exited_process_does_not_make_negative_or_false_activity():
    detector = AiWatch(cpu_pct=5, io_kb=200)
    detector.observe(Snapshot(0.0, (make_tree(procs=(Proc((10, 1.0), 0.0, 0.0), Proc((11, 2.0), 100.0, 0.0))),)))
    state = detector.observe(Snapshot(15.0, (make_tree(procs=(Proc((10, 1.0), 0.0, 0.0),)),)))
    assert not state.active
    assert state.stats[0].cpu_pct == 0.0


def test_console_agents_count_only_with_an_ai_word_in_the_command_line():
    assert counted_root("node.exe", r"node C:\x\@anthropic-ai\claude-code\cli.js")
    assert counted_root("python.exe", "python -m codex_helper")
    assert not counted_root("node.exe", "node server.js")
    assert not counted_root("python.exe", "python -m app")
    assert counted_root("Code.exe", "")  # редактор считается всегда


# Тест 2. Тишина

def test_quiet_timer_counts_from_the_last_activity():
    start = datetime(2026, 10, 26, 2, 0)
    timer = QuietTimer(start)
    assert timer.update(start + timedelta(seconds=599), False) == 599
    assert timer.update(start + timedelta(seconds=600), False) == 600


def test_activity_on_the_ninth_minute_resets_the_countdown():
    start = datetime(2026, 10, 26, 2, 0)
    timer = QuietTimer(start)
    assert timer.update(start + timedelta(minutes=9), True) == 0
    assert timer.update(start + timedelta(minutes=18, seconds=59), False) == 599
    assert timer.update(start + timedelta(minutes=19), False) == 600


# Калибровка: журнал наблюдения

def test_watch_writes_one_line_per_tree_and_goes_on_after_a_read_error(tmp_path):
    path = tmp_path / "ai_watch.log"
    answers = [[make_tree(cpu_s=5.0, io_bytes=2048)], RuntimeError("psutil недоступен"), []]

    def read():
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    watch(path, read, rounds=3, now=lambda: datetime(2026, 11, 1, 2, 0, 0), sleep=lambda seconds: None)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("# 2026-11-01 02:00:00")
    assert "claude.exe" in lines[1] and "cpu_s=5.0" in lines[1] and "io_kb=2.0" in lines[1]
    assert "ОШИБКА" in lines[2]
    assert "нет процессов" in lines[3]


def test_watch_waits_between_rounds_but_not_after_the_last_one(tmp_path):
    pauses = []
    watch(tmp_path / "ai_watch.log", lambda: [], rounds=3, every_s=15, sleep=pauses.append)
    assert pauses == [15, 15]


def test_watch_log_shows_the_sixty_second_load_per_tree(tmp_path):
    path = tmp_path / "ai_watch.log"
    ticks = iter(range(0, 1000, 15))
    cpu = iter([3.0 * i for i in range(5)])
    watch(
        path,
        lambda: [make_tree(cpu_s=next(cpu))],
        rounds=5,
        now=lambda: datetime(2026, 11, 1, 2, 0, 0),
        clock=lambda: float(next(ticks)),
        sleep=lambda seconds: None,
    )
    last = path.read_text(encoding="utf-8").splitlines()[-1]
    assert "cpu60=20.0%" in last
    assert "активно=да" in last
