"""由电脑驱动手机悬浮面板完成一次带视觉同步的自动打歌。

逻辑与已验证拿到 All Perfect 的脚本一致：
找面板按钮 -> 点「开始」-> 等进歌（左上暂停键）-> 全宽找音符
-> 两帧测速预测过线 / 中央音符单帧估算 -> 提前 280ms 点「同步」。
"""

from __future__ import annotations

import io
import subprocess
import time
from pathlib import Path

from PIL import Image

LEAD = 0.28            # adb tap 延迟补偿
NOMINAL_SPEED = 1150.0  # 音符下落速度（1080p 基准）


class PhoneAutoplay:
    def __init__(self, adb: str, serial: str, log, first_x: int | None = None,
                 plan_events: list | None = None, chart_delay: float = 1.2,
                 delay_ms: int = 0):
        self.adb = adb
        self.serial = serial
        self.log = log
        self.first_x = first_x
        self.plan_events = plan_events or []
        self.chart_delay = chart_delay
        self.delay_ms = delay_ms
        # adb tap 约 250ms 落地；判定延迟会把事件整体提前，两者要抵消
        self.lead = max(0.05, 0.25 - delay_ms / 1000.0)

    # ---------- 基础 ----------
    def _base(self) -> list[str]:
        return [self.adb] + (["-s", self.serial] if self.serial else [])

    def shell(self, *args: str) -> str:
        result = subprocess.run(
            self._base() + ["shell", *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
        )
        return (result.stdout or b"").decode("utf-8", "replace")

    def tap(self, x: int, y: int) -> None:
        self.shell("input", "tap", str(x), str(y))

    def shot(self) -> tuple[Image.Image, float]:
        began = time.perf_counter()
        data = subprocess.run(
            self._base() + ["exec-out", "screencap", "-p"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
        ).stdout
        finished = time.perf_counter()
        return Image.open(io.BytesIO(data)).convert("RGB"), (began + finished) / 2

    # ---------- 识别 ----------
    @staticmethod
    def find_buttons(image: Image.Image) -> list[tuple[int, int]]:
        pixels = image.load()
        width, height = image.size
        hits = []
        for y in range(100, height - 100, 2):
            for x in range(20, 800, 2):
                r, g, b = pixels[x, y]
                if 60 <= r <= 100 and 110 <= g <= 145 and 235 <= b <= 255:
                    hits.append((x, y))
        if not hits:
            return []
        row_y = sorted(y for _, y in hits)[len(hits) // 2]
        xs = sorted(set(x for x, _ in hits))
        groups = [[xs[0]]]
        for value in xs[1:]:
            if value - groups[-1][-1] <= 6:
                groups[-1].append(value)
            else:
                groups.append([value])
        return [(sum(g) // len(g), row_y) for g in groups if len(g) > 8]

    @staticmethod
    def playing(image: Image.Image) -> bool:
        """左上角暂停键亮起即认为是游戏画面（与已验证脚本一致）。"""
        gray = image.convert("L")
        width, height = image.size
        box = gray.crop((int(width * 0.016), int(height * 0.028),
                         int(width * 0.052), int(height * 0.093)))
        pixels = list(box.getdata())
        bright = sum(1 for v in pixels if v > 190)
        return bright > len(pixels) * 0.08

    @staticmethod
    def bars(image: Image.Image) -> list[tuple[int, int, int]]:
        """全宽扫描亮色横条，排除面板区域。返回 [(y, x_center, width)]。"""
        pixels = image.load()
        width, height = image.size
        line_y = int(height * 0.80)
        out: list[tuple[int, int, int]] = []
        for y in range(30, line_y - 4, 2):
            run = 0
            best_run = 0
            run_start = 0
            best_center = 0
            for x in range(0, width, 2):
                r, g, b = pixels[x, y]
                bright = max(r, g, b) > 160 and (max(r, g, b) - min(r, g, b) > 20 or max(r, g, b) > 210)
                if bright:
                    if run == 0:
                        run_start = x
                    run += 2
                    if run > best_run:
                        best_run = run
                        best_center = run_start + run // 2
                else:
                    run = 0
            if 40 <= best_run <= 700 and not (best_center < 560 and y < 620):
                out.append((y, best_center, best_run))
        return out

    @staticmethod
    def line_y(image: Image.Image) -> int:
        return int(image.size[1] * 0.8)

    def _write_ref(self, value: float) -> None:
        import tempfile

        tmp = Path(tempfile.gettempdir()) / "phigros_sync_ref.txt"
        tmp.write_text(f"{value:.4f}", encoding="utf-8")
        subprocess.run(
            self._base() + ["push", str(tmp), "/sdcard/Android/data/io.phigros.auto/files/sync_ref.txt"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
        )
        self.log(f"  同步参考写入 {value:.3f}s")

    def _match_ref(self, x: int, estimate: float) -> float | None:
        if not self.plan_events:
            return None
        downs = [(float(e[0]), int(e[3])) for e in self.plan_events if int(e[1]) == 0]
        if not downs:
            return None
        near = [c for c in downs if abs(c[1] - x) <= 30] or downs
        best = min(near, key=lambda c: abs(c[0] - estimate))
        return best[0]

    def _sync_at(self, sync_btn: tuple[int, int], x: int, estimate: float) -> None:
        ref = self._match_ref(x, estimate)
        if ref is not None:
            self._write_ref(ref)
        self.tap(*sync_btn)
        self.log(f"已同步（参考 {ref if ref is not None else 'plan.first'}）")

    # ---------- 主流程 ----------
    def run(self, timeout: float = 45.0) -> bool:
        image, _ = self.shot()
        buttons = self.find_buttons(image)
        if len(buttons) < 2:
            self.log("[错误] 没找到手机悬浮面板，先打开 App 的面板")
            return False
        start_btn, sync_btn = buttons[0], buttons[1]
        self.log(f"面板按钮：开始={start_btn} 同步={sync_btn}")

        self.tap(*start_btn)
        self.log("已点「开始」，等进歌…")

        width, height = image.size
        line_y = self.line_y(image)

        deadline = time.perf_counter() + timeout
        field_t = None
        while time.perf_counter() < deadline:
            image, moment = self.shot()
            if self.playing(image):
                field_t = moment
                break
        if field_t is None:
            self.log("[错误] 没等到进歌画面")
            return False
        self.log("进歌了，追首音符…")

        deadline = time.perf_counter() + 30.0
        locked = None
        samples: list[tuple[float, int]] = []
        last_seen = time.perf_counter()
        while time.perf_counter() < deadline:
            image, moment = self.shot()
            found = self.bars(image)
            if not found:
                continue
            near = [c for c in found if locked is None or abs(c[1] - locked) <= 80]
            if not near:
                if locked is not None and time.perf_counter() - last_seen > 0.5:
                    locked = None
                    samples = []
                continue
            last_seen = time.perf_counter()
            y, x, _w = max(near, key=lambda c: c[0])
            if locked is None:
                locked = x
                self.log(f"  锁定 x={x} y={y}（进歌 {moment - field_t:+.2f}s）")

            # 中央音符且已经靠近判定线（来不及等第二帧）：用标称速度估算
            near_line = line_y - y < 350
            if not samples and near_line and abs(x - (self.first_x or width // 2)) <= 70:
                crossing = moment + (line_y - y) / NOMINAL_SPEED
                wait = crossing - LEAD - time.perf_counter()
                wait = crossing - self.lead - time.perf_counter()
                if -0.25 < wait < 1.2:
                    if wait > 0:
                        time.sleep(wait)
                    self._sync_at(sync_btn, x, (moment - field_t) - self.chart_delay)
                    self.log(f"  单帧估算 y={y}")
                    return True

            samples.append((moment, y))
            if len(samples) > 4:
                samples.pop(0)
            if len(samples) >= 2:
                (t0, y0), (t1, y1) = samples[0], samples[-1]
                if t1 > t0 and y1 - y0 > 25:
                    speed = (y1 - y0) / (t1 - t0)
                    crossing = t1 + (line_y - y1) / max(speed, 150.0)
                    wait = crossing - self.lead - time.perf_counter()
                    if wait < -0.25:
                        samples = []
                        continue
                    if wait > 0:
                        time.sleep(wait)
                    self._sync_at(sync_btn, x, (moment - field_t) - self.chart_delay)
                    self.log(f"  两帧速度≈{speed:.0f}px/s y={y1}")
                    return True
        self.log("[错误] 没追上音符")
        return False
