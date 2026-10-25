"""Счётчик сбоев цикла и повторы в error.log. Без tkinter и Windows, поэтому проверяется тестами."""

from __future__ import annotations

TRIP_AFTER = 10
ERROR_EVERY_S = 60.0


class FailCounter:
    """Сбои подряд. На десятом сбое сообщает о срабатывании. Удачный шаг сбрасывает счётчик."""

    def __init__(self, trip_after: int = TRIP_AFTER) -> None:
        self.trip_after = trip_after
        self.count = 0
        self.tripped = False

    def fail(self) -> bool:
        """True только на том сбое, после которого надо снять блокировку."""
        self.count += 1
        if not self.tripped and self.count >= self.trip_after:
            self.tripped = True
            return True
        return False

    def ok(self) -> bool:
        """True, если до этого сработал failsafe и теперь работа восстановлена."""
        recovered = self.tripped
        self.count = 0
        self.tripped = False
        return recovered


class ErrorThrottle:
    """Одинаковую ошибку пишем не чаще раза в минуту и считаем повторы."""

    def __init__(self, every_s: float = ERROR_EVERY_S) -> None:
        self.every_s = every_s
        self._text: str | None = None
        self._at = 0.0
        self._repeats = 0

    def should_write(self, text: str, now: float) -> tuple[bool, int]:
        """(писать ли, сколько повторов пропущено с прошлой записи)."""
        if text == self._text and now - self._at < self.every_s:
            self._repeats += 1
            return False, 0
        repeats, self._repeats = self._repeats, 0
        self._text, self._at = text, now
        return True, repeats
