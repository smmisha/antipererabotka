"""SQLite: журнал событий, активное время по минутам, heartbeat. Все вызовы — из одного потока."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from .core import Event

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS events_ts ON events (ts);
CREATE TABLE IF NOT EXISTS activity (
    minute TEXT PRIMARY KEY,
    secs REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS heartbeat (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    ts TEXT NOT NULL,
    state TEXT NOT NULL,
    active_s REAL NOT NULL
);
"""


def _ts(at: datetime) -> str:
    return at.isoformat(timespec="seconds")


def _minute(at: datetime) -> str:
    return at.strftime("%Y-%m-%dT%H:%M")


class Storage:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=5)
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.commit()
        self.db.close()

    def log(self, events: list[Event]) -> None:
        rows = [(_ts(e.at), e.kind, json.dumps(e.payload, ensure_ascii=False)) for e in events]
        self.db.executemany("INSERT INTO events (ts, kind, payload) VALUES (?, ?, ?)", rows)
        self.db.commit()

    def events(self, start: datetime, end: datetime) -> list[tuple[datetime, str, dict]]:
        rows = self.db.execute(
            "SELECT ts, kind, payload FROM events WHERE ts >= ? AND ts < ? ORDER BY ts, id",
            (_ts(start), _ts(end)),
        ).fetchall()
        return [(datetime.fromisoformat(ts), kind, json.loads(payload)) for ts, kind, payload in rows]

    def add_active(self, at: datetime, secs: float) -> None:
        if secs <= 0:
            return
        self.db.execute(
            "INSERT INTO activity (minute, secs) VALUES (?, ?) "
            "ON CONFLICT (minute) DO UPDATE SET secs = secs + excluded.secs",
            (_minute(at), secs),
        )
        self.db.commit()

    def active_rows(self, start: datetime, end: datetime) -> list[tuple[datetime, float]]:
        rows = self.db.execute(
            "SELECT minute, secs FROM activity WHERE minute >= ? AND minute < ? ORDER BY minute",
            (_minute(start), _minute(end)),
        ).fetchall()
        return [(datetime.strptime(minute, "%Y-%m-%dT%H:%M"), secs) for minute, secs in rows]

    def beat(self, at: datetime, state: str, active_s: float) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO heartbeat (id, ts, state, active_s) VALUES (1, ?, ?, ?)",
            (_ts(at), state, active_s),
        )
        self.db.commit()

    def last_beat(self) -> tuple[datetime, str, float] | None:
        row = self.db.execute("SELECT ts, state, active_s FROM heartbeat WHERE id = 1").fetchone()
        if row is None:
            return None
        return datetime.fromisoformat(row[0]), row[1], row[2]

    def unlocked_since(self, at: datetime) -> bool:
        row = self.db.execute(
            "SELECT 1 FROM events WHERE kind = 'unlock' AND ts > ? LIMIT 1", (_ts(at),)
        ).fetchone()
        return row is not None
