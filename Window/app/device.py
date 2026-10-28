"""设备与配置：adb 发现、root、屏幕尺寸、开始按钮坐标。"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # v1.0/Window
REPO = ROOT.parents[1]                              # 仓库根
CONFIG_PATH = ROOT / "config.json"

DEFAULTS = {
    "adb": "",
    "serial": "",
    "charts_dir": str(REPO / "charts"),
    "countdown": 5,
    "delay_ms": 0,
    "start_tap": "",
    "difficulty": "IN",
    "use_root": True,
}


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        pass
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def find_adb(prefer: str = "") -> str:
    if prefer and Path(prefer).is_file():
        return prefer
    local = shutil.which("adb")
    if local:
        return local
    for candidate in REPO.rglob("adb.exe"):
        return str(candidate)
    raise RuntimeError("没找到 adb（把 scrcpy 放到仓库目录，或安装 platform-tools）")


def _run(cmd: list[str], timeout: float = 20.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout, check=False
    )


class Device:
    def __init__(self, adb: str, serial: str = ""):
        self.adb = adb
        self.serial = serial
        self._screen: tuple[int, int] | None = None

    # -- 基础 ---------------------------------------------------------
    def _cmd(self, *args: str, timeout: float = 20.0) -> list[str]:
        cmd = [self.adb]
        if self.serial:
            cmd += ["-s", self.serial]
        return cmd + list(args)

    def shell(self, command: str, timeout: float = 20.0) -> str:
        result = _run(self._cmd("shell", command), timeout=timeout)
        return (result.stdout or "") + (result.stderr or "")

    @staticmethod
    def list_devices(adb: str) -> list[str]:
        result = _run([adb, "devices"])
        devices = []
        for line in result.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                devices.append(parts[0])
        return devices

    # -- 状态 ---------------------------------------------------------
    def is_root(self) -> bool:
        return "uid=0" in self.shell("id")

    def try_root(self) -> bool:
        _run(self._cmd("root"), timeout=20)
        for _ in range(12):
            if self.is_root():
                return True
            import time

            time.sleep(0.5)
        return False

    def screen(self) -> tuple[int, int]:
        """返回横屏下的 (宽, 高)。"""
        if self._screen:
            return self._screen
        text = self.shell("wm size")
        width = height = 0
        for line in text.splitlines():
            if "x" not in line:
                continue
            size = line.split(":")[-1].strip()
            try:
                w, h = (int(v) for v in size.split("x"))
            except ValueError:
                continue
            width, height = w, h
        if not width:
            raise RuntimeError(f"读不到屏幕尺寸：{text.strip()}")
        if width < height:
            width, height = height, width
        self._screen = (width, height)
        return self._screen

    def start_tap(self, override: str = "") -> tuple[int, int]:
        if override:
            parts = override.replace("，", ",").split(",")
            if len(parts) == 2:
                return int(parts[0]), int(parts[1])
        width, height = self.screen()
        return int(width * 0.95), int(height * 0.87)

    def game_running(self) -> bool:
        return "com.PigeonGames.Phigros" in self.shell("pidof com.PigeonGames.Phigros; ps -A | grep PigeonGames")
