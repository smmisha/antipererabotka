"""Окно отчёта: сводка за день, активные часы за неделю, отсчёт до блокировки."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable

from .ui_lock import ACCENT, ACCENT_ACTIVE, BG, BRAND, FG, LINE, MUTED, ON_ACCENT, SMALL_FONT, TEXT_FONT, WARN, logo

BAR = ACCENT
REPORT_TITLE_FONT = ("Segoe UI", 26, "bold")
BRAND_FONT = ("Segoe UI", 16, "bold")
REPORT_MIN_SIZE = (400, 500)  # меньше не делаем: иначе текст и кнопки перестают помещаться
REPORT_MAX_HEIGHT = 0.8  # доля высоты экрана; остальное — заголовок окна и панель задач
REPORT_PAD_X = 36  # боковые отступы; это пиксели Tk, при масштабе Windows они не растут
WEEK_ROW = 28  # высота строки графика за 7 дней


class ReportWindow:
    def __init__(self, root: tk.Tk, on_close: Callable[[], None], on_ai: Callable[[bool], None]) -> None:
        self.root = root
        self.on_close = on_close
        self.on_ai = on_ai
        self.win: tk.Toplevel | None = None
        self._countdown: tk.Label | None = None
        self._ai_var: tk.BooleanVar | None = None
        self._ai_check: tk.Checkbutton | None = None
        self._ai_note: tk.Label | None = None
        self._wrapped: list[tk.Widget] = []  # подписи и галочка: переносятся по ширине окна

    def show(self, view: dict) -> None:
        if self.win is None:
            self._build(view)
        self._set_countdown(view["countdown"])
        ai = view.get("ai")
        if ai is not None and self._ai_check is not None:
            self._ai_var.set(ai["checked"])
            self._ai_check.config(text=ai["checkbox"], state="normal" if ai["enabled"] else "disabled")
            self._ai_note.config(text=ai["note"])

    def hide(self) -> None:
        if self.win is not None:
            self.win.destroy()
            self.win = None
            self._countdown = None
            self._ai_var = self._ai_check = self._ai_note = None
            self._wrapped = []

    def _ai_toggled(self) -> None:
        self.on_ai(bool(self._ai_var.get()))

    def _set_countdown(self, text: str) -> None:
        """Таймер виден только ночью. Пустую строку не держим: иначе между разделителем и галочкой остаётся пустой промежуток."""
        self._countdown.config(text=text)
        shown = self._countdown.winfo_manager() != ""
        if text and not shown:
            self._countdown.pack(anchor="w", before=self._below_countdown)
        elif not text and shown:
            self._countdown.pack_forget()

    def _build_ai(self, footer: tk.Frame, ai: dict) -> None:
        """Галочка режима «ИИ». Серая, пока ИИ не виден: поставить её без ИИ нельзя."""
        self._ai_var = tk.BooleanVar(master=footer, value=ai["checked"])
        self._ai_check = tk.Checkbutton(
            footer,
            text=ai["checkbox"],
            variable=self._ai_var,
            command=self._ai_toggled,
            font=TEXT_FONT,
            fg=FG,
            bg=BG,
            activeforeground=FG,
            activebackground=BG,
            selectcolor=ACCENT,
            disabledforeground=MUTED,
            highlightthickness=0,
            state="normal" if ai["enabled"] else "disabled",
        )
        self._ai_check.pack(anchor="w", pady=(12, 0))
        self._wrapped.append(self._ai_check)
        self._ai_note = self._label(footer, ai["note"], SMALL_FONT, MUTED)
        self._ai_note.pack(anchor="w")

    def _build(self, view: dict) -> None:
        win = tk.Toplevel(self.root)
        win.title(view["title"])
        win.configure(bg=BG)
        win.attributes("-topmost", True)
        win.resizable(True, True)
        win.minsize(*REPORT_MIN_SIZE)
        win.protocol("WM_DELETE_WINDOW", self.on_close)
        self._wrapped = []
        # Внизу всегда видны таймер, галочка ИИ и кнопка, даже когда окно маленькое.
        footer = tk.Frame(win, bg=BG, padx=REPORT_PAD_X)
        footer.pack(side="bottom", fill="x")
        _rule(footer).pack(fill="x", pady=(0, 14))
        self._countdown = self._label(footer, "", TEXT_FONT, WARN)
        if view.get("ai") is not None:
            self._build_ai(footer, view["ai"])
        ok = _action(footer, view["button"], self.on_close)
        ok.pack(pady=(18, 28))
        self._below_countdown = self._ai_check if self._ai_check is not None else ok
        self._set_countdown(view["countdown"])
        # Статистика и график прокручиваются, если окно ниже содержимого.
        scroll = tk.Scrollbar(win, orient="vertical")
        canvas = tk.Canvas(win, bg=BG, highlightthickness=0)
        scroll.configure(command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        body = tk.Frame(canvas, bg=BG, padx=REPORT_PAD_X, pady=28)
        item = canvas.create_window(0, 0, window=body, anchor="nw")
        body.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: self._fit_width(canvas, item, event.width))
        win.bind("<MouseWheel>", lambda event: canvas.yview_scroll(-1 if event.delta > 0 else 1, "units"))
        head = tk.Frame(body, bg=BG)
        head.pack(anchor="w", pady=(12, 0))  # ниже верхнего края окна, а не впритык к заголовку
        logo(head, BG, size=44).pack(side="left")
        self._label(head, BRAND, BRAND_FONT, FG).pack(side="left", padx=(14, 0))
        self._label(body, view["title"], REPORT_TITLE_FONT, FG).pack(anchor="w", pady=(18, 0))
        self._label(body, view["date"], TEXT_FONT, MUTED).pack(anchor="w", pady=(4, 0))
        _rule(body).pack(fill="x", pady=(18, 16))
        if view["warning"]:
            self._label(body, view["warning"], TEXT_FONT, WARN).pack(anchor="w", pady=(0, 14))
        for line in view["lines"]:
            self._label(body, line, TEXT_FONT, FG).pack(anchor="w", pady=3)
        _rule(body).pack(fill="x", pady=(18, 16))
        self._label(body, view["week_title"], TEXT_FONT, MUTED).pack(anchor="w", pady=(0, 8))
        _week_chart(body, view["week"]).pack(fill="x")
        self.win = win
        self._fit(win, body, footer, scroll)

    def _fit(self, win: tk.Toplevel, body: tk.Frame, footer: tk.Frame, scroll: tk.Scrollbar) -> None:
        """Окно по содержимому, но не выше REPORT_MAX_HEIGHT экрана, и по центру. Что не влезло, прокручивается."""
        win.update_idletasks()
        screen_w = win.winfo_screenwidth()
        screen_h = win.winfo_screenheight()
        width = min(body.winfo_reqwidth() + scroll.winfo_reqwidth(), screen_w)
        height = min(body.winfo_reqheight() + footer.winfo_reqheight(), int(screen_h * REPORT_MAX_HEIGHT))
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2)
        win.geometry(f"{width}x{height}+{x}+{y}")

    def _fit_width(self, canvas: tk.Canvas, item: int, width: int) -> None:
        """Содержимое занимает ширину окна, подписи переносятся внутри неё."""
        canvas.itemconfigure(item, width=width)
        for widget in self._wrapped:
            widget.config(wraplength=max(160, width - 2 * REPORT_PAD_X))

    def _label(self, frame: tk.Frame, text: str, font: tuple, fg: str) -> tk.Label:
        label = _text(frame, text, font, fg)
        self._wrapped.append(label)
        return label


class WarningWindow:
    """Предупреждение перед блокировкой: окно поверх остальных, ввод не перехватывает."""

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
        logo(body, BG, size=48).pack(anchor="w")
        _text(body, view["title"], REPORT_TITLE_FONT, WARN).pack(anchor="w", pady=(14, 0))
        _text(body, view["text"], TEXT_FONT, FG).pack(anchor="w", pady=(6, 14))
        _rule(body).pack(fill="x", pady=(0, 14))
        self._countdown = _text(body, "", TEXT_FONT, WARN)
        self._countdown.pack(anchor="w")
        _action(body, view["button"], self.on_close).pack(pady=(18, 0))
        self.win = win


def _rule(frame: tk.Frame) -> tk.Frame:
    """Тонкая линия-разделитель между блоками."""
    return tk.Frame(frame, height=1, bg=LINE)


def _action(frame: tk.Frame, text: str, command: Callable[[], None]) -> tk.Button:
    """Главная кнопка окна: синяя, как основное действие в окне блокировки."""
    return tk.Button(
        frame,
        text=text,
        font=TEXT_FONT,
        bg=ACCENT,
        fg=ON_ACCENT,
        activebackground=ACCENT_ACTIVE,
        activeforeground=ON_ACCENT,
        relief="flat",
        padx=30,
        pady=10,
        command=command,
    )


def _text(frame: tk.Frame, text: str, font: tuple, fg: str) -> tk.Label:
    return tk.Label(frame, text=text, font=font, fg=fg, bg=BG, wraplength=520, justify="left")


def _week_chart(frame: tk.Frame, week: list[tuple[str, float]]) -> tk.Canvas:
    canvas = tk.Canvas(frame, width=520, height=WEEK_ROW * len(week), bg=BG, highlightthickness=0)
    canvas.bind("<Configure>", lambda event: _draw_week(canvas, week, event.width))
    return canvas


def _draw_week(canvas: tk.Canvas, week: list[tuple[str, float]], width: int) -> None:
    """Полосы умещаются в ширину канваса: при узком окне график не выходит за край."""
    canvas.delete("all")
    top = max((hours for _, hours in week), default=0.0) or 1.0
    room = max(60, width - 220)  # 70 px под дату слева, 150 px справа под подпись «N ч»
    for i, (label, hours) in enumerate(week):
        y = i * WEEK_ROW
        length = int(room * hours / top)
        canvas.create_text(0, y + 12, text=label, anchor="w", fill=MUTED, font=SMALL_FONT)
        canvas.create_rectangle(70, y + 4, 70 + length, y + 22, fill=BAR, outline="")
        canvas.create_text(80 + length, y + 12, text=f"{hours:g} ч", anchor="w", fill=FG, font=SMALL_FONT)
