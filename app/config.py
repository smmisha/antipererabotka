"""Настройки и тексты интерфейса: config.json со значениями по умолчанию."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

APP_DIR = "antipererabotka"
CONFIG_NAME = "config.json"
DB_NAME = "antipererabotka.sqlite3"
AI_LOG_NAME = "ai_watch.log"  # журнал калибровки --test --ai-watch
# Процессы ИИ-инструментов, имена без учёта регистра. node.exe и python.exe учитываются, только если в командной строке есть ИИ (см. ai_watch.counted_root).
AI_PROCESSES = ("claude.exe", "Code.exe", "codex.exe", "Antigravity.exe", "node.exe", "python.exe")

TIME_FIELDS = ("report_time", "reminder_time", "night_time", "morning_time", "ai_cap_time")
# Пределы значений из config.json. Значение вне пределов не применяется.
RANGES = {
    "break_interval_min": (5, 240),
    "break_min_lock_s": (0, 600),
    "snooze_min": (1, 60),
    "idle_reset_min": (1, 60),
    "active_gap_s": (10, 600),
    "sleep_after_min": (1, 120),
    "emergency_wait_min": (1, 60),
    "unlock_min": (1, 120),
    "usb_unlock_min": (1, 120),
    "flash_code_period_min": (1, 10),
    "emergency_code_period_min": (1, 10),
    "warn_before_lock_s": (60, 600),
    "ai_quiet_min": (1, 60),
    "ai_poll_s": (5, 60),
    "ai_cpu_pct": (1, 100),
    "ai_io_kb": (10, 100000),
}
INT_RANGES = {
    "emergency_len": (8, 80),
    "flash_code_len": (8, 80),
    "flash_unlocks_per_night": (1, 50),
    "emergency_unlocks_per_night": (1, 50),
}
BOOL_FIELDS = ("ai_mode_enabled",)
AI_PROCESSES_MAX = 20


class ConfigError(ValueError):
    """Неверный config.json. Причина пишется в журнал и в error.log."""


@dataclass(frozen=True)
class Settings:
    """Настройки из config.json. Единицы указаны в имени поля."""

    break_interval_min: float = 60
    break_min_lock_s: float = 300
    snooze_min: float = 10
    idle_reset_min: float = 5
    active_gap_s: float = 120
    report_time: str = "01:45"
    reminder_time: str = "01:55"
    night_time: str = "02:00"
    morning_time: str = "08:00"
    sleep_after_min: float = 10
    emergency_len: int = 40
    emergency_wait_min: float = 60
    emergency_code_period_min: float = 2
    emergency_unlocks_per_night: int = 3
    unlock_min: float = 30
    usb_unlock_min: float = 30
    flash_unlocks_per_night: int = 7
    flash_code_len: int = 40
    flash_code_period_min: float = 2
    warn_before_lock_s: float = 60
    # Режим «ИИ работает ночью». Пороги ai_cpu_pct и ai_io_kb ориентировочные, см. калибровку в README.
    ai_mode_enabled: bool = True
    ai_quiet_min: float = 10  # тишина, после которой режим «ИИ» заканчивается
    ai_cap_time: str = "04:00"  # режим «ИИ» заканчивается в это время, даже если ИИ работает
    ai_poll_s: float = 15
    ai_cpu_pct: float = 5  # загрузка дерева за 60 с, % одного ядра
    ai_io_kb: float = 200  # прочитано и записано деревом за 60 с, КБ
    ai_processes: tuple[str, ...] = AI_PROCESSES
    # Не хранится в файле: выставляет только профиль --test.
    test_mode: bool = False


def short_settings() -> Settings:
    """Короткие интервалы для --test."""
    return Settings(
        break_interval_min=2,
        break_min_lock_s=15,
        snooze_min=0.5,
        idle_reset_min=1,
        active_gap_s=30,
        emergency_wait_min=20 / 60,
        unlock_min=0.5,
        usb_unlock_min=0.5,
        warn_before_lock_s=10,
        ai_quiet_min=1,
        ai_poll_s=5,
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
    "stat_usb": "Разблокировок флешкой: {n}",
    "stat_bypass": "Обходов: {n}",
    "stat_restart": "Перезагрузок в блокировке: {n}",
    "stat_week": "Активные часы за 7 дней",
    "night_title": "Пора спать. Разблокировка в {morning}",
    "night_now": "Сейчас {now}",
    "night_code_label": "Экстренный код",
    "night_code_hint": "Набери код вручную. Вставка запрещена.",
    "night_code_button": "Проверить код",
    "night_code_wrong": "Код не совпал. Набери ещё раз.",
    "night_wait": "Разблокировка через {left}",
    "night_code_timer": "Новый код через {left}",
    "night_limit_usb": "С флешкой на эту ночь разблокировки закончились",
    "night_limit_emergency": "Без флешки на эту ночь разблокировки закончились",
    "warn_title": "Скоро блокировка",
    "warn_countdown": "Блокировка через {left}",
    "warn_text": "Сохрани работу и закрой всё.",
    "warn_button": "Понятно",
    "flash_label": "Код с флешки",
    "flash_timer": "Новый код через {left}",
    "flash_missing": "Вставь флешку с ключом, чтобы увидеть код",
    "tray_break": "До перерыва: {min} мин",
    "tray_night": "Ночной режим до {morning}",
    "tray_stats": "Статистика за сегодня",
    "tray_settings": "Открыть настройки",
    "tray_failsafe": "Ошибка: блокировка отключена, см. error.log",
    "ai_checkbox": "Сейчас в фоне работает ИИ",
    "ai_detected": "ИИ обнаружен: {names}",
    "ai_not_detected": "ИИ сейчас не обнаружен",
    "ai_dark": "ИИ работает. Ты спишь. Блокировка до {morning}",
    "stat_ai": "Ночью работал ИИ: {n} мин",
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


def _minutes(hhmm) -> int:
    if not isinstance(hhmm, str) or not re.fullmatch(r"\d{2}:\d{2}", hhmm):
        raise ConfigError(f"время должно быть в формате ЧЧ:ММ, получено {hhmm!r}")
    hours, minutes = int(hhmm[:2]), int(hhmm[3:])
    if hours > 23 or minutes > 59:
        raise ConfigError(f"время {hhmm} вне суток")
    return hours * 60 + minutes


def _check_settings(section) -> dict:
    if not isinstance(section, dict):
        raise ConfigError("settings должен быть объектом")
    names = {f.name for f in fields(Settings)} - {"test_mode"}
    values = {k: v for k, v in section.items() if k in names}
    for name, (lo, hi) in RANGES.items():
        if name in values:
            value = values[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= value <= hi:
                raise ConfigError(f"{name}: число от {lo} до {hi}, получено {value!r}")
    for name, (lo, hi) in INT_RANGES.items():
        if name in values:
            value = values[name]
            if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
                raise ConfigError(f"{name}: целое от {lo} до {hi}, получено {value!r}")
    for name in BOOL_FIELDS:
        if name in values and not isinstance(values[name], bool):
            raise ConfigError(f"{name}: true или false, получено {values[name]!r}")
    if "ai_processes" in values:
        procs = values["ai_processes"]
        if not isinstance(procs, list) or not 1 <= len(procs) <= AI_PROCESSES_MAX or not all(
            isinstance(p, str) and p.strip() for p in procs
        ):
            raise ConfigError(f"ai_processes: список имён из 1–{AI_PROCESSES_MAX} строк, получено {procs!r}")
        values["ai_processes"] = tuple(procs)
    for name in TIME_FIELDS:
        if name in values:
            _minutes(values[name])
    s = Settings(**values)
    morning = _minutes(s.morning_time)
    report, reminder, night = (((_minutes(t) - morning) % 1440) for t in (s.report_time, s.reminder_time, s.night_time))
    if not 0 < report <= reminder <= night < 1440:
        raise ConfigError("порядок времени: утро, отчёт, напоминание, блокировка, блокировка раньше следующего утра")
    cap = (_minutes(s.ai_cap_time) - morning) % 1440
    if not night < cap < 1440:
        raise ConfigError("ai_cap_time: позже блокировки и раньше следующего утра")
    return values


def _check_texts(section) -> dict:
    if not isinstance(section, dict):
        raise ConfigError("texts должен быть объектом")
    texts = dict(DEFAULT_TEXTS)
    for key, value in section.items():
        if key not in DEFAULT_TEXTS:
            continue  # неизвестные ключи игнорируем
        if not isinstance(value, str):
            raise ConfigError(f"текст {key} должен быть строкой")
        texts[key] = value
    return texts


def load(path: Path) -> Config:
    """Читает config.json и проверяет значения. Неверный файл даёт ConfigError, и ничего не применяется."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ConfigError("корень файла должен быть объектом")
    values = _check_settings(raw.get("settings", {}))
    texts = _check_texts(raw.get("texts", {}))
    usb = raw.get("usb_key_sha256", "")
    if not isinstance(usb, str):
        raise ConfigError("usb_key_sha256 должен быть строкой")
    return Config(Settings(**values), texts, usb)


def save(path: Path, cfg: Config) -> None:
    settings = {k: v for k, v in asdict(cfg.settings).items() if k != "test_mode"}
    data = {"settings": settings, "texts": cfg.texts, "usb_key_sha256": cfg.usb_key_sha256}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_for_start(path: Path) -> tuple[Config, str | None]:
    """Для запуска. Нет файла: создаём со значениями по умолчанию. Неверный файл не трогаем, работаем на значениях по умолчанию."""
    if not path.exists():
        cfg = Config()
        save(path, cfg)
        return cfg, None
    try:
        return load(path), None
    except (OSError, ValueError) as exc:
        return Config(), f"config.json: {exc}"
