"""测试桌面端的「提取谱面」链路（后台跑）。"""

import sys
import time
from pathlib import Path

APP = Path(__file__).resolve().parent
sys.path.insert(0, str(APP))

from extract import find_interpreter, run_extract  # noqa: E402

REPO = APP.parents[2]
started = time.time()
print("解释器:", find_interpreter())
run_extract(
    REPO / "com.PigeonGames.Phigros.apk",
    REPO / "charts",
    APP / "out" / "apk_assets",
    log=print,
)
print(f"总耗时 {time.time() - started:.0f}s")
