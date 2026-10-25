"""Сводка за день и активные часы за неделю. День идёт от morning_time до night_time следующих суток."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from .config import Settings
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


def _hhmm(text: str) -> timedelta:
    hours, minutes = text.split(":")
    return timedelta(hours=int(hours), minutes=int(minutes))


def day_of(at: datetime, s: Settings) -> date:
    return (at - _hhmm(s.morning_time)).date()


def bounds(day: date, s: Settings) -> tuple[datetime, datetime]:
    midnight = datetime.combine(day, time())
    return midnight + _hhmm(s.morning_time), midnight + timedelta(days=1) + _hhmm(s.night_time)


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
    )


def week(store: Storage, today: date, s: Settings, days: int = 7) -> list[tuple[date, float]]:
    first = today - timedelta(days=days - 1)
    start, _ = bounds(first, s)
    _, end = bounds(today, s)
    totals = {first + timedelta(days=i): 0.0 for i in range(days)}
    for minute, secs in store.active_rows(start, end):
        day = day_of(minute, s)
        totals[day] = totals.get(day, 0.0) + secs
    return [(day, round(secs / 3600, 1)) for day, secs in totals.items()]
