"""打歌会话：算计划、连注入通道、等同步、下发触控。"""

from __future__ import annotations

import ctypes
import threading
import time
from pathlib import Path

from engine.chart import Chart
from engine.play import Player
from engine.schedule import Calibration, Point, build_plan
from engine.scrcpy import ScrcpyBackend, ScrcpyServer

VK_SPACE = 0x20
VK_RETURN = 0x0D


def wait_sync_key(stop: threading.Event, timeout: float = 120.0) -> float | None:
    """等一次空格/回车，返回按下瞬间的时间戳（不消费控制台输入）。"""
    if not hasattr(ctypes, "windll"):
        return time.perf_counter()
    user32 = ctypes.windll.user32

    def pressed() -> bool:
        return bool(user32.GetAsyncKeyState(VK_SPACE) & 0x8000 or user32.GetAsyncKeyState(VK_RETURN) & 0x8000)

    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if stop.is_set():
            return None
        if pressed():
            stamp = time.perf_counter()
            while pressed() and not stop.is_set():
                time.sleep(0.005)
            return stamp
        time.sleep(0.004)
    return None


class Session(threading.Thread):
    """一次完整的「点开始 → 同步 → 打完」。"""

    def __init__(
        self,
        *,
        chart_path: str | Path,
        adb: str,
        serial: str,
        screen: tuple[int, int],
        start_tap: tuple[int, int],
        countdown: int = 5,
        delay_ms: int = 0,
        jar: str | Path | None = None,
        on_log=print,
        on_status=lambda text: None,
        on_done=lambda: None,
        on_progress=lambda done, total: None,
    ):
        super().__init__(daemon=True)
        self.chart_path = Path(chart_path)
        self.adb = adb
        self.serial = serial
        self.screen = screen
        self.start_tap = start_tap
        self.countdown = countdown
        self.delay_ms = delay_ms
        self.jar = jar
        self.on_log = on_log
        self.on_status = on_status
        self.on_done = on_done
        self.on_progress = on_progress
        self.stop_event = threading.Event()
        self._player: Player | None = None
        self._server: ScrcpyServer | None = None
        self.touches = 0

    # -- 控制 ---------------------------------------------------------
    def stop(self) -> None:
        self.stop_event.set()
        if self._player is not None:
            self._player._stop = True

    @property
    def running(self) -> bool:
        return self.is_alive() and not self.stop_event.is_set()

    # -- 主流程 -------------------------------------------------------
    def run(self) -> None:
        try:
            self._run()
        except Exception as exc:  # noqa: BLE001
            self.on_log(f"[错误] {exc}")
            self.on_status("已停止（出错）")
        finally:
            if self._server is not None:
                try:
                    self._server.close()
                except Exception:  # noqa: BLE001
                    pass
            self.on_done()

    def _run(self) -> None:
        self.on_status("准备中…")
        chart = Chart.load(self.chart_path)
        calib = Calibration(width=self.screen[0], height=self.screen[1])
        plan = build_plan(chart, calib, offset=0.0)
        self.touches = len(plan.touches)
        first = plan.touches[0].down_t if plan.touches else 0.0
        self.on_log(
            f"谱面：{self.chart_path.stem}  触控 {len(plan.touches)}  峰值手指 {plan.max_fingers()}  时长 {plan.duration:.1f}s"
        )

        self.on_status("连接注入通道…")
        self._server = ScrcpyServer(
            adb=self.adb,
            serial=self.serial or None,
            jar=self.jar,
            port=27183,
            screen=self.screen,
            show_touches=False,
            logger=lambda text: self.on_log(f"[scrcpy] {text}"),
        )
        self._server.start()
        backend = ScrcpyBackend(self._server)
        self.on_log("注入通道就绪")

        if self.countdown > 0:
            for remaining in range(self.countdown, 0, -1):
                if self.stop_event.is_set():
                    return
                self.on_status(f"{remaining} 秒后自动点「开始」…")
                time.sleep(1.0)

        self.on_status("点击「开始」")
        x, y = self.start_tap
        backend.press(9, Point(x, y))
        time.sleep(0.06)
        backend.release(9)

        self.on_status("等你在第一个音符处按空格…")
        moment = wait_sync_key(self.stop_event)
        if moment is None:
            self.on_log("没有收到同步按键，已取消")
            self.on_status("待机")
            return

        shift = self.delay_ms / 1000.0
        start_at = moment - first + shift
        self.on_log(f"同步完成：t0 = 按键时刻 - {first:.2f}s，判定延迟 {self.delay_ms:+d} ms")

        player = Player(
            plan,
            backend,
            speed=1.0,
            realtime=True,
            start_at=start_at,
            shift=0.0,
            nudge=False,
            logger=lambda text: self.on_log(text),
        )
        self._player = player
        self.on_status("打歌中…")
        result = player.run()
        self.on_log(f"完成：下发 {result.get('fired', 0)} 个事件，错误 {len(result.get('errors', []))} 个")
        self.on_status("待机")
