"""Детектор ИИ-работы по дереву процессов. Без tkinter и без вызовов Windows: снимки и время передаются снаружи."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

WINDOW_S = 60.0  # окно, за которое считаем загрузку процессора и объём IO
# node.exe и python.exe — консольные агенты: считаем только если в командной строке есть одно из этих слов.
CONSOLE_NAMES = ("node.exe", "python.exe")
CONSOLE_WORDS = ("claude", "codex", "copilot", "gemini")


@dataclass(frozen=True)
class Proc:
    """Процесс внутри дерева. Пара (pid, время создания) отличает его от нового процесса с тем же pid."""

    key: tuple[int, float]
    cpu_s: float  # накопленное время процессора, с
    io_bytes: float  # накопленные прочитанные и записанные байты


@dataclass(frozen=True)
class Tree:
    """Процесс из ai_processes вместе со всеми потомками."""

    key: tuple[str, int, float]  # имя, pid и время создания корня
    name: str
    procs: tuple[Proc, ...]


@dataclass(frozen=True)
class Snapshot:
    """Снимок: время в секундах (time.monotonic) и деревья процессов."""

    at: float
    trees: tuple[Tree, ...]


@dataclass(frozen=True)
class TreeStat:
    name: str
    cpu_pct: float  # загрузка дерева за окно, в процентах одного ядра
    io_bytes: float  # прочитано и записано за окно
    active: bool


@dataclass(frozen=True)
class AiState:
    active: bool
    names: tuple[str, ...]  # имена активных деревьев, без повторов
    stats: tuple[TreeStat, ...]  # по одной записи на дерево снимка, в том же порядке


def counted_root(name: str, cmdline: str) -> bool:
    """Считать ли процесс корнем дерева. node.exe и python.exe — только если в командной строке есть ИИ."""
    if name.lower() not in CONSOLE_NAMES:
        return True
    text = cmdline.lower()
    return any(word in text for word in CONSOLE_WORDS)


def _find(snap: Snapshot, key: tuple) -> Tree | None:
    for tree in snap.trees:
        if tree.key == key:
            return tree
    return None


class AiWatch:
    """ИИ работает, если за последние WINDOW_S секунд дерево загружало процессор не меньше cpu_pct процентов
    одного ядра или прочитало и записало не меньше io_kb килобайт. Один короткий пик внутри окна не решает:
    считаем сумму за всё окно. Процесс, который появился или пропал внутри окна, считаем только там, где он виден в обоих соседних снимках."""

    def __init__(self, cpu_pct: float, io_kb: float, window_s: float = WINDOW_S) -> None:
        self.cpu_pct = cpu_pct
        self.io_bytes = io_kb * 1024
        self.window_s = window_s
        self._snaps: deque[Snapshot] = deque()

    def observe(self, snap: Snapshot) -> AiState:
        self._snaps.append(snap)
        while snap.at - self._snaps[0].at > self.window_s:
            self._snaps.popleft()
        stats = tuple(self._stat(tree) for tree in snap.trees)
        names = tuple(dict.fromkeys(stat.name for stat in stats if stat.active))
        return AiState(active=bool(names), names=names, stats=stats)

    def _stat(self, tree: Tree) -> TreeStat:
        cpu_s = io_bytes = 0.0
        snaps = list(self._snaps)
        for prev, cur in zip(snaps, snaps[1:]):
            before, after = _find(prev, tree.key), _find(cur, tree.key)
            if before is None or after is None:
                continue
            seen = {proc.key: proc for proc in before.procs}
            for proc in after.procs:
                old = seen.get(proc.key)
                if old is not None:  # отрицательная разница: процесс пересоздан или счётчик сбросился
                    cpu_s += max(0.0, proc.cpu_s - old.cpu_s)
                    io_bytes += max(0.0, proc.io_bytes - old.io_bytes)
        cpu_pct = cpu_s / self.window_s * 100
        active = cpu_pct >= self.cpu_pct or io_bytes >= self.io_bytes
        return TreeStat(tree.name, cpu_pct, io_bytes, active)


class QuietTimer:
    """Сколько времени ИИ не виден. Отсчёт идёт от последней активности и сбрасывается на каждой активности."""

    def __init__(self, since: datetime) -> None:
        self._since = since

    def update(self, now: datetime, active: bool) -> float:
        """Секунд тишины к моменту now."""
        if active:
            self._since = now
        return (now - self._since).total_seconds()


def watch(
    path: Path,
    read: Callable[[], list[Tree]],
    *,
    cpu_pct: float = 5.0,
    io_kb: float = 200.0,
    every_s: float = 15.0,
    rounds: int | None = None,
    now: Callable[[], datetime] = datetime.now,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Режим наблюдения для калибровки: раз в every_s секунд дописывает в файл CPU и IO каждого дерева
    и то, как их оценил бы детектор с порогами cpu_pct и io_kb. Ошибка чтения записывается строкой, наблюдение продолжается.
    Остановить: Ctrl+C."""
    path.parent.mkdir(parents=True, exist_ok=True)
    detector = AiWatch(cpu_pct, io_kb)
    with open(path, "a", encoding="utf-8") as log:
        log.write(f"# {now():%Y-%m-%d %H:%M:%S}: наблюдение, раз в {every_s:g} с, пороги {cpu_pct:g} %, {io_kb:g} КБ\n")
        done = 0
        while rounds is None or done < rounds:
            stamp = f"{now():%Y-%m-%d %H:%M:%S}"
            try:
                trees = read()
            except Exception as exc:
                log.write(f"{stamp}\tОШИБКА\t{exc!r}\n")
            else:
                state = detector.observe(Snapshot(clock(), tuple(trees)))
                if not trees:
                    log.write(f"{stamp}\t-\tнет процессов из ai_processes\n")
                for tree, stat in zip(trees, state.stats):
                    cpu_s = sum(proc.cpu_s for proc in tree.procs)
                    io_now_kb = sum(proc.io_bytes for proc in tree.procs) / 1024
                    log.write(
                        f"{stamp}\t{tree.name}\tpid={tree.key[1]}\tпроцессов={len(tree.procs)}"
                        f"\tcpu_s={cpu_s:.1f}\tio_kb={io_now_kb:.1f}"
                        f"\tcpu60={stat.cpu_pct:.1f}%\tio60_kb={stat.io_bytes / 1024:.1f}"
                        f"\tактивно={'да' if stat.active else 'нет'}\n"
                    )
            log.flush()
            done += 1
            if rounds is None or done < rounds:
                sleep(every_s)
