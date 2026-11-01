"""Сводка за день и активные часы за неделю. День идёт от morning_time до night_time следующих суток."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from .config import Settings
from .core import at_logical_day
from .storage import Storage


@dataclass(frozen=True)
class Summary:
    day: date
    active_h: float
    water: int
    snoozed: int
    longest_min: int
    emergency: int
    usb: int
    bypass: int
    clock: int
    restart: int
    ai_min: int


def _hhmm(text: str) -> timedelta:
    hours, minutes = text.split(":")
    return timedelta(hours=int(hours), minutes=int(minutes))


def day_of(at: datetime, s: Settings) -> date:
    return (at - _hhmm(s.morning_time)).date()


def bounds(day: date, s: Settings) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time()) + _hhmm(s.morning_time)
    return start, at_logical_day(start, s.night_time, s)


def summary(store: Storage, day: date, s: Settings) -> Summary:
    start, end = bounds(day, s)
    active_s = sum(secs for _, secs in store.active_rows(start, end))
    counts: Counter = Counter()
    longest_s = 0
    for _, kind, payload in store.events(start, end):
        counts[kind] += 1
        if kind in ("water_drunk", "idle_break"):
            longest_s = max(longest_s, payload.get("stretch_s", 0))
        if kind == "unlock":
            counts[f"unlock_{payload.get('method')}"] += 1
    return Summary(
        day=day,
        active_h=round(active_s / 3600, 1),
        water=counts["water_drunk"],
        snoozed=counts["break_snoozed"],
        longest_min=round(longest_s / 60),
        emergency=counts["unlock_emergency"],
        usb=counts["unlock_usb"] + counts["unlock_usb_code"],
        bypass=counts["bypass"],
        clock=counts["clock_change"],
        restart=counts["restart_during_lock"],
        ai_min=ai_minutes(store, day, s),
    )


def ai_minutes(store: Storage, day: date, s: Settings) -> int:
    """Ночью работал ИИ: отрезки ai_mode_start → ai_mode_end от ночной блокировки перед этим днём до конца дня.
    Отрезок без конца (журнал оборвался в режиме) не считаем."""
    start, end = bounds(day, s)
    night_start = at_logical_day(start - timedelta(days=1), s.night_time, s)
    total = 0.0
    since = None
    for at, kind, _ in store.events(night_start, end):
        if kind == "ai_mode_start":
            since = at
        elif kind == "ai_mode_end" and since is not None:
            total += (at - since).total_seconds()
            since = None
    return round(total / 60)


def week(store: Storage, today: date, s: Settings, days: int = 7) -> list[tuple[date, float]]:
    first = today - timedelta(days=days - 1)
    start, _ = bounds(first, s)
    _, end = bounds(today, s)
    totals = {first + timedelta(days=i): 0.0 for i in range(days)}
    for minute, secs in store.active_rows(start, end):
        day = day_of(minute, s)
        totals[day] = totals.get(day, 0.0) + secs
    return [(day, round(secs / 3600, 1)) for day, secs in totals.items()]
