"""Окно «Отчёт перед сном» на масштабах Windows 100/125/150 %: помещается на экран, нижние кнопки видны, размер меняется."""

import tkinter as tk

import pytest

from app import ai_watch, config, winapi
from app.ui_report import ReportWindow

T = config.DEFAULT_TEXTS
AI_NAMES = ("Code.exe", "claude.exe", "codex.exe", "node.exe", "python.exe")
SCALES = [96 / 72, 120 / 72, 144 / 72]  # масштаб Windows 100 %, 125 %, 150 % (Tk считает пункты по DPI)


def view():
    return {
        "title": T["report_title"],
        "date": f"{T['report_day']}: 02.11.2026",
        "warning": T["report_warning"].format(night="02:00"),
        "lines": [
            T["stat_active"].format(hours=10.5),
            T["stat_water"].format(n=4),
            T["stat_snoozed"].format(n=1),
            T["stat_longest"].format(minutes=75),
            T["stat_emergency"].format(n=0),
            T["stat_usb"].format(n=0),
            T["stat_bypass"].format(n=0),
            T["stat_restart"].format(n=0),
            T["stat_ai"].format(n=0),
        ],
        "week_title": T["stat_week"],
        "week": [(f"{day:02d}.11", float(day)) for day in range(1, 8)],
        "countdown": T["report_left"].format(left="14:59"),
        "ai": {
            "checkbox": T["ai_checkbox"],
            "checked": True,
            "enabled": True,
            "note": T["ai_detected"].format(names=ai_watch.ai_titles(AI_NAMES)),
        },
        "button": T["report_ok"],
    }


@pytest.fixture(scope="module")
def root():
    """Один Tk на модуль: каждый новый Tk заново читает файлы Tcl, а на Windows это иногда срывается."""
    winapi.set_dpi_aware()  # как в приложении: масштаб Windows доходит до Tk
    tk_root = tk.Tk()
    tk_root.withdraw()
    yield tk_root
    tk_root.destroy()


@pytest.fixture
def open_report(root):
    opened = []

    def open_at(scale):
        root.tk.call("tk", "scaling", scale)
        rw = ReportWindow(root, on_close=lambda: None, on_ai=lambda on: None)
        rw.show(view())
        root.update()
        opened.append(rw)
        return rw

    yield open_at
    for rw in opened:
        rw.hide()


def descendants(widget, cls):
    for child in widget.winfo_children():
        if isinstance(child, cls):
            yield child
        yield from descendants(child, cls)


def first(widget, cls):
    return next(descendants(widget, cls))


@pytest.mark.parametrize("scale", SCALES)
def test_window_fits_screen_height(root, open_report, scale):
    rw = open_report(scale)
    screen_h = root.winfo_screenheight()
    assert rw.win.winfo_height() <= screen_h * 0.85
    assert rw.win.winfo_rooty() + rw.win.winfo_height() <= screen_h


@pytest.mark.parametrize("scale", SCALES)
def test_controls_stay_on_screen(root, open_report, scale):
    rw = open_report(scale)
    screen_h = root.winfo_screenheight()
    for widget in (rw._countdown, first(rw.win, tk.Button), first(rw.win, tk.Checkbutton)):
        assert widget.winfo_rooty() >= 0
        assert widget.winfo_rooty() + widget.winfo_height() <= screen_h


def test_window_keeps_its_height_when_ai_note_changes(root, open_report):
    rw = open_report(SCALES[1])
    height = rw.win.winfo_height()
    rw.show({**view(), "ai": {**view()["ai"], "enabled": False, "checked": False, "note": T["ai_not_detected"]}})
    root.update()
    assert rw.win.winfo_height() == height


def test_content_scrolls_when_taller_than_window(open_report):
    rw = open_report(SCALES[2])
    _, bottom = first(rw.win, tk.Canvas).yview()
    assert bottom < 1.0


def test_window_is_resizable_with_min_size(open_report):
    rw = open_report(SCALES[1])
    assert [int(value) for value in rw.win.resizable()] == [1, 1]
    assert rw.win.minsize() == (400, 500)


def test_narrow_window_does_not_clip_text(root, open_report):
    rw = open_report(SCALES[2])
    rw.win.geometry("400x500+0+0")
    root.update()
    width = rw.win.winfo_width()
    widgets = list(descendants(rw.win, (tk.Label, tk.Checkbutton, tk.Button)))
    assert widgets
    for widget in widgets:
        assert widget.winfo_width() <= width, widget.cget("text")


def test_mouse_wheel_scrolls_content(root, open_report):
    rw = open_report(SCALES[2])
    canvas = first(rw.win, tk.Canvas)
    top_before, _ = canvas.yview()
    rw.win.event_generate("<MouseWheel>", delta=-120)  # колёсико вниз
    root.update()
    top_after, _ = canvas.yview()
    assert top_after > top_before
