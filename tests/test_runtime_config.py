import os
from dataclasses import replace
from datetime import datetime

from app import config
from app.config import Settings
from app.core import Core
from app.failsafe import ErrorThrottle
from app.runtime import Runtime


def runtime_for(path, data):
    """Runtime без окна: _reload_config читает только эти поля."""
    rt = Runtime.__new__(Runtime)
    rt.cfg_path = path
    rt.data = data
    rt.test = False
    rt.storage = None
    rt.cfg = config.load(path)
    rt.core = Core(rt.cfg.settings, datetime(2026, 10, 26, 19, 0))
    rt._throttle = ErrorThrottle()
    rt._cfg_mtime = rt._mtime()
    rt._cfg_checked = 0.0
    return rt


def test_settings_file_that_could_not_be_read_is_read_again(tmp_path, monkeypatch):
    path = tmp_path / config.CONFIG_NAME
    config.save(path, config.Config())
    rt = runtime_for(path, tmp_path)

    changed = replace(config.Config(), settings=replace(Settings(), break_min_lock_s=300))
    config.save(path, changed)
    os.utime(path, ns=(10**18, 10**18 + 10**9))  # время изменения точно другое, чем при первом чтении

    real_load = config.load
    calls = []

    def load_busy_once(p):
        calls.append(p)
        if len(calls) == 1:
            raise PermissionError("файл занят другим процессом")
        return real_load(p)

    monkeypatch.setattr(config, "load", load_busy_once)
    now = datetime(2026, 10, 26, 19, 36)

    rt._reload_config(now)  # первое чтение не удалось
    assert rt.core._pending is None
    rt._cfg_checked = 0.0
    rt._reload_config(now)  # следующая проверка читает файл снова
    assert rt.core._pending is not None
    assert rt.core._pending[0].break_min_lock_s == 300
