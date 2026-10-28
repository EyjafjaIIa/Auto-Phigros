"""实时下发触控。

时间轴被摊平成事件流（down / move / up），由一个循环按绝对时间派发。
这样可以在播放过程中整体「微调」（phisap 的 delay 微调），
而不必每次都重跑一遍。

``shift > 0`` 表示整体延后，``shift < 0`` 表示提前；播放时按 ``+`` / ``-``。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .schedule import Plan, Point, Touch


class Backend:
    name = "base"

    def wait_ready(self) -> None:
        """等设备/连接就绪。"""

    def press(self, pointer: int, point: Point) -> None:
        raise NotImplementedError

    def drag(self, pointer: int, point: Point) -> None:
        raise NotImplementedError

    def release(self, pointer: int) -> None:
        raise NotImplementedError

    def close(self) -> None:
        return None


class DryRunBackend(Backend):
    """只记录，不下发。"""

    name = "dryrun"

    def __init__(self, log_path: str | Path | None = None):
        self.log_path = Path(log_path) if log_path else None
        self.events: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._t0 = time.perf_counter()

    def _record(self, action: str, pointer: int, point: Point | None = None) -> None:
        item: dict[str, Any] = {
            "t": round(time.perf_counter() - self._t0, 4),
            "action": action,
            "pointer": pointer,
        }
        if point is not None:
            item["x"] = point.x
            item["y"] = point.y
        with self._lock:
            self.events.append(item)

    def press(self, pointer: int, point: Point) -> None:
        self._record("down", pointer, point)

    def drag(self, pointer: int, point: Point) -> None:
        self._record("move", pointer, point)

    def release(self, pointer: int) -> None:
        self._record("up", pointer)

    def close(self) -> None:
        if not self.log_path:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text(
            json.dumps(self.events, ensure_ascii=False, indent=2), encoding="utf-8"
        )


class AdbInputBackend(Backend):
    """``adb shell input`` 版：单点、慢，只用于确认「点下去确实能打到音符」。"""

    name = "adb"

    def __init__(self, adb: str = "adb", serial: str | None = None):
        self.adb = adb
        self.serial = serial
        self._state: dict[int, dict[str, Point]] = {}
        self._lock = threading.Lock()

    def wait_ready(self) -> None:
        print(
            "[警告] adb 后端只能单点注入，每条命令几十毫秒，"
            "只适合慢速谱面/连通性测试；正式使用请接 root 或 scrcpy 通道。"
        )

    def _run(self, *args: str) -> None:
        cmd = [self.adb]
        if self.serial:
            cmd += ["-s", self.serial]
        cmd += ["shell", *args]
        try:
            subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, timeout=15, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"[adb] 执行失败：{exc}")

    def press(self, pointer: int, point: Point) -> None:
        with self._lock:
            self._state[pointer] = {"down": point, "last": point}

    def drag(self, pointer: int, point: Point) -> None:
        with self._lock:
            state = self._state.get(pointer)
            if state:
                state["last"] = point

    def release(self, pointer: int) -> None:
        with self._lock:
            state = self._state.pop(pointer, None)
        if not state:
            return
        down = state["down"]
        last = state["last"]
        if (down.x, down.y) == (last.x, last.y):
            self._run("tap", str(down.x), str(down.y))
        else:
            dist = abs(down.x - last.x) + abs(down.y - last.y)
            self._run(
                "swipe",
                str(down.x),
                str(down.y),
                str(last.x),
                str(last.y),
                str(max(30, dist // 2)),
            )


@dataclass
class _Event:
    t: float
    kind: str
    pointer: int
    point: Point | None = None


class Player:
    """按时间轴把 ``Plan`` 下发给 ``Backend``，支持运行中微调。"""

    def __init__(
        self,
        plan: Plan,
        backend: Backend,
        *,
        speed: float = 1.0,
        realtime: bool = True,
        lead: float = 0.0,
        start_at: float | None = None,
        shift: float = 0.0,
        nudge: bool = False,
        nudge_mode: str = "keys",
        logger=print,
    ):
        if speed <= 0:
            raise ValueError("speed 必须为正数")
        self.plan = plan
        self.backend = backend
        self.speed = speed
        self.realtime = realtime
        self.lead = lead
        self.start_at = start_at
        self.shift = shift
        self.nudge = nudge
        self.nudge_mode = nudge_mode
        self.logger = logger
        self.errors: list[str] = []
        self._stop = False
        self._fired = 0

    # -- 微调监听 -----------------------------------------------------
    def _apply_nudge(self, name: str) -> None:
        if name == "up":
            self.shift -= 0.02
            label = "提前 20ms"
        elif name == "down":
            self.shift += 0.02
            label = "延后 20ms"
        elif name == "pgup":
            self.shift -= 0.2
            label = "提前 200ms"
        elif name == "pgdn":
            self.shift += 0.2
            label = "延后 200ms"
        elif name == "q":
            self.logger("[微调] 停止")
            self._stop = True
            return
        else:
            return
        self.logger(f"[微调] {self.shift:+.2f}s（{label}）")

    def _start_nudge_listener(self) -> None:
        if not self.nudge:
            return
        if self.nudge_mode == "line" or os.name != "nt":
            self._start_line_listener()
            return

        def listen() -> None:
            try:
                import ctypes

                user32 = ctypes.windll.user32
            except Exception as exc:  # noqa: BLE001
                self.logger(f"[微调] 全局按键不可用（{exc}），改用输入模式")
                self._start_line_listener()
                return

            # 全局按键：终端不需要有焦点
            keys = {"up": 0x26, "down": 0x28, "pgup": 0x21, "pgdn": 0x22, "q": 0x51}
            pressed = {name: False for name in keys}
            since = {name: 0.0 for name in keys}
            repeated = {name: 0.0 for name in keys}

            while not self._stop:
                now = time.perf_counter()
                for name, vk in keys.items():
                    down = bool(user32.GetAsyncKeyState(vk) & 0x8000)
                    if down and not pressed[name]:
                        since[name] = now
                        repeated[name] = now
                        self._apply_nudge(name)
                    elif down and now - since[name] > 0.45 and now - repeated[name] > 0.08:
                        repeated[name] = now
                        self._apply_nudge(name)
                    pressed[name] = down
                time.sleep(0.02)

        threading.Thread(target=listen, daemon=True, name="nudge").start()

    def _start_line_listener(self) -> None:
        """保底方案：输入 ``-5`` 回车 = 提前 100ms，``+3`` = 延后 60ms。"""

        def listen() -> None:
            while not self._stop:
                try:
                    line = sys.stdin.readline()
                except Exception:  # noqa: BLE001
                    return
                if not line:
                    return
                text = line.strip()
                if not text:
                    continue
                if text.lower() == "q":
                    self.logger("[微调] 停止")
                    self._stop = True
                    return
                sign = -1 if text.startswith("-") else 1
                digits = text.lstrip("+-")
                try:
                    steps = int(digits) if digits else 1
                except ValueError:
                    steps = 1
                self.shift += sign * 0.02 * max(1, steps)
                self.logger(f"[微调] {self.shift:+.2f}s（{'提前' if sign < 0 else '延后'} {steps}×20ms）")

        threading.Thread(target=listen, daemon=True, name="nudge-line").start()

    # -- 主循环 -------------------------------------------------------
    def run(self) -> dict[str, Any]:
        self.backend.wait_ready()
        start = self.start_at if self.start_at is not None else time.perf_counter() + self.lead

        song_now = time.perf_counter() - start if self.start_at is not None else 0.0
        pending = [t for t in self.plan.touches if t.down_t > song_now - 0.02]
        skipped = len(self.plan.touches) - len(pending)
        if skipped > 0:
            self.logger(f"跳过已经过期的 {skipped} 个音符（从歌曲第 {song_now:.2f}s 开始）")

        events: list[_Event] = []
        for touch in pending:
            events.append(_Event(touch.down_t, "down", touch.pointer, touch.down))
            for at, point in touch.moves:
                events.append(_Event(at, "move", touch.pointer, point))
            events.append(_Event(touch.up_t, "up", touch.pointer))
        events.sort(key=lambda e: (e.t, e.kind != "up"))

        if self.nudge:
            if self.nudge_mode == "line":
                self.logger("播放中每行输入 -5 回车 = 提前 100ms，+3 = 延后 60ms，q 停止")
            else:
                self.logger("微调（全局按键，终端不用在前台）：↑/↓ = 20ms，PageUp/PageDown = 200ms，q 停止")
        self._start_nudge_listener()

        self.logger(f"下发 {len(pending)} 个触控 / {len(events)} 个事件，曲长 {self.plan.duration:.2f}s")

        dispatch = {"down": self.backend.press, "move": self.backend.drag}
        for event in events:
            if self._stop:
                break
            if not self.realtime:
                self._dispatch(dispatch, event)
                continue
            self._wait_until(start + event.t / self.speed + self.shift)
            if self._stop:
                break
            self._dispatch(dispatch, event)

        self.backend.close()
        return {
            "touches": len(pending),
            "events": len(events),
            "fired": self._fired,
            "shift": round(self.shift, 3),
            "errors": self.errors,
        }

    def _dispatch(self, dispatch, event: _Event) -> None:
        try:
            if event.kind == "up":
                self.backend.release(event.pointer)
            else:
                dispatch[event.kind](event.pointer, event.point)
            self._fired += 1
        except Exception as exc:  # noqa: BLE001
            self.errors.append(f"{event.kind} 失败：{exc!r}")

    @staticmethod
    def _wait_until(target: float) -> None:
        while True:
            remaining = target - time.perf_counter()
            if remaining <= 0:
                return
            if remaining > 0.002:
                time.sleep(remaining - 0.0015)
            else:
                while time.perf_counter() < target:
                    pass
                return


def make_backend(name: str, **kwargs: Any) -> Backend:
    if name == "dryrun":
        return DryRunBackend(kwargs.get("log_path"))
    if name == "adb":
        return AdbInputBackend(adb=kwargs.get("adb", "adb"), serial=kwargs.get("serial"))
    if name == "scrcpy":
        from .scrcpy import ScrcpyBackend, ScrcpyServer

        server = ScrcpyServer(
            adb=kwargs.get("adb", "adb"),
            serial=kwargs.get("serial"),
            jar=kwargs.get("scrcpy_jar"),
            version=kwargs.get("scrcpy_version"),
            port=kwargs.get("port", 27183),
            screen=kwargs.get("screen"),
            show_touches=kwargs.get("show_touches", False),
        )
        return ScrcpyBackend(server)
    raise ValueError(f"未知后端：{name}（可选 dryrun / adb / scrcpy）")
