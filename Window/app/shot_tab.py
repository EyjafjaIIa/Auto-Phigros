"""打开桌面端指定页签并截图（用于验证界面）。"""

import sys
import time
from pathlib import Path

APP = Path(__file__).resolve().parent
sys.path.insert(0, str(APP))

from PIL import ImageGrab  # noqa: E402

from main import App  # noqa: E402

tab = sys.argv[1] if len(sys.argv) > 1 else "功能"
out = sys.argv[2] if len(sys.argv) > 2 else "shot.png"

app = App()
app.tabbar.select(tab)
for _ in range(40):
    app.update()
    time.sleep(0.08)
app.lift()
app.attributes("-topmost", True)
app.update()
time.sleep(1.0)

x, y = app.winfo_rootx(), app.winfo_rooty()
w, h = app.winfo_width(), app.winfo_height()
ImageGrab.grab(bbox=(x, y, x + w, y + h)).save(out)
print(f"{tab} -> {out} ({w}x{h})")
app.destroy()
