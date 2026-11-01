from datetime import datetime

from app.ai_watch import Proc, Tree, counted_root, watch


def make_tree(name="claude.exe", pid=10, cpu_s=0.0, io_bytes=0.0):
    return Tree(key=(name.lower(), pid, 1.0), name=name, procs=(Proc((pid, 1.0), cpu_s, io_bytes),))


def test_console_agents_count_only_with_an_ai_word_in_the_command_line():
    assert counted_root("node.exe", r"node C:\x\@anthropic-ai\claude-code\cli.js")
    assert counted_root("python.exe", "python -m codex_helper")
    assert not counted_root("node.exe", "node server.js")
    assert not counted_root("python.exe", "python -m app")
    assert counted_root("Code.exe", "")  # редактор считается всегда


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
