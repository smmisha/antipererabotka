"""Звонок по журналу доступа Windows: Дискорд звонком не считается, остальные программы считаются."""

from app import winapi

MIC = winapi.CONSENT_STORE + "\\microphone"
ZOOM = r"C:#Program Files#Zoom#bin#Zoom.exe"
DISCORD = r"C:#Users#Michael#AppData#Local#Discord#app-1.0.9#Discord.exe"


class FakeKey:
    def __init__(self, values=None, subkeys=()):
        self.values = values or {}
        self.subkeys = list(subkeys)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_registry(monkeypatch, apps):
    """apps: программа -> (начало доступа, конец доступа). Конец None значит, что доступ ещё идёт."""
    tree = {
        MIC: FakeKey(subkeys=["NonPackaged"]),
        MIC + "\\NonPackaged": FakeKey(subkeys=list(apps)),
    }
    for name, (start, stop) in apps.items():
        values = {"LastUsedTimeStart": start}
        if stop is not None:
            values["LastUsedTimeStop"] = stop
        tree[f"{MIC}\\NonPackaged\\{name}"] = FakeKey(values)

    def open_key(hive, path):
        if path not in tree:
            raise OSError(path)
        return tree[path]

    def query_value(key, name):
        if name not in key.values:
            raise OSError(name)
        return (key.values[name],)

    def enum_key(key, index):
        if index >= len(key.subkeys):
            raise OSError(index)
        return key.subkeys[index]

    monkeypatch.setattr(winapi.winreg, "OpenKey", open_key)
    monkeypatch.setattr(winapi.winreg, "QueryValueEx", query_value)
    monkeypatch.setattr(winapi.winreg, "EnumKey", enum_key)


def test_zoom_is_a_call(monkeypatch):
    fake_registry(monkeypatch, {ZOOM: (200, 100)})  # доступ начался позже, чем закончился предыдущий
    assert winapi.call_in_progress()


def test_discord_is_not_a_call(monkeypatch):
    fake_registry(monkeypatch, {DISCORD: (200, 100)})
    assert not winapi.call_in_progress()


def test_zoom_still_counts_next_to_discord(monkeypatch):
    fake_registry(monkeypatch, {DISCORD: (200, 100), ZOOM: (300, None)})
    assert winapi.call_in_progress()


def test_finished_zoom_is_not_a_call(monkeypatch):
    fake_registry(monkeypatch, {ZOOM: (100, 200)})
    assert not winapi.call_in_progress()
