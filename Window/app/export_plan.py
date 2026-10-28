"""把谱面编译成手机端可回放的触控计划（plan.json）。

事件格式：[时间, 动作, 指针, x, y]，动作 0=按下 1=抬起 2=移动。
坐标按目标屏幕（横屏 宽x高）算好，手机端只负责按时间回放。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from engine.chart import Chart
from engine.schedule import Calibration, build_plan

ACTION_DOWN, ACTION_UP, ACTION_MOVE = 0, 1, 2


def build_events(chart_path: str | Path, width: int, height: int) -> dict:
    chart = Chart.load(chart_path)
    plan = build_plan(chart, Calibration(width=width, height=height))
    if not plan.touches:
        raise RuntimeError("这份谱面没有触控")

    events: list[list] = []
    for touch in plan.touches:
        events.append([round(touch.down_t, 4), ACTION_DOWN, touch.pointer, touch.down.x, touch.down.y])
        last = touch.down
        for when, point in touch.moves:
            events.append([round(when, 4), ACTION_MOVE, touch.pointer, point.x, point.y])
            last = point
        events.append([round(touch.up_t, 4), ACTION_UP, touch.pointer, last.x, last.y])
    # 同一时刻的顺序：先移动、再抬起、最后按下（避免同一指针"没松手又按下"）
    order = {ACTION_MOVE: 0, ACTION_UP: 1, ACTION_DOWN: 2}
    events.sort(key=lambda item: (item[0], order[item[1]]))

    stem = Path(chart_path).stem
    parts = stem.rsplit(".", 2)
    song = parts[0] if parts else stem
    artist = parts[1] if len(parts) > 1 else ""
    difficulty = parts[2] if len(parts) > 2 else ""

    return {
        "song": song,
        "artist": artist,
        "difficulty": difficulty,
        "duration": round(plan.duration, 3),
        "first": round(plan.touches[0].down_t, 4),
        "width": width,
        "height": height,
        "events": events,
    }


def export(chart_path: str | Path, out_path: str | Path, width: int = 1920, height: int = 1080) -> Path:
    data = build_events(chart_path, width, height)
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return target


if __name__ == "__main__":
    chart = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else "plan.json"
    w = int(sys.argv[3]) if len(sys.argv) > 3 else 1920
    h = int(sys.argv[4]) if len(sys.argv) > 4 else 1080
    path = export(chart, out, w, h)
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    print(f"计划已生成：{path}")
    print(f"  曲目 {data['song']} [{data['difficulty']}]  事件 {len(data['events'])}  时长 {data['duration']}s")
