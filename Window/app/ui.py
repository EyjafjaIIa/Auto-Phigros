"""界面基础件：配色、卡片、开关、标签页。"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont

# ---- 配色（深色，参考视频里的面板观感）----
BG = "#0e1014"
PANEL = "#161a21"
CARD = "#1d222b"
CARD_HOVER = "#242a35"
TEXT = "#e8ebf2"
DIM = "#8a93a6"
LINE = "#2a3040"
ACCENT = "#4f7cff"
ACCENT_DARK = "#3a5fd0"
GOOD = "#38c976"
WARN = "#f2b33d"
BAD = "#ef5b5b"

THEMES = {
    "深色": dict(bg="#0e1014", panel="#161a21", card="#1d222b", text="#e8ebf2", dim="#8a93a6", line="#2a3040"),
    "浅色": dict(bg="#f2f4f8", panel="#ffffff", card="#f7f9fc", text="#1a1f29", dim="#6b7385", line="#dde2ec"),
}

FONT_UI = "Microsoft YaHei UI"


def font(size: int = 10, weight: str = "normal") -> tuple:
    return (FONT_UI, size, weight)


class Toggle(tk.Canvas):
    """圆角开关（画布绘制，带简单动画）。"""

    def __init__(self, master, command=None, value: bool = False, **kw):
        super().__init__(master, width=48, height=26, highlightthickness=0, bd=0, bg=kw.pop("bg", CARD), **kw)
        self.command = command
        self.value = value
        self._pos = 1.0 if value else 0.0
        self.bind("<Button-1>", self._click)
        self.bind("<Configure>", lambda _e: self._draw())
        self._draw()

    def set(self, value: bool) -> None:
        if value == self.value:
            return
        self.value = value
        self._animate()

    def _click(self, _event) -> None:
        self.value = not self.value
        self._animate()
        if self.command:
            self.command(self.value)

    def _animate(self) -> None:
        target = 1.0 if self.value else 0.0
        step = 0.18 if target > self._pos else -0.18
        if abs(target - self._pos) <= abs(step):
            self._pos = target
            self._draw()
            return
        self._pos += step
        self._draw()
        self.after(12, self._animate)

    def _draw(self) -> None:
        self.delete("all")
        w = max(self.winfo_width(), 48)
        h = max(self.winfo_height(), 26)
        r = h / 2
        track = ACCENT if self.value else "#3a4152"
        self._pill(1, 1, w - 1, h - 1, r, track)
        cx = r + (w - 2 * r) * self._pos
        self.create_oval(cx - r + 3, 3, cx + r - 3, h - 3, fill="#ffffff", outline="")

    def _pill(self, x0, y0, x1, y1, r, color) -> None:
        self.create_oval(x0, y0, x0 + 2 * r, y1, fill=color, outline="")
        self.create_oval(x1 - 2 * r, y0, x1, y1, fill=color, outline="")
        self.create_rectangle(x0 + r, y0, x1 - r, y1, fill=color, outline="")


class Card(tk.Frame):
    """带标题和说明的一行卡片。"""

    def __init__(self, master, title: str, subtitle: str = "", **kw):
        super().__init__(master, bg=CARD, **kw)
        self.columnconfigure(0, weight=1)
        self.title = tk.Label(self, text=title, bg=CARD, fg=TEXT, font=font(12, "bold"), anchor="w")
        self.title.grid(row=0, column=0, sticky="w", padx=16, pady=(12, 0))
        self.subtitle = tk.Label(
            self, text=subtitle, bg=CARD, fg=DIM, font=font(9), anchor="w", justify="left"
        )
        self.subtitle.grid(row=1, column=0, sticky="w", padx=16, pady=(2, 12))
        self.body = tk.Frame(self, bg=CARD)
        self.body.grid(row=0, column=1, rowspan=2, sticky="e", padx=16, pady=10)

    def set_subtitle(self, text: str) -> None:
        self.subtitle.configure(text=text)


class TabBar(tk.Frame):
    def __init__(self, master, tabs: list[str], command, **kw):
        super().__init__(master, bg=kw.pop("bg", PANEL), **kw)
        self.command = command
        self.buttons: dict[str, tk.Label] = {}
        for name in tabs:
            label = tk.Label(self, text=name, bg=PANEL, fg=DIM, font=font(11, "bold"), padx=18, pady=8)
            label.pack(side="left")
            label.bind("<Button-1>", lambda _e, n=name: self.select(n))
            self.buttons[name] = label
        self.current = None

    def select(self, name: str) -> None:
        for key, label in self.buttons.items():
            active = key == name
            label.configure(fg=TEXT if active else DIM, bg=ACCENT if active else PANEL)
        self.current = name
        self.command(name)
