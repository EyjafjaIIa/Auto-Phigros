"""量一下 Player 的调度抖动（相对抖动，绝对偏移由校准处理）。"""

from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from phigros_auto.chart import Chart
from phigros_auto.play import Backend, Player, Point
from phigros_auto.schedule import Calibration, build_plan


class StampBackend(Backend):
    def __init__(self) -> None:
        self.by_pointer: dict[int, list[float]] = {}
        self.count = 0

    def press(self, pointer: int, point: Point) -> None:
        self.by_pointer.setdefault(pointer, []).append(time.perf_counter())
        self.count += 1

    def drag(self, pointer: int, point: Point) -> None:
        pass

    def release(self, pointer: int) -> None:
        pass


def main(limit: float = 4.0) -> None:
    chart = Chart.load(ROOT / "assets" / "demo_chart.json")
    plan = build_plan(chart, Calibration(width=1080, height=2400))
    plan.touches = [t for t in plan.touches if t.down_t < limit]

    backend = StampBackend()
    started = time.perf_counter()
    Player(plan, backend, realtime=True).run()
    elapsed = time.perf_counter() - started

    deviations: list[float] = []
    grouped: dict[int, list] = {}
    for touch in plan.touches:
        grouped.setdefault(touch.pointer, []).append(touch)
    for pointer, touches in grouped.items():
        stamps = backend.by_pointer.get(pointer, [])
        if len(stamps) < 2:
            continue
        for i in range(1, len(stamps)):
            planned = touches[i].down_t - touches[0].down_t
            actual = stamps[i] - stamps[0]
            deviations.append(actual - planned)

    if not deviations:
        print("样本不足，没法评估抖动")
        return
    deviations_ms = sorted(d * 1000.0 for d in deviations)
    p95 = deviations_ms[int(len(deviations_ms) * 0.95) - 1]
    print(f"触控数：{backend.count}，采样：{len(deviations_ms)}，耗时 {elapsed:.2f}s")
    print(
        f"相对抖动：均值 {statistics.mean(deviations_ms):+.2f} ms，"
        f"中位数 {statistics.median(deviations_ms):+.2f} ms，"
        f"P95 {p95:+.2f} ms，最大 {deviations_ms[-1]:+.2f} ms"
    )


if __name__ == "__main__":
    main()
