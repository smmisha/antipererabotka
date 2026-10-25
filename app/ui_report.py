"""Окно отчёта: сводка за день, активные часы за неделю, отсчёт до блокировки."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable

from .ui_lock import BG, BUTTON, BUTTON_ACTIVE, FG, MUTED, SMALL_FONT, TEXT_FONT, WARN

BAR = "#3a7bd5"
REPORT_TITLE_FONT = ("Segoe UI", 26, "bold")


class ReportWindow:
    def __init__(self, root: tk.Tk, on_close: Callable[[], None]) -> None:
        self.root = root
        self.on_close = on_close
        self.win: tk.Toplevel | None = None
        self._countdown: tk.Label | None = None

    def show(self, view: dict) -> None:
        if self.win is None:
            self._build(view)
        self._countdown.config(text=view["countdown"])

    def hide(self) -> None:
        if self.win is not None:
            self.win.destroy()
            self.win = None
            self._countdown = None

    def _build(self, view: dict) -> None:
        win = tk.Toplevel(self.root)
        win.title(view["title"])
        win.configure(bg=BG)
        win.attributes("-topmost", True)
        win.resizable(False, False)
        win.protocol("WM_DELETE_WINDOW", self.on_close)
        body = tk.Frame(win, bg=BG, padx=36, pady=28)
        body.pack()
        _text(body, view["title"], REPORT_TITLE_FONT, FG).pack(anchor="w")
        _text(body, view["date"], TEXT_FONT, MUTED).pack(anchor="w", pady=(0, 12))
        if view["warning"]:
            _text(body, view["warning"], TEXT_FONT, WARN).pack(anchor="w", pady=(0, 12))
        for line in view["lines"]:
            _text(body, line, TEXT_FONT, FG).pack(anchor="w", pady=3)
        _text(body, view["week_title"], TEXT_FONT, MUTED).pack(anchor="w", pady=(18, 6))
        _week_chart(body, view["week"]).pack(anchor="w")
        self._countdown = _text(body, "", TEXT_FONT, WARN)
        self._countdown.pack(anchor="w", pady=(12, 0))
        tk.Button(
            body,
            text=view["button"],
            font=TEXT_FONT,
            bg=BUTTON,
            fg=FG,
            activebackground=BUTTON_ACTIVE,
            activeforeground=FG,
            relief="flat",
            padx=30,
            pady=10,
            command=self.on_close,
        ).pack(pady=(18, 0))
        self.win = win


def _text(frame: tk.Frame, text: str, font: tuple, fg: str) -> tk.Label:
    return tk.Label(frame, text=text, font=font, fg=fg, bg=BG, wraplength=520, justify="left")


def _week_chart(frame: tk.Frame, week: list[tuple[str, float]]) -> tk.Canvas:
    row = 28
    top = max((hours for _, hours in week), default=0.0) or 1.0
    canvas = tk.Canvas(frame, width=520, height=row * len(week), bg=BG, highlightthickness=0)
    for i, (label, hours) in enumerate(week):
        y = i * row
        length = int(300 * hours / top)
        canvas.create_text(0, y + 12, text=label, anchor="w", fill=MUTED, font=SMALL_FONT)
        canvas.create_rectangle(70, y + 4, 70 + length, y + 22, fill=BAR, outline="")
        canvas.create_text(80 + length, y + 12, text=f"{hours:g} ч", anchor="w", fill=FG, font=SMALL_FONT)
    return canvas
