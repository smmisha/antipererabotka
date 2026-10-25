"""Настройки и тексты интерфейса: config.json со значениями по умолчанию."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

APP_DIR = "antipererabotka"
CONFIG_NAME = "config.json"
DB_NAME = "antipererabotka.sqlite3"


@dataclass(frozen=True)
class Settings:
    """Настройки из config.json. Единицы указаны в имени поля."""

    break_interval_min: float = 60
    break_min_lock_s: float = 60
    snooze_min: float = 10
    idle_reset_min: float = 5
    report_time: str = "01:45"
    reminder_time: str = "01:55"
    night_time: str = "02:00"
    morning_time: str = "08:00"
    sleep_after_min: float = 10
    emergency_len: int = 40
    emergency_wait_min: float = 10
    unlock_min: float = 30
    usb_unlock_min: float = 30
    flash_code_len: int = 40
    flash_code_period_min: float = 2
    # Не хранится в файле: выставляет только профиль --test.
    test_mode: bool = False


def short_settings() -> Settings:
    """Короткие интервалы для --test."""
    return Settings(
        break_interval_min=2,
        break_min_lock_s=15,
        snooze_min=0.5,
        idle_reset_min=1,
        emergency_wait_min=20 / 60,
        unlock_min=0.5,
        usb_unlock_min=0.5,
        test_mode=True,
    )


DEFAULT_TEXTS = {
    "break_title": "Встань, пройдись и выпей воды",
    "break_wait": "Кнопки появятся через {sec} с",
    "break_drunk": "Вода выпита",
    "break_snooze": "Отложить на {minutes} минут",
    "report_title": "Отчёт перед сном",
    "report_warning": "В {night} экран заблокируется. Сохрани работу и закрой всё.",
    "report_ok": "Понятно",
    "report_left": "До блокировки: {left}",
    "report_day": "За сегодня",
    "stat_active": "За компьютером: {hours} ч",
    "stat_water": "Вода выпита: {n} раз",
    "stat_snoozed": "Перерывов отложено: {n}",
    "stat_longest": "Самый длинный отрезок без перерыва: {minutes} мин",
    "stat_emergency": "Экстренных разблокировок: {n}",
    "stat_bypass": "Обходов: {n}",
    "stat_week": "Активные часы за 7 дней",
    "night_title": "Пора спать. Разблокировка в {morning}",
    "night_now": "Сейчас {now}",
    "night_code_label": "Экстренный код",
    "night_code_hint": "Набери код вручную. Вставка запрещена.",
    "night_code_button": "Проверить код",
    "night_code_wrong": "Код не совпал. Набери ещё раз.",
    "night_wait": "Разблокировка через {left}",
    "flash_label": "Код с флешки",
    "flash_timer": "Новый код через {left}",
    "flash_missing": "Вставь флешку с ключом, чтобы увидеть код",
    "tray_break": "До перерыва: {min} мин",
    "tray_night": "Ночной режим до {morning}",
    "tray_stats": "Статистика за сегодня",
    "tray_settings": "Открыть настройки",
    "first_run_title": "Антипереработка",
    "first_run": (
        "Перерыв на воду каждые {interval} мин активной работы.\n"
        "Отчёт в {report}, блокировка в {night}, утро в {morning}.\n\n"
        "USB-ключ создаётся командой:\n"
        "python -m app --make-usb-key E:"
    ),
    "test_banner": "ТЕСТ",
}


@dataclass
class Config:
    settings: Settings = field(default_factory=Settings)
    texts: dict = field(default_factory=lambda: dict(DEFAULT_TEXTS))
    usb_key_sha256: str = ""


def data_dir(test_mode: bool = False) -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / APP_DIR
    return base / "test" if test_mode else base


def load(path: Path) -> Config:
    raw = json.loads(path.read_text(encoding="utf-8"))
    names = {f.name for f in fields(Settings)} - {"test_mode"}
    values = {k: v for k, v in raw.get("settings", {}).items() if k in names}
    texts = dict(DEFAULT_TEXTS)
    texts.update(raw.get("texts", {}))
    return Config(Settings(**values), texts, str(raw.get("usb_key_sha256", "")))


def save(path: Path, cfg: Config) -> None:
    settings = {k: v for k, v in asdict(cfg.settings).items() if k != "test_mode"}
    data = {"settings": settings, "texts": cfg.texts, "usb_key_sha256": cfg.usb_key_sha256}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_or_create(path: Path) -> Config:
    if not path.exists():
        cfg = Config()
        save(path, cfg)
        return cfg
    return load(path)
