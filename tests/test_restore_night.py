from datetime import datetime

from app.config import Settings
from app.core import Core, Event
from app.runtime import Runtime
from app.storage import Storage


def test_counts_and_pending_wait_come_back_from_the_journal(tmp_path):
    storage = Storage(tmp_path / "journal.sqlite3")
    storage.log(
        [
            Event("unlock", datetime(2026, 10, 25, 2, 10), {"method": "usb_code"}),  # прошлая ночь: не считается
            Event("unlock", datetime(2026, 10, 26, 2, 15), {"method": "emergency"}),  # старая запись без принятия
            Event("unlock", datetime(2026, 10, 26, 2, 30), {"method": "usb_code"}),
            Event("emergency_accepted", datetime(2026, 10, 26, 2, 40)),
            Event("unlock", datetime(2026, 10, 26, 3, 40), {"method": "emergency"}),  # это разблокировка того кода
            Event("emergency_accepted", datetime(2026, 10, 26, 3, 45)),  # ожидание ещё идёт
        ]
    )
    now = datetime(2026, 10, 26, 4, 0)
    core = Core(Settings(), now)
    rt = Runtime.__new__(Runtime)
    rt.core = core
    rt.storage = storage

    rt._restore_night(now)

    assert core.flash_used == 1
    assert core.emergency_used == 3
    assert core.wait_until == datetime(2026, 10, 26, 4, 45)
