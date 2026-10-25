"""Точка входа: python -m app [--test] [--make-usb-key ДИСК] [--test-key-dir ПАПКА]."""

from __future__ import annotations

import argparse
import os
import re
import tkinter as tk
from datetime import datetime
from tkinter import messagebox

from . import config, winapi
from .core import key_hash, new_usb_secret, usb_key_allowed
from .runtime import Runtime
from .storage import Storage

MUTEX_NORMAL = "Local\\antipererabotka-main"
MUTEX_TEST = "Local\\antipererabotka-test"


def make_usb_key(target: str, test: bool) -> int:
    cfg_path = config.data_dir(test) / config.CONFIG_NAME
    cfg = config.load_or_create(cfg_path)
    settings = config.short_settings() if test else cfg.settings
    if not usb_key_allowed(datetime.now(), settings):
        print("Ключ нельзя создать с 01:45 до 08:00. Сделай его днём.")
        return 1
    drive = re.fullmatch(r"([A-Za-z]):\\?", target)
    if drive:
        root = f"{drive.group(1).upper()}:\\"
        if root not in winapi.removable_roots([]):
            print(f"{root} — не съёмный диск. Вставь флешку и укажи её букву.")
            return 1
    elif test and os.path.isdir(target):
        root = os.path.join(target, "")
    else:
        print("Укажи букву флешки, например E:")
        return 1
    secret = new_usb_secret()
    winapi.write_key(root, secret)
    cfg.usb_key_sha256 = key_hash(secret)
    config.save(cfg_path, cfg)
    print(f"Ключ записан: {root}{winapi.KEY_FILE}. Старый ключ больше не работает.")
    print("Флешку с ключом храни отдельно от ноутбука.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app", description="Блокировка экрана для воды и сна.")
    parser.add_argument("--test", action="store_true", help="тестовый режим: короткие интервалы, отдельная база")
    parser.add_argument("--make-usb-key", metavar="ДИСК", help="создать USB-ключ, например E:")
    parser.add_argument("--test-key-dir", metavar="ПАПКА", help="тест: папка, которая считается съёмным диском")
    args = parser.parse_args(argv)
    if args.make_usb_key:
        return make_usb_key(args.make_usb_key, args.test)

    data = config.data_dir(args.test)
    cfg_path = data / config.CONFIG_NAME
    first_run = not cfg_path.exists()
    cfg = config.load_or_create(cfg_path)
    winapi.set_dpi_aware()
    instance = winapi.single_instance(MUTEX_TEST if args.test else MUTEX_NORMAL)
    if instance is None:
        return 0
    storage = Storage(data / config.DB_NAME)
    root = tk.Tk()
    root.withdraw()
    if first_run and not args.test:
        text = cfg.texts["first_run"].format(
            interval=int(cfg.settings.break_interval_min),
            report=cfg.settings.report_time,
            night=cfg.settings.night_time,
            morning=cfg.settings.morning_time,
        )
        messagebox.showinfo(cfg.texts["first_run_title"], text, parent=root)
    runtime = Runtime(
        root,
        data=data,
        cfg=cfg,
        storage=storage,
        test=args.test,
        test_key_dir=args.test_key_dir,
    )
    runtime.start()
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        runtime.shutdown()
        storage.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
