"""毫秒级同步：通过 App 的本地控制端口驱动开始/同步，绕开 adb tap 的延迟抖动。"""

from __future__ import annotations

import io
import socket
import subprocess
import time
from pathlib import Path

from PIL import Image

PORT = 27184
LEAD = 0.03          # 控制端口只有几毫秒延迟，稍微提前即可
NOMINAL_SPEED = 1150.0


class PhoneControl:
    def __init__(self, adb: str, serial: str, log, plan_events: list | None = None,
                 delay_ms: int = 0):
        self.adb = adb
        self.serial = serial
        self.log = log
        self.plan_events = plan_events or []
        self.delay_ms = delay_ms
        self._first = self.plan_events[0] if self.plan_events else None
        self._sock: socket.socket | None = None

    # ---------- 基础 ----------
    def _base(self) -> list[str]:
        return [self.adb] + (["-s", self.serial] if self.serial else [])

    def shell(self, *args: str) -> str:
        result = subprocess.run(
            self._base() + ["shell", *args], stdin=subprocess.DEVNULL, capture_output=True, check=False
        )
        return (result.stdout or b"").decode("utf-8", "replace")

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

    # ---------- 控制通道 ----------
    def connect(self) -> bool:
        subprocess.run(
            self._base() + ["forward", f"tcp:{PORT}", f"tcp:{PORT}"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
        )
        for _ in range(20):
            try:
                sock = socket.create_connection(("127.0.0.1", PORT), timeout=2.0)
                sock.settimeout(5.0)
                self._sock = sock
                self.log("控制端口已连接")
                return True
            except OSError:
                time.sleep(0.2)
        self.log("[错误] 连不上 App 控制端口（面板起了吗？）")
        return False

    def send(self, command: str) -> str:
        if self._sock is None:
            return ""
        try:
            self._sock.sendall((command + "\n").encode("utf-8"))
            return self._sock.recv(64).decode("utf-8", "replace").strip()
        except OSError as exc:
            self.log(f"[错误] 控制命令失败：{exc}")
            return ""

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        subprocess.run(
            self._base() + ["forward", "--remove", f"tcp:{PORT}"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
        )

    # ---------- 识别 ----------
    @staticmethod
    def playing(image: Image.Image) -> bool:
        gray = image.convert("L")
        width, height = image.size
        box = gray.crop((int(width * 0.016), int(height * 0.028),
                         int(width * 0.052), int(height * 0.093)))
        pixels = list(box.getdata())
        bright = sum(1 for v in pixels if v > 190)
        return bright > len(pixels) * 0.08

    @staticmethod
    def bars(image: Image.Image) -> list[tuple[int, int, int]]:
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

    def _match_note(self, x: int, estimate: float) -> tuple[float, int] | None:
        """按 x 与估计时间找最可能的计划音符，返回 (谱面时间, 该音符的判定线 y)。"""
        if not self.plan_events:
            return None
        downs = [(float(e[0]), int(e[3]), int(e[4])) for e in self.plan_events if int(e[1]) == 0]
        if not downs:
            return None
        near = [c for c in downs if abs(c[1] - x) <= 30] or downs
        best = min(near, key=lambda c: abs(c[0] - estimate))
        return best[0], best[2]

    # ---------- 主流程 ----------
    def run(self, timeout: float = 45.0, chart_delay: float = 1.2) -> bool:
        if not self.connect():
            return False
        # 总是显式下发（包括 0），否则会沿用手机里上一次存的旧值
        self.send(f"delay {int(self.delay_ms)}")
        self.log(f"判定延迟已设为 {int(self.delay_ms)}ms")

        image, _ = self.shot()
        width, height = image.size
        line_y = int(height * 0.8)

        field_t = None
        for attempt in range(3):
            self.send("start")
            self.log(f"已发送 start（第 {attempt + 1} 次），等进歌…")

            deadline = time.perf_counter() + 12.0
            while time.perf_counter() < deadline:
                image, moment = self.shot()
                if self.playing(image):
                    field_t = moment
                    break
            if field_t is not None:
                break

            # 不在选歌界面（多半停在标题页/警告页）：点一下屏幕中央再试
            self.log("  没进歌，点一下屏幕中央（跳过标题页）后重试")
            self.shell("input", "tap", str(width // 2), str(height // 2))
            time.sleep(2.0)

        if field_t is None:
            self.log("[错误] 没等到进歌画面（请把游戏停在选歌界面）")
            self.close()
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

            # 进歌初期优先跟首音符那条
            first_x = int(self._first[3]) if self._first is not None else None
            if first_x is not None and moment - field_t < 5.0:
                preferred = [c for c in found if abs(c[1] - first_x) <= 100]
                if preferred:
                    near = preferred
                    if locked != first_x:
                        locked = first_x
                        samples = []

            y, x, _w = max(near, key=lambda c: c[0])
            if locked is None:
                locked = x
                self.log(f"  锁定 x={x} y={y}（进歌 {moment - field_t:+.2f}s）")

            samples.append((moment, y))
            if len(samples) > 4:
                samples.pop(0)

            crossing = None
            speed = None
            target_y = line_y
            ref = None
            estimate = (moment - field_t) - chart_delay
            matched = self._match_note(x, estimate)
            if matched is not None and abs(matched[0] - estimate) <= 2.0:
                ref, target_y = matched
            elif self._first is not None:
                ref, target_y = float(self._first[0]), int(self._first[4])

            if len(samples) >= 2:
                (t0, y0), (t1, y1) = samples[0], samples[-1]
                if t1 > t0 and y1 - y0 > 25:
                    speed = (y1 - y0) / (t1 - t0)
                    crossing = t1 + (target_y - y1) / max(speed, 150.0)
            elif abs(target_y - y) < 350:
                speed = NOMINAL_SPEED
                crossing = moment + (target_y - y) / NOMINAL_SPEED

            if crossing is None:
                continue
            wait = crossing - LEAD - time.perf_counter()
            if wait < -0.2:
                samples = []
                continue
            if wait > 0:
                time.sleep(wait)
            if ref is None and self._first is not None:
                ref = float(self._first[0])
            answer = self.send(f"sync {ref:.4f}" if ref is not None else "sync")
            self.log(
                f"已发 sync（参考 {ref if ref is not None else 'plan.first'}，"
                f"目标线 y={target_y}，回包 {answer}）"
            )
            self.close()
            return answer == "ok"
        self.log("[错误] 没追上音符")
        self.close()
        return False
