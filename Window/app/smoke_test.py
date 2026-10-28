"""不弹窗的冒烟测试：建界面、跑一轮事件循环、销毁。"""

import sys
import tkinter as tk
from pathlib import Path

APP = Path(__file__).resolve().parent
sys.path.insert(0, str(APP))

from main import App  # noqa: E402

app = App()
app.update_idletasks()
app.update()
print("窗口:", app.winfo_width(), "x", app.winfo_height())
print("页签:", list(app.pages))
print("谱面:", len(app.charts))
print("默认难度筛选:", app.diff_var.get())
app.destroy()
print("OK")
