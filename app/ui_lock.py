"""Блокирующие окна: перерыв на воду и ночная блокировка. По одному окну на каждый монитор."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable

from . import winapi

BG = "#15171c"
FG = "#e9e9ec"
MUTED = "#9aa1ac"
WARN = "#f0b060"
BUTTON = "#2d3440"
BUTTON_ACTIVE = "#3b4555"
CODE_BG = "#1e2229"
TITLE_FONT = ("Segoe UI", 36, "bold")
TEXT_FONT = ("Segoe UI", 18)
SMALL_FONT = ("Segoe UI", 13)
CODE_FONT = ("Consolas", 24, "bold")


class LockScreen:
    def __init__(
        self,
        root: tk.Tk,
        *,
        on_water: Callable[[], None],
        on_snooze: Callable[[], None],
        on_code: Callable[[str], bool],
        on_test_exit: Callable[[], None] | None = None,
    ) -> None:
        self.root = root
        self.on_water = on_water
        self.on_snooze = on_snooze
        self.on_code = on_code
        self.mode: str | None = None
        self._wins: list[tuple[tk.Toplevel, dict]] = []
        self._view: dict = {}
        self._after: str | None = None
        if on_test_exit is not None:
            root.bind_all("<Control-Shift-F12>", lambda _event: on_test_exit())

    def show(self, mode: str, view: dict) -> None:
        if self.mode != mode:
            self.hide()
            self.mode = mode
            self._build(mode)
        self._view = view
        for _win, w in self._wins:
            if mode == "break":
                _update_break(w, view)
            else:
                _update_night(w, view)

    def hide(self) -> None:
        if self._after is not None:
            self.root.after_cancel(self._after)
            self._after = None
        for win, _ in self._wins:
            win.destroy()
        self._wins = []
        self.mode = None

    def _build(self, mode: str) -> None:
        rects = winapi.monitor_rects() or [(0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight())]
        for x, y, width, height in rects:
            win = tk.Toplevel(self.root)
            win.overrideredirect(True)
            win.geometry(f"{width}x{height}+{x}+{y}")
            win.configure(bg=BG)
            win.attributes("-topmost", True)
            frame = tk.Frame(win, bg=BG)
            frame.pack(expand=True)
            if mode == "break":
                widgets = self._break_widgets(frame)
            else:
                widgets = self._night_widgets(frame)
            self._wins.append((win, widgets))
        self._after = self.root.after(1000, self._keep_front)
        if mode == "night" and self._wins:
            self._wins[0][1]["entry"].focus_force()

    def _keep_front(self) -> None:
        for win, _ in self._wins:
            win.attributes("-topmost", True)
            win.lift()
        if self._wins:
            win = self._wins[0][0]
            winapi.foreground(int(win.wm_frame(), 16))
            win.focus_force()
        self._after = self.root.after(1000, self._keep_front)

    def _break_widgets(self, frame: tk.Frame) -> dict:
        w = {
            "title": _label(frame, TITLE_FONT, FG),
            "countdown": _label(frame, TEXT_FONT, MUTED),
            "water": _button(frame, self.on_water),
            "snooze": _button(frame, self.on_snooze),
            "banner": _label(frame, SMALL_FONT, MUTED),
        }
        for row, name in enumerate(w):
            w[name].grid(row=row, column=0, pady=12)
        return w

    def _night_widgets(self, frame: tk.Frame) -> dict:
        w = {
            "title": _label(frame, TITLE_FONT, FG),
            "now": _label(frame, TEXT_FONT, MUTED),
            "code_label": _label(frame, SMALL_FONT, MUTED),
            "code": _label(frame, CODE_FONT, FG),
            "hint": _label(frame, SMALL_FONT, MUTED),
            "entry": _entry(frame),
            "button": _button(frame, lambda: None),
            "message": _label(frame, SMALL_FONT, WARN),
            "wait": _label(frame, TEXT_FONT, FG),
            "banner": _label(frame, SMALL_FONT, MUTED),
        }
        entry = w["entry"]
        w["button"].config(command=lambda: self._submit(entry, w["message"]))
        entry.bind("<Return>", lambda _event: self._submit(entry, w["message"]))
        for row, name in enumerate(w):
            w[name].grid(row=row, column=0, pady=10)
        return w

    def _submit(self, entry: tk.Entry, message: tk.Label) -> None:
        text = entry.get()
        entry.delete(0, "end")
        if not self.on_code(text):
            message.config(text=self._view.get("wrong", ""))


def _label(frame: tk.Frame, font: tuple, fg: str) -> tk.Label:
    return tk.Label(frame, font=font, fg=fg, bg=BG, wraplength=1100, justify="center")


def _button(frame: tk.Frame, command: Callable[[], None]) -> tk.Button:
    return tk.Button(
        frame,
        font=TEXT_FONT,
        bg=BUTTON,
        fg=FG,
        activebackground=BUTTON_ACTIVE,
        activeforeground=FG,
        relief="flat",
        padx=30,
        pady=10,
        command=command,
    )


def _entry(frame: tk.Frame) -> tk.Entry:
    entry = tk.Entry(
        frame,
        font=CODE_FONT,
        width=44,
        justify="center",
        bg=CODE_BG,
        fg=FG,
        insertbackground=FG,
        relief="flat",
    )
    # Вставка запрещена: код нужно набрать вручную.
    for sequence in ("<<Paste>>", "<Control-v>", "<Control-V>", "<Shift-Insert>", "<Button-3>"):
        entry.bind(sequence, lambda _event: "break")
    return entry


def _toggle(widget: tk.Widget, shown: bool) -> None:
    if shown:
        widget.grid()
    else:
        widget.grid_remove()


def _update_break(w: dict, view: dict) -> None:
    w["title"].config(text=view["title"])
    w["countdown"].config(text=view["countdown"])
    w["water"].config(text=view["water_text"], state="normal" if view["water_enabled"] else "disabled")
    w["snooze"].config(text=view["snooze"] or "")
    _toggle(w["snooze"], bool(view["snooze"]))
    w["banner"].config(text=view["banner"])
    _toggle(w["banner"], bool(view["banner"]))


def _update_night(w: dict, view: dict) -> None:
    waiting = bool(view["wait"])
    if w["code"].cget("text") != view["code"]:
        w["message"].config(text="")
    w["title"].config(text=view["title"])
    w["now"].config(text=view["now"])
    w["code_label"].config(text=view["code_label"])
    w["code"].config(text=view["code"])
    w["hint"].config(text=view["hint"])
    w["button"].config(text=view["button"])
    w["wait"].config(text=view["wait"])
    w["banner"].config(text=view["banner"])
    for name in ("code_label", "code", "hint", "entry", "button"):
        _toggle(w[name], not waiting)
    _toggle(w["message"], not waiting)
    _toggle(w["wait"], waiting)
    _toggle(w["banner"], bool(view["banner"]))
    w["entry"].config(state="normal")
