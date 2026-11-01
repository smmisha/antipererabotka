"""Детектор ИИ-работы по дереву процессов. Без tkinter и без вызовов Windows: снимки и время передаются снаружи."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

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


def counted_root(name: str, cmdline: str) -> bool:
    """Считать ли процесс корнем дерева. node.exe и python.exe — только если в командной строке есть ИИ."""
    if name.lower() not in CONSOLE_NAMES:
        return True
    text = cmdline.lower()
    return any(word in text for word in CONSOLE_WORDS)


def watch(
    path: Path,
    read: Callable[[], list[Tree]],
    *,
    every_s: float = 15.0,
    rounds: int | None = None,
    now: Callable[[], datetime] = datetime.now,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Режим наблюдения для калибровки: раз в every_s секунд дописывает в файл CPU и IO каждого дерева.
    Ошибка чтения записывается строкой, наблюдение продолжается. Остановить: Ctrl+C."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as log:
        log.write(f"# {now():%Y-%m-%d %H:%M:%S}: наблюдение, раз в {every_s:g} с\n")
        done = 0
        while rounds is None or done < rounds:
            stamp = f"{now():%Y-%m-%d %H:%M:%S}"
            try:
                trees = read()
            except Exception as exc:
                log.write(f"{stamp}\tОШИБКА\t{exc!r}\n")
            else:
                if not trees:
                    log.write(f"{stamp}\t-\tнет процессов из ai_processes\n")
                for tree in trees:
                    cpu_s = sum(p.cpu_s for p in tree.procs)
                    io_kb = sum(p.io_bytes for p in tree.procs) / 1024
                    log.write(
                        f"{stamp}\t{tree.name}\tpid={tree.key[1]}\tпроцессов={len(tree.procs)}"
                        f"\tcpu_s={cpu_s:.1f}\tio_kb={io_kb:.1f}\n"
                    )
            log.flush()
            done += 1
            if rounds is None or done < rounds:
                sleep(every_s)
