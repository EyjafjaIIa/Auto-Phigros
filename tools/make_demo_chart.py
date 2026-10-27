"""生成一份演示谱面（RPE formatVersion 3）。

没有真实谱面时，用它验证「解析 -> 调度 -> 渲染」整条链路。
"""

from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path

BPM = 150.0
FLOOR_PER_SEC = 6.0


def _t(beat: float) -> list[int]:
    fr = Fraction(beat).limit_denominator(32)
    return [fr.numerator // fr.denominator, fr.numerator % fr.denominator, fr.denominator]


def _note(kind: int, beat: float, x: float, hold: float = 0.0) -> dict:
    seconds = beat * 60.0 / BPM
    return {
        "type": kind,
        "time": _t(beat),
        "positionX": round(x, 4),
        "holdTime": _t(hold),
        "speed": 1.0,
        "floorPosition": round(seconds * FLOOR_PER_SEC, 5),
    }


def build() -> dict:
    notes: list[dict] = []

    def add(kind: int, beat: float, x: float, hold: float = 0.0) -> None:
        notes.append(_note(kind, beat, x, hold))

    # 前奏：四分音符
    for beat, x in ((4, 0.2), (5, 0.35), (6, 0.65), (7, 0.8)):
        add(1, beat, x)
    add(1, 8, 0.35)
    add(1, 8, 0.65)

    # 八分交替
    for i in range(8):
        add(1, 10 + i * 0.5, 0.25 if i % 2 == 0 else 0.75)
    add(2, 14, 0.5)
    add(1, 15, 0.3)
    add(1, 15.5, 0.7)

    # Hold + 单点
    add(3, 16, 0.25, hold=2.0)
    add(1, 16.5, 0.75)
    add(1, 17, 0.75)
    add(1, 17.5, 0.75)
    add(4, 18, 0.8)
    add(1, 18.5, 0.2)

    # 跑动
    lanes = (0.2, 0.35, 0.5, 0.65, 0.8)
    for i in range(8):
        add(1, 20 + i * 0.5, lanes[i % len(lanes)])
    add(1, 24, 0.2)
    add(1, 24, 0.5)
    add(1, 24, 0.8)

    # 长 Hold + 两侧单点
    add(3, 26, 0.5, hold=2.0)
    for i in range(4):
        add(1, 26 + i, 0.15 if i % 2 == 0 else 0.85)
    add(4, 30, 0.4)
    add(4, 30.5, 0.6)
    add(1, 32, 0.2)
    add(1, 32, 0.5)
    add(1, 32, 0.8)

    # 收尾
    add(3, 34, 0.5, hold=4.0)
    add(1, 35, 0.2)
    add(1, 37, 0.8)

    notes.sort(key=lambda n: n["floorPosition"])

    move_events = [
        {"startTime": [0, 0, 1], "endTime": [8, 0, 1], "start": 0.5, "end": 0.5, "start2": 0.58, "end2": 0.58},
        {"startTime": [8, 0, 1], "endTime": [16, 0, 1], "start": 0.5, "end": 0.5, "start2": 0.58, "end2": 0.52, "easingType": 4},
        {"startTime": [16, 0, 1], "endTime": [24, 0, 1], "start": 0.5, "end": 0.5, "start2": 0.52, "end2": 0.58, "easingType": 4},
        {"startTime": [24, 0, 1], "endTime": [40, 0, 1], "start": 0.5, "end": 0.5, "start2": 0.58, "end2": 0.5, "easingType": 3},
    ]
    rotate_events = [
        {"startTime": [0, 0, 1], "endTime": [16, 0, 1], "start": 0, "end": 0},
        {"startTime": [16, 0, 1], "endTime": [24, 0, 1], "start": 0, "end": -9, "easingType": 4},
        {"startTime": [24, 0, 1], "endTime": [30, 0, 1], "start": -9, "end": 0, "easingType": 4},
        {"startTime": [30, 0, 1], "endTime": [40, 0, 1], "start": 0, "end": 6, "easingType": 4},
    ]

    return {
        "formatVersion": 3,
        "offset": 0.0,
        "judgeLineList": [
            {
                "bpm": BPM,
                "speedEvents": [
                    {"startTime": [0, 0, 1], "endTime": [0, 0, 1], "start": 1.0, "end": 1.0}
                ],
                "judgeLineMoveEvents": move_events,
                "judgeLineRotateEvents": rotate_events,
                "judgeLineDisappearEvents": [
                    {"startTime": [0, 0, 1], "endTime": [40, 0, 1], "start": 1.0, "end": 1.0}
                ],
                "notesAbove": notes,
                "notesBelow": [],
            }
        ],
    }


def main() -> None:
    target = Path(__file__).resolve().parents[1] / "assets" / "demo_chart.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(build(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已生成 {target}")


if __name__ == "__main__":
    main()
