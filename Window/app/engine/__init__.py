"""phigros_auto —— 面向 Phigros 的自动打歌核心。

已实现的是「大脑」部分：谱面解析、判定线数学、触控时间轴、音频对齐、
离线预览。设备端的触控注入见 README 的落地路线。
"""

from .chart import Chart, ChartError, JudgeLine, Note
from .schedule import Calibration, Plan, Point, ScreenMapper, Touch, build_plan

__version__ = "0.1.0"

__all__ = [
    "Chart",
    "ChartError",
    "JudgeLine",
    "Note",
    "Calibration",
    "Plan",
    "Point",
    "ScreenMapper",
    "Touch",
    "build_plan",
    "__version__",
]
