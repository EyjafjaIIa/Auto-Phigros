"""Phigros 自动打歌 · 桌面控制台（v1.0 / Window）

一个窗口完成：连接设备 → 选谱面 → 点开始 → 同步一次 → 自动打完。
"""

from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import ui
from device import REPO, Device, find_adb, load_config, save_config
import backup
import extract
import fake
from export_plan import build_events
from player import Session

DIFFS = ("EZ", "HD", "IN", "AT")
MAX_LOG_LINES = 400


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.cfg = load_config()
        self.theme = ui.THEMES["深色"]
        self.device: Device | None = None
        self.screen = (1920, 1080)
        self.charts: list[dict] = []
        self.filtered: list[dict] = []
        self.session: Session | None = None
        self.server_proc = None
        self.log_queue: queue.Queue[str] = queue.Queue()

        self.title("Phigros 自动打歌 · 控制台  v1.0")
        self.geometry("1240x760")
        self.minsize(1100, 660)
        self.configure(bg=ui.BG)

        self._build()
        self._load_charts()
        self._pump_log()

    # ------------------------------------------------------------------
    # 布局
    # ------------------------------------------------------------------
    def _build(self) -> None:
        header = tk.Frame(self, bg=ui.PANEL, height=64)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(
            header, text="Phigros 自动打歌", bg=ui.PANEL, fg=ui.TEXT, font=ui.font(16, "bold")
        ).pack(side="left", padx=(20, 8), pady=16)
        tk.Label(header, text="控制台 v1.0", bg=ui.PANEL, fg=ui.DIM, font=ui.font(10)).pack(
            side="left", pady=20
        )

        self.status_label = tk.Label(
            header, text="未连接", bg=ui.PANEL, fg=ui.DIM, font=ui.font(10)
        )
        self.status_label.pack(side="right", padx=20)

        self.tabbar = ui.TabBar(self, ["功能", "谱面库", "假课题", "存档", "设置", "主题", "关于"], self._switch_tab, bg=ui.PANEL)
        self.tabbar.pack(fill="x")

        body = tk.Frame(self, bg=ui.BG)
        body.pack(fill="both", expand=True, padx=18, pady=(12, 0))

        self.pages: dict[str, tk.Frame] = {}
        self.pages["功能"] = self._page_home(body)
        self.pages["谱面库"] = self._page_library(body)
        self.pages["假课题"] = self._page_fake(body)
        self.pages["存档"] = self._page_save(body)
        self.pages["设置"] = self._page_settings(body)
        self.pages["主题"] = self._page_theme(body)
        self.pages["关于"] = self._page_about(body)
        for page in self.pages.values():
            page.place(relwidth=1, relheight=1)

        log_wrap = tk.Frame(self, bg=ui.BG)
        log_wrap.pack(fill="x", padx=18, pady=(8, 14))
        tk.Label(log_wrap, text="日志", bg=ui.BG, fg=ui.DIM, font=ui.font(9)).pack(anchor="w")
        self.log_text = tk.Text(
            log_wrap,
            height=7,
            bg=ui.PANEL,
            fg="#c9d1e3",
            insertbackground=ui.TEXT,
            font=("Consolas", 9),
            relief="flat",
            bd=0,
            padx=10,
            pady=8,
            wrap="word",
        )
        self.log_text.pack(fill="x", pady=(4, 0))
        self.log_text.configure(state="disabled")

        self.tabbar.select("功能")

    def _switch_tab(self, name: str) -> None:
        for key, page in self.pages.items():
            if key == name:
                page.lift()
        if name == "谱面库":
            self._refresh_library()

    # -- 功能页 --------------------------------------------------------
    def _page_home(self, parent: tk.Frame) -> tk.Frame:
        page = tk.Frame(parent, bg=ui.BG)

        left = tk.Frame(page, bg=ui.BG)
        left.place(x=0, y=0, relheight=1, width=330)

        card = tk.Frame(left, bg=ui.CARD)
        card.pack(fill="x")
        tk.Label(card, text="设备", bg=ui.CARD, fg=ui.TEXT, font=ui.font(12, "bold")).pack(
            anchor="w", padx=16, pady=(12, 2)
        )
        self.device_text = tk.Label(
            card, text="点下面按钮连接手机", bg=ui.CARD, fg=ui.DIM, font=ui.font(9), justify="left"
        )
        self.device_text.pack(anchor="w", padx=16)
        self.connect_btn = tk.Button(
            card,
            text="连接设备",
            command=self._connect,
            bg=ui.ACCENT,
            fg="white",
            relief="flat",
            bd=0,
            font=ui.font(10, "bold"),
            padx=14,
            pady=6,
            activebackground=ui.ACCENT_DARK,
            activeforeground="white",
        )
        self.connect_btn.pack(anchor="w", padx=16, pady=12)

        card2 = tk.Frame(left, bg=ui.CARD)
        card2.pack(fill="x", pady=(12, 0))
        tk.Label(card2, text="谱面", bg=ui.CARD, fg=ui.TEXT, font=ui.font(12, "bold")).pack(
            anchor="w", padx=16, pady=(12, 2)
        )
        self.song_label = tk.Label(
            card2, text="还没选曲", bg=ui.CARD, fg=ui.DIM, font=ui.font(9), justify="left", wraplength=290
        )
        self.song_label.pack(anchor="w", padx=16, pady=(0, 8))
        self.export_btn = tk.Button(
            card2,
            text="导出到手机",
            command=self._export_selected,
            bg=ui.CARD_HOVER,
            fg=ui.TEXT,
            relief="flat",
            bd=0,
            font=ui.font(9, "bold"),
            padx=12,
            pady=5,
            activebackground=ui.LINE,
            activeforeground=ui.TEXT,
        )
        self.export_btn.pack(anchor="w", padx=16, pady=(0, 12))

        right = tk.Frame(page, bg=ui.BG)
        right.place(x=350, y=0, relwidth=1, width=-350, relheight=1)

        self.master_card = ui.Card(
            right, title="总开关", subtitle="关闭后不会下发任何触控"
        )
        self.master_card.pack(fill="x")
        self.master_toggle = ui.Toggle(self.master_card.body, command=self._on_master)
        self.master_toggle.pack()

        self.play_card = ui.Card(
            right, title="自动打歌", subtitle="已关闭：点右侧开关开始"
        )
        self.play_card.pack(fill="x", pady=(12, 0))
        self.play_toggle = ui.Toggle(self.play_card.body, command=self._on_play)
        self.play_toggle.pack()

        self.channel_card = ui.Card(
            right,
            title="手机端注入通道",
            subtitle="给手机 App 用：由电脑起注入服务端，手机不需要 root",
        )
        self.channel_card.pack(fill="x", pady=(12, 0))
        self.channel_toggle = ui.Toggle(self.channel_card.body, command=self._on_channel)
        self.channel_toggle.pack()
        self.phone_auto_btn = tk.Button(
            self.channel_card.body,
            text="自动同步开打",
            command=self._phone_autoplay,
            bg=ui.ACCENT,
            fg="#ffffff",
            relief="flat",
            bd=0,
            font=ui.font(10, "bold"),
            padx=16,
            pady=6,
            activebackground=ui.ACCENT,
            activeforeground="#ffffff",
        )
        self.phone_auto_btn.pack(pady=(10, 0))
        self.one_click_btn = tk.Button(
            self.channel_card.body,
            text="一键自动打歌",
            command=self._one_click,
            bg=ui.CARD_HOVER,
            fg=ui.TEXT,
            relief="flat",
            bd=0,
            font=ui.font(10, "bold"),
            padx=16,
            pady=6,
            activebackground=ui.LINE,
            activeforeground=ui.TEXT,
        )
        self.one_click_btn.pack(pady=(8, 0))

        delay_card = ui.Card(
            right,
            title="判定延迟",
            subtitle="自动播放推迟 / 提前（毫秒），打早了往左、打晚了往右",
        )
        delay_card.pack(fill="x", pady=(12, 0))
        box = tk.Frame(delay_card.body, bg=ui.CARD)
        box.pack()
        self.delay_var = tk.IntVar(value=int(self.cfg.get("delay_ms", 0)))
        self.delay_value = tk.Label(
            box, text=f"{self.delay_var.get():+d} ms", bg=ui.CARD, fg=ui.ACCENT, font=ui.font(11, "bold"), width=8
        )
        self.delay_value.pack(side="left")
        style = ttk.Style(self)
        style.configure("Delay.Horizontal.TScale", background=ui.CARD, troughcolor="#2b3242")
        self.delay_scale = ttk.Scale(
            box,
            from_=-300,
            to=300,
            orient="horizontal",
            length=220,
            style="Delay.Horizontal.TScale",
            command=self._on_delay,
        )
        self.delay_scale.set(self.delay_var.get())
        self.delay_scale.pack(side="left", padx=10)
        tk.Button(
            box,
            text="还原",
            command=lambda: self.delay_scale.set(0),
            bg=ui.CARD_HOVER,
            fg=ui.DIM,
            relief="flat",
            bd=0,
            font=ui.font(9),
            padx=10,
            pady=3,
            activebackground=ui.LINE,
            activeforeground=ui.TEXT,
        ).pack(side="left")

        hint = tk.Label(
            right,
            text="用法：手机停在选歌界面并选中曲目 → 打开「总开关」→ 打开「自动打歌」\n"
            "程序会点「开始」，进歌后在第一个音符压到判定线时按一次空格。",
            bg=ui.BG,
            fg=ui.DIM,
            font=ui.font(9),
            justify="left",
        )
        hint.pack(anchor="w", pady=(16, 0))
        return page

    # -- 谱面库 --------------------------------------------------------
    def _page_library(self, parent: tk.Frame) -> tk.Frame:
        page = tk.Frame(parent, bg=ui.BG)

        bar = tk.Frame(page, bg=ui.BG)
        bar.pack(fill="x")
        self.search_var = tk.StringVar()
        entry = tk.Entry(
            bar,
            textvariable=self.search_var,
            bg=ui.CARD,
            fg=ui.TEXT,
            insertbackground=ui.TEXT,
            relief="flat",
            font=ui.font(10),
        )
        entry.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 10))
        self.search_var.trace_add("write", lambda *_: self._refresh_library())

        self.diff_var = tk.StringVar(value="全部")
        for diff in ("全部",) + DIFFS:
            tk.Radiobutton(
                bar,
                text=diff,
                value=diff,
                variable=self.diff_var,
                command=self._refresh_library,
                bg=ui.BG,
                fg=ui.DIM,
                selectcolor=ui.CARD,
                activebackground=ui.BG,
                activeforeground=ui.TEXT,
                font=ui.font(9),
                indicatoron=False,
                padx=8,
                pady=4,
            ).pack(side="left", padx=2)

        wrap = tk.Frame(page, bg=ui.BG)
        wrap.pack(fill="both", expand=True, pady=(10, 0))
        self.listbox = tk.Listbox(
            wrap,
            bg=ui.PANEL,
            fg=ui.TEXT,
            selectbackground=ui.ACCENT,
            selectforeground="white",
            font=ui.font(10),
            relief="flat",
            bd=0,
            highlightthickness=0,
            activestyle="none",
        )
        self.listbox.pack(side="left", fill="both", expand=True)
        scroll = tk.Scrollbar(wrap, command=self.listbox.yview, bg=ui.PANEL)
        scroll.pack(side="right", fill="y")
        self.listbox.configure(yscrollcommand=scroll.set)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)
        self.library_count = tk.Label(page, text="", bg=ui.BG, fg=ui.DIM, font=ui.font(9))
        self.library_count.pack(anchor="w", pady=(6, 0))
        return page

    # -- 假课题 --------------------------------------------------------
    def _page_fake(self, parent: tk.Frame) -> tk.Frame:
        page = tk.Frame(parent, bg=ui.BG)
        tk.Label(page, text="假课题", bg=ui.BG, fg=ui.TEXT, font=ui.font(14, "bold")).pack(anchor="w")
        tk.Label(
            page,
            text="拿选中的谱面整活：镜像左右、乱序打散、整体偏移。\n生成的谱面会出现在「谱面库」里，名字带 _Mirror / _Shuffle / _Shift。",
            bg=ui.BG,
            fg=ui.DIM,
            font=ui.font(9),
            justify="left",
        ).pack(anchor="w", pady=(6, 14))

        self.fake_hint = tk.Label(
            page, text="先在「谱面库」里选一首", bg=ui.BG, fg=ui.WARN, font=ui.font(9)
        )
        self.fake_hint.pack(anchor="w", pady=(0, 10))

        row = tk.Frame(page, bg=ui.BG)
        row.pack(anchor="w")
        for mode, label in (("Mirror", "镜像"), ("Shuffle", "乱序"), ("Shift", "偏移")):
            tk.Button(
                row,
                text=label,
                command=lambda m=mode: self._make_fake(m),
                bg=ui.ACCENT,
                fg="white",
                relief="flat",
                bd=0,
                font=ui.font(10, "bold"),
                padx=22,
                pady=8,
                activebackground=ui.ACCENT_DARK,
                activeforeground="white",
            ).pack(side="left", padx=(0, 10))
        return page

    # -- 存档 ----------------------------------------------------------
    def _page_save(self, parent: tk.Frame) -> tk.Frame:
        page = tk.Frame(parent, bg=ui.BG)
        tk.Label(page, text="存档", bg=ui.BG, fg=ui.TEXT, font=ui.font(14, "bold")).pack(anchor="w")
        tk.Label(
            page,
            text="备份 / 恢复 config.json 与 calibration.json（判定延迟、校准值都在里面）。",
            bg=ui.BG,
            fg=ui.DIM,
            font=ui.font(9),
        ).pack(anchor="w", pady=(6, 12))

        row = tk.Frame(page, bg=ui.BG)
        row.pack(anchor="w", pady=(0, 10))
        tk.Button(
            row,
            text="立即备份",
            command=self._do_backup,
            bg=ui.ACCENT,
            fg="white",
            relief="flat",
            bd=0,
            font=ui.font(10, "bold"),
            padx=18,
            pady=6,
            activebackground=ui.ACCENT_DARK,
            activeforeground="white",
        ).pack(side="left", padx=(0, 10))
        tk.Button(
            row,
            text="恢复选中",
            command=self._do_restore,
            bg=ui.CARD_HOVER,
            fg=ui.TEXT,
            relief="flat",
            bd=0,
            font=ui.font(10),
            padx=18,
            pady=6,
            activebackground=ui.LINE,
            activeforeground=ui.TEXT,
        ).pack(side="left", padx=(0, 10))
        tk.Button(
            row,
            text="打开文件夹",
            command=self._open_backup_dir,
            bg=ui.CARD_HOVER,
            fg=ui.TEXT,
            relief="flat",
            bd=0,
            font=ui.font(10),
            padx=18,
            pady=6,
            activebackground=ui.LINE,
            activeforeground=ui.TEXT,
        ).pack(side="left")

        self.save_list = tk.Listbox(
            page,
            bg=ui.PANEL,
            fg=ui.TEXT,
            selectbackground=ui.ACCENT,
            selectforeground="white",
            font=ui.font(10),
            relief="flat",
            bd=0,
            highlightthickness=0,
            height=12,
        )
        self.save_list.pack(fill="both", expand=True, pady=(4, 0))
        self._refresh_saves()
        return page

    def _refresh_saves(self) -> None:
        if not hasattr(self, "save_list"):
            return
        self.save_list.delete(0, "end")
        self.backups = backup.list_backups()
        for folder in self.backups:
            self.save_list.insert("end", "  " + folder.name)
        if not self.backups:
            self.save_list.insert("end", "  （还没有备份）")

    def _do_backup(self) -> None:
        try:
            folder = backup.create_backup()
            self._log(f"已备份到 {folder}")
            self._refresh_saves()
        except Exception as exc:  # noqa: BLE001
            self._log(f"[错误] 备份失败：{exc}")

    def _do_restore(self) -> None:
        selection = self.save_list.curselection()
        if not selection or not getattr(self, "backups", None):
            self._log("先在列表里选一个备份")
            return
        folder = self.backups[selection[0]]
        try:
            restored = backup.restore_backup(folder)
            self._log(f"已从 {folder.name} 恢复：{', '.join(restored) or '无文件'}")
            self.cfg = load_config()
            if hasattr(self, "delay_scale"):
                self.delay_scale.set(int(self.cfg.get("delay_ms", 0)))
        except Exception as exc:  # noqa: BLE001
            self._log(f"[错误] 恢复失败：{exc}")

    def _open_backup_dir(self) -> None:
        import os

        backup.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(backup.BACKUP_DIR))
        except Exception as exc:  # noqa: BLE001
            self._log(f"[错误] 打不开文件夹：{exc}")

    def _make_fake(self, mode: str) -> None:
        if not hasattr(self, "selected"):
            self._log("先在「谱面库」里选一首")
            return

        item = self.selected
        folder = Path(self.cfg.get("charts_dir", ""))

        def work() -> None:
            try:
                target = fake.make_fake(item["path"], folder, mode)
                self._log(f"已生成假课题：{target.name}")
                self.after(0, self._load_charts)
            except Exception as exc:  # noqa: BLE001
                self._log(f"[错误] 生成失败：{exc}")

        threading.Thread(target=work, daemon=True).start()

    # -- 设置 ----------------------------------------------------------
    def _page_settings(self, parent: tk.Frame) -> tk.Frame:
        page = tk.Frame(parent, bg=ui.BG)
        wrap = tk.Frame(page, bg=ui.CARD)
        wrap.pack(fill="x")

        self.setting_vars = {
            "adb": tk.StringVar(value=self.cfg.get("adb", "")),
            "apk": tk.StringVar(value=self.cfg.get("apk", str(REPO / "com.PigeonGames.Phigros.apk"))),
            "charts_dir": tk.StringVar(value=self.cfg.get("charts_dir", "")),
            "countdown": tk.StringVar(value=str(self.cfg.get("countdown", 5))),
            "start_tap": tk.StringVar(value=self.cfg.get("start_tap", "")),
        }
        rows = [
            ("adb 路径", "adb", "留空自动查找（PATH 或仓库里的 scrcpy）"),
            ("谱面目录", "charts_dir", "放 *.json 谱面的文件夹"),
            ("倒计时（秒）", "countdown", "点开始前的准备时间"),
            ("开始按钮坐标", "start_tap", "留空按屏幕比例自动推算，例如 2050,950"),
            ("安装包路径", "apk", "提取谱面时从这个 apk 解包"),
        ]
        for index, (label, key, hint) in enumerate(rows):
            tk.Label(wrap, text=label, bg=ui.CARD, fg=ui.TEXT, font=ui.font(10, "bold")).grid(
                row=index, column=0, sticky="w", padx=16, pady=(12 if index == 0 else 6, 0)
            )
            tk.Entry(
                wrap,
                textvariable=self.setting_vars[key],
                bg=ui.CARD_HOVER,
                fg=ui.TEXT,
                insertbackground=ui.TEXT,
                relief="flat",
                font=ui.font(9),
                width=48,
            ).grid(row=index, column=1, sticky="w", padx=16, pady=(12 if index == 0 else 6, 0))
            tk.Label(wrap, text=hint, bg=ui.CARD, fg=ui.DIM, font=ui.font(8)).grid(
                row=index, column=2, sticky="w", padx=16, pady=(12 if index == 0 else 6, 0)
            )

        self.touch_debug_var = tk.BooleanVar(value=bool(self.cfg.get("show_touch_debug", True)))
        tk.Checkbutton(
            wrap,
            text="运行时显示触摸操作（调试用，system show_touches + pointer_location）",
            variable=self.touch_debug_var,
            bg=ui.CARD,
            fg=ui.TEXT,
            selectcolor=ui.CARD_HOVER,
            activebackground=ui.CARD,
            activeforeground=ui.TEXT,
            font=ui.font(9),
            command=self._on_touch_debug,
        ).grid(row=len(rows), column=0, columnspan=3, sticky="w", padx=16, pady=(14, 4))

        tk.Button(
            wrap,
            text="从 APK 提取谱面",
            command=self._extract_charts,
            bg=ui.CARD_HOVER,
            fg=ui.TEXT,
            relief="flat",
            bd=0,
            font=ui.font(10, "bold"),
            padx=16,
            pady=6,
            activebackground=ui.LINE,
            activeforeground=ui.TEXT,
        ).grid(row=len(rows), column=0, sticky="w", padx=16, pady=16)

        tk.Button(
            wrap,
            text="保存设置",
            command=self._save_settings,
            bg=ui.ACCENT,
            fg="white",
            relief="flat",
            bd=0,
            font=ui.font(10, "bold"),
            padx=16,
            pady=6,
            activebackground=ui.ACCENT_DARK,
            activeforeground="white",
        ).grid(row=len(rows), column=1, sticky="w", padx=16, pady=16)
        return page

    # -- 主题 ----------------------------------------------------------
    def _page_theme(self, parent: tk.Frame) -> tk.Frame:
        page = tk.Frame(parent, bg=ui.BG)
        tk.Label(page, text="界面主题", bg=ui.BG, fg=ui.TEXT, font=ui.font(12, "bold")).pack(
            anchor="w"
        )
        self.theme_note = tk.Label(
            page,
            text="切换后立即生效（当前版本只做了配色，重启后保持默认深色）",
            bg=ui.BG,
            fg=ui.DIM,
            font=ui.font(9),
        )
        self.theme_note.pack(anchor="w", pady=(4, 12))
        for name in ui.THEMES:
            tk.Button(
                page,
                text=name,
                command=lambda n=name: self._apply_theme(n),
                bg=ui.CARD,
                fg=ui.TEXT,
                relief="flat",
                bd=0,
                font=ui.font(10),
                padx=20,
                pady=8,
                activebackground=ui.CARD_HOVER,
                activeforeground=ui.TEXT,
            ).pack(anchor="w", pady=4)
        return page

    # -- 关于 ----------------------------------------------------------
    def _page_about(self, parent: tk.Frame) -> tk.Frame:
        page = tk.Frame(parent, bg=ui.BG)
        text = (
            "Phigros 自动打歌 · 桌面控制台 v1.0\n\n"
            "通过 adb + scrcpy 通道向手机注入触控，不需要在手机上装任何东西。\n"
            "谱面来自你自己的游戏安装包，本工具不附带任何游戏资源。\n\n"
            "第二版规划：\n"
            "  · 假课题 / 存档 / 主题 等页签\n"
            "  · 安卓端游戏内悬浮窗\n"
        )
        tk.Label(page, text=text, bg=ui.BG, fg=ui.DIM, font=ui.font(10), justify="left").pack(anchor="w")
        return page

    # ------------------------------------------------------------------
    # 数据
    # ------------------------------------------------------------------
    def _load_charts(self) -> None:
        folder = Path(self.cfg.get("charts_dir", ""))
        self.charts = []
        if folder.is_dir():
            for path in sorted(folder.glob("*.json")):
                if path.name == "manifest.json":
                    continue
                parts = path.stem.rsplit(".", 2)
                if len(parts) != 3 or parts[2] not in DIFFS:
                    continue
                self.charts.append(
                    {"song": parts[0], "artist": parts[1], "diff": parts[2], "path": path}
                )
        self._log(f"谱面目录：{folder}（{len(self.charts)} 份）")
        self._refresh_library()

    def _refresh_library(self) -> None:
        if not hasattr(self, "listbox"):
            return
        keyword = self.search_var.get().strip().lower()
        diff = self.diff_var.get()
        self.filtered = [
            item
            for item in self.charts
            if (diff == "全部" or item["diff"] == diff)
            and (not keyword or keyword in item["song"].lower() or keyword in item["artist"].lower())
        ]
        self.listbox.delete(0, "end")
        for item in self.filtered:
            self.listbox.insert("end", f"  {item['song']}    [{item['diff']}]   {item['artist']}")
        self.library_count.configure(text=f"共 {len(self.filtered)} 份（总 {len(self.charts)} 份）")

    def _on_select(self, _event=None) -> None:
        selection = self.listbox.curselection()
        if not selection:
            return
        item = self.filtered[selection[0]]
        self.selected = item
        self.song_label.configure(
            text=f"{item['song']}\n{item['artist']}  ·  {item['diff']}\n{item['path'].name}"
        )
        if hasattr(self, "fake_hint"):
            self.fake_hint.configure(text=f"当前：{item['song']} [{item['diff']}]")

        # 选歌即同步到手机（防抖，避免快速翻列表时连续推送）
        if getattr(self, "device", None) is not None:
            self._sync_seq = getattr(self, "_sync_seq", 0) + 1
            seq = self._sync_seq
            self.after(300, lambda: self._auto_sync_plan(seq))

    # ------------------------------------------------------------------
    # 交互
    # ------------------------------------------------------------------
    def _log(self, text: str) -> None:
        self.log_queue.put(text)

    def _pump_log(self) -> None:
        try:
            while True:
                line = self.log_queue.get_nowait()
                self.log_text.configure(state="normal")
                self.log_text.insert("end", line + "\n")
                lines = int(self.log_text.index("end-1c").split(".")[0])
                if lines > MAX_LOG_LINES:
                    self.log_text.delete("1.0", f"{lines - MAX_LOG_LINES}.0")
                self.log_text.see("end")
                self.log_text.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(120, self._pump_log)

    def _set_status(self, text: str) -> None:
        self.status_label.configure(text=text)

    def _connect(self) -> None:
        def work() -> None:
            try:
                adb = find_adb(self.cfg.get("adb", ""))
                self._log(f"adb: {adb}")
                devices = Device.list_devices(adb)
                if not devices:
                    self._log("没检测到设备，检查 USB 调试授权")
                    self._set_status("未连接")
                    return
                device = Device(adb, devices[0])
                rooted = device.is_root() or device.try_root()
                screen = device.screen()
                self.device = device
                self.screen = screen
                per_device = self.cfg.get("delay_by_device", {}).get(device.serial or "default")
                if per_device is not None:
                    self.delay_scale.set(int(per_device))
                    self._on_delay(int(per_device))
                self._log(f"已连接 {devices[0]}  屏幕 {screen[0]}x{screen[1]}  root={rooted}")
                self.device_text.configure(
                    text=f"{devices[0]}\n{screen[0]}×{screen[1]}  {'root ✓' if rooted else 'root ✗'}"
                )
                self._set_status("已连接")
            except Exception as exc:  # noqa: BLE001
                self._log(f"[错误] {exc}")
                self._set_status("连接失败")

        threading.Thread(target=work, daemon=True).start()

    def _auto_sync_plan(self, seq: int) -> None:
        """把当前选中曲目的计划推到手机，让悬浮面板和电脑端保持一致。"""
        if seq != getattr(self, "_sync_seq", 0):
            return  # 期间又切歌了，丢弃这次
        if self.device is None or not hasattr(self, "selected"):
            return

        item = self.selected
        screen = self.screen

        def work() -> None:
            try:
                import json
                import subprocess

                from export_plan import build_events

                data = build_events(item["path"], screen[0], screen[1])
                local = APP_DIR / "out" / f"{item['song']}.{item['diff']}.plan.json"
                local.parent.mkdir(parents=True, exist_ok=True)
                local.write_text(
                    json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
                )
                target_dir = "/sdcard/Android/data/io.phigros.auto/files/plans"
                target = f"{target_dir}/{item['song']}.{item['diff']}.json"
                base = [self.device.adb] + (["-s", self.device.serial] if self.device.serial else [])
                subprocess.run(
                    base + ["shell", f"mkdir -p {target_dir}"],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    check=False,
                )
                result = subprocess.run(
                    base + ["push", str(local), target],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                )
                if result.returncode == 0:
                    self._log(f"已同步到手机：{item['song']} [{item['diff']}]（{len(data['events'])} 事件）")
                    self._log("手机面板按「开始」会自动用这份计划")
                else:
                    self._log(f"[错误] 同步到手机失败：{(result.stderr or '').strip()[:120]}")
            except Exception as exc:  # noqa: BLE001
                self._log(f"[错误] 同步到手机失败：{exc}")

        threading.Thread(target=work, daemon=True).start()

    def _export_selected(self) -> None:
        if not hasattr(self, "selected"):
            self._log("先在「谱面库」里选一首")
            return
        if self.device is None:
            self._log("先点「连接设备」")
            return

        item = self.selected
        screen = self.screen
        target_dir = "/sdcard/Android/data/io.phigros.auto/files/plans"
        target = f"{target_dir}/{item['song']}.{item['diff']}.json"
        local = APP_DIR / "out" / f"{item['song']}.{item['diff']}.plan.json"

        def work() -> None:
            try:
                self._log(f"编译计划：{item['song']} [{item['diff']}] {screen[0]}x{screen[1]}")
                data = build_events(item["path"], screen[0], screen[1])
                local.parent.mkdir(parents=True, exist_ok=True)
                import json

                local.write_text(
                    json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
                )
                self._log(f"本地计划：{local.name}（{len(data['events'])} 事件）")

                adb = self.device.adb
                serial = self.device.serial
                base = [adb] + (["-s", serial] if serial else [])
                import subprocess

                subprocess.run(
                    base + ["shell", f"mkdir -p {target_dir}"],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    check=False,
                )
                result = subprocess.run(
                    base + ["push", str(local), target],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode == 0:
                    self._log(f"已推送到手机：{target}")
                else:
                    self._log(f"[错误] 推送失败：{result.stderr.strip()}")
            except Exception as exc:  # noqa: BLE001
                self._log(f"[错误] 导出失败：{exc}")

        threading.Thread(target=work, daemon=True).start()

    def _on_master(self, value: bool) -> None:
        if not value and self.session and self.session.running:
            self.play_toggle.set(False)
        self._log("总开关：" + ("开" if value else "关"))

    def _one_click(self) -> None:
        if self.device is None:
            self._log("先点「连接设备」")
            return
        if not hasattr(self, "selected"):
            self._log("先在「谱面库」里选一首歌")
            return

        item = self.selected
        screen = self.screen

        def work() -> None:
            try:
                try:
                    from one_click import run as run_one_click
                except ImportError as exc:
                    self._log(f"[错误] 缺少依赖：{exc}")
                    self._log("一键打歌需要 Pillow。请用「启动.bat」启动（会自动挑带 Pillow 的解释器），")
                    self._log("或在命令行执行：py -3 -m pip install pillow")
                    return

                self._log("=== 一键自动打歌 ===")
                ok = run_one_click(
                    self.device,
                    item,
                    screen,
                    self._log,
                    self.delay_var.get(),
                    show_touch=bool(self.cfg.get("show_touch_debug", True)),
                )
                self._log("已开始打歌" if ok else "没能开始，检查手机是否停在选歌界面")
            except Exception as exc:  # noqa: BLE001
                self._log(f"[错误] 一键自动打歌失败：{exc}")

        threading.Thread(target=work, daemon=True).start()

    def _phone_autoplay(self) -> None:
        if self.device is None:
            self._log("先点「连接设备」")
            return

        def work() -> None:
            try:
                try:
                    from phone_autoplay import PhoneAutoplay
                except ImportError as exc:
                    self._log(f"[错误] 缺少依赖：{exc}")
                    self._log("请用「启动.bat」启动，或执行：py -3 -m pip install pillow")
                    return

                self._log("手机自动同步：找悬浮面板…")
                helper = PhoneAutoplay(self.device.adb, self.device.serial, self._log)
                if helper.run():
                    self._log("手机自动同步：已开始打歌")
                else:
                    self._log("手机自动同步：失败，可手动点手机上的「同步」")
            except Exception as exc:  # noqa: BLE001
                self._log(f"[错误] 手机自动同步失败：{exc}")

        threading.Thread(target=work, daemon=True).start()

    def _on_channel(self, value: bool) -> None:
        if value:
            self._start_channel()
        else:
            self._stop_channel()

    def _start_channel(self) -> None:
        if self.device is None:
            self._log("先点「连接设备」")
            self.after(0, lambda: self.channel_toggle.set(False))
            return

        def work() -> None:
            try:
                import subprocess

                adb = self.device.adb
                serial = self.device.serial
                base = [adb] + (["-s", serial] if serial else [])
                jar = APP_DIR / "scrcpy-server-v5.0.1"
                remote = "/data/local/tmp/phigros_scrcpy.jar"
                pushed = subprocess.run(
                    base + ["push", str(jar), remote],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if pushed.returncode != 0:
                    raise RuntimeError(pushed.stderr.strip())

                command = (
                    f"CLASSPATH={remote} app_process / com.genymobile.scrcpy.Server 5.0.1 "
                    "log_level=info video=false audio=false control=true tunnel_forward=true "
                    "cleanup=false send_device_meta=false send_dummy_byte=true power_on=false"
                )
                self.server_proc = subprocess.Popen(
                    base + ["shell", command],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self._log("注入服务端已启动（手机端 App 现在可以直接按「开始」）")
                self.channel_card.set_subtitle("已启动：手机 App 可直接使用")
            except Exception as exc:  # noqa: BLE001
                self._log(f"[错误] 启动注入服务端失败：{exc}")
                self.after(0, lambda: self.channel_toggle.set(False))

        threading.Thread(target=work, daemon=True).start()

    def _stop_channel(self) -> None:
        proc = self.server_proc
        self.server_proc = None
        if proc is not None:
            try:
                proc.terminate()
            except Exception:  # noqa: BLE001
                pass
        try:
            import subprocess

            adb = self.device.adb if self.device else find_adb(self.cfg.get("adb", ""))
            subprocess.run(
                [adb, "shell", "pkill -f com.genymobile.scrcpy.Server"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=False,
            )
        except Exception:  # noqa: BLE001
            pass
        self.channel_card.set_subtitle("已关闭")
        self._log("注入服务端已关闭")

    def _on_play(self, value: bool) -> None:
        if value:
            self._start_session()
        else:
            self._stop_session()

    def _on_delay(self, value) -> None:
        ms = int(float(value))
        self.delay_var.set(ms)
        self.delay_value.configure(text=f"{ms:+d} ms")
        self.cfg["delay_ms"] = ms
        if getattr(self, "device", None) is not None:
            key = self.device.serial or "default"
            self.cfg.setdefault("delay_by_device", {})[key] = ms
        save_config(self.cfg)

    def _on_touch_debug(self) -> None:
        value = bool(self.touch_debug_var.get())
        self.cfg["show_touch_debug"] = value
        save_config(self.cfg)
        self._log("运行时显示触摸操作：" + ("开" if value else "关"))

    def _save_settings(self) -> None:
        self.cfg["show_touch_debug"] = bool(getattr(self, "touch_debug_var", None) and self.touch_debug_var.get())
        self.cfg["adb"] = self.setting_vars["adb"].get().strip()
        self.cfg["charts_dir"] = self.setting_vars["charts_dir"].get().strip()
        try:
            self.cfg["countdown"] = int(self.setting_vars["countdown"].get())
        except ValueError:
            self.cfg["countdown"] = 5
        self.cfg["start_tap"] = self.setting_vars["start_tap"].get().strip()
        self.cfg["apk"] = self.setting_vars["apk"].get().strip()
        save_config(self.cfg)
        self._log("设置已保存")
        self._load_charts()

    def _extract_charts(self) -> None:
        apk = Path(self.setting_vars["apk"].get().strip())
        folder = Path(self.cfg.get("charts_dir", ""))
        if not apk.is_file():
            self._log(f"[错误] 找不到安装包：{apk}")
            return
        self._log(f"开始提取：{apk.name} -> {folder}")

        def work() -> None:
            try:
                extract.run_extract(apk, folder, REPO / "out" / "apk_assets", log=self._log)
                self._log("提取完成，刷新谱面库")
                self.after(0, self._load_charts)
            except Exception as exc:  # noqa: BLE001
                self._log(f"[错误] 提取失败：{exc}")

        threading.Thread(target=work, daemon=True).start()

    def _apply_theme(self, name: str) -> None:
        self.theme_note.configure(text=f"已选择「{name}」—— 深色主题已锁定（浅色适配中）")

    # -- 打歌 ----------------------------------------------------------
    def _start_session(self) -> None:
        if not self.master_toggle.value:
            self._log("请先打开「总开关」")
            self.play_toggle.set(False)
            return
        if not hasattr(self, "selected"):
            self._log("请先在「谱面库」里选一首")
            self.play_toggle.set(False)
            return
        if self.device is None:
            self._log("还没有连接设备")
            self.play_toggle.set(False)
            return

        tap = self.device.start_tap(self.cfg.get("start_tap", ""))
        self.play_card.set_subtitle("正在打歌…（再点一次开关可停止）")
        self.session = Session(
            chart_path=self.selected["path"],
            adb=self.device.adb,
            serial=self.device.serial,
            screen=self.screen,
            start_tap=tap,
            countdown=int(self.cfg.get("countdown", 5)),
            delay_ms=int(self.cfg.get("delay_ms", 0)),
            jar=APP_DIR / "scrcpy-server-v5.0.1",
            on_log=self._log,
            on_status=self._set_status,
            on_done=self._session_done,
        )
        self.session.start()

    def _stop_session(self) -> None:
        if self.session and self.session.running:
            self.session.stop()
            self._log("已请求停止")
        self.play_card.set_subtitle("已关闭：点右侧开关开始")

    def _session_done(self) -> None:
        self.after(0, lambda: self.play_toggle.set(False))
        self.after(0, lambda: self.play_card.set_subtitle("已关闭：点右侧开关开始"))


if __name__ == "__main__":
    App().mainloop()
