"""通过 scrcpy-server 注入触控（免 root）。

原理：scrcpy-server 以 `shell` 用户身份运行 `app_process`，拥有
`android.permission.INJECT_EVENTS`，因此可以调用
`InputManager.injectInputEvent` 把触摸事件塞进系统 —— 不需要 root。

协议按 scrcpy **v2.4** 的源码实现（`Server.java` / `Options.java` /
`ControlMessageReader.java`）：

* 服务端参数是 `key=value` 形式，第一个参数必须等于服务端版本号；
* 关掉视频和音频后，服务端只接受 **一个** control socket；
* 连上后服务端先写 1 个字节（`send_dummy_byte`），之后才开始收控制消息；
* 触控消息共 32 字节（大端）：

    ``u8 type=2 | u8 action | i64 pointerId | i32 x | i32 y |
    u16 screenW | u16 screenH | u16 pressure | i32 actionButton | i32 buttons``

其中 ``screenW/screenH`` 必须与服务端认为的显示尺寸完全一致，否则事件会被
静默丢弃（`Device.getPhysicalPoint()` 会返回 null）。
"""

from __future__ import annotations

import re
import shutil
import socket
import struct
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from .play import Backend
from .schedule import Point

# 控制消息类型（ControlMessage.java）
MSG_INJECT_KEYCODE = 0
MSG_INJECT_TEXT = 1
MSG_INJECT_TOUCH = 2

# MotionEvent 的 action
ACTION_DOWN = 0
ACTION_MOVE = 2
ACTION_UP = 1

# 触摸压力用 16 位定点数表示
_PRESSURE_MAX = 0xFFFF

PACKAGE = "com.PigeonGames.Phigros"


def encode_touch(
    action: int,
    pointer: int,
    x: int,
    y: int,
    screen_w: int,
    screen_h: int,
    pressure: float = 1.0,
) -> bytes:
    """把一次触摸编码成 scrcpy 的 INJECT_TOUCH_EVENT（32 字节，大端）。"""
    p = int(round(max(0.0, min(1.0, pressure)) * _PRESSURE_MAX))
    return struct.pack(
        ">BBqiiHHHii",
        MSG_INJECT_TOUCH,
        action & 0xFF,
        pointer,
        int(x),
        int(y),
        int(screen_w),
        int(screen_h),
        p,
        0,  # actionButton
        0,  # buttons（触摸事件必须为 0）
    )


class ScrcpyControl:
    """一条已经连上的控制通道。与 adb 解耦，方便单测。"""

    def __init__(self, sock: socket.socket, screen: tuple[int, int], logger=print):
        self._sock = sock
        self._screen = (int(screen[0]), int(screen[1]))
        self._lock = threading.Lock()
        self.logger = logger
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass

    @property
    def screen(self) -> tuple[int, int]:
        return self._screen

    def send_touch(self, action: int, pointer: int, point: Point) -> None:
        pressure = 0.0 if action == ACTION_UP else 1.0
        data = encode_touch(
            action, pointer, point.x, point.y, self._screen[0], self._screen[1], pressure
        )
        with self._lock:
            self._sock.sendall(data)

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


def find_adb(explicit: str | None = None) -> str:
    """找 adb：显式指定 > PATH > 仓库里 scrcpy 压缩包自带的那个。"""
    if explicit and explicit not in ("adb", "adb.exe"):
        return explicit
    found = shutil.which("adb")
    if found:
        return found
    here = Path(__file__).resolve().parents[1]
    for pattern in (
        "adb.exe",
        "*/adb.exe",
        "*/*/adb.exe",
        "*/*/*/adb.exe",
        "adb",
        "*/adb",
        "*/*/adb",
    ):
        for candidate in sorted(here.glob(pattern)):
            if candidate.is_file():
                return str(candidate)
    raise FileNotFoundError(
        "找不到 adb。把 platform-tools 加进 PATH，或用 --adb 指定 adb.exe 的完整路径"
        "（scrcpy 的 Windows 压缩包里通常自带 adb.exe）。"
    )


def find_server_jar(explicit: str | Path | None = None) -> Path:
    """找一个可用的 scrcpy-server jar。"""
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise FileNotFoundError(f"找不到 scrcpy-server：{path}")
        return path

    here = Path(__file__).resolve().parents[1]
    for candidate in sorted(here.glob("scrcpy-server*")):
        if candidate.is_file() and candidate.name.startswith("scrcpy-server"):
            return candidate

    which = shutil.which("scrcpy")
    if which:
        base = Path(which).resolve().parent
        for candidate in base.rglob("scrcpy-server*"):
            if candidate.is_file():
                return candidate

    raise FileNotFoundError(
        "找不到 scrcpy-server。\n"
        "  1) 从 https://github.com/Genymobile/scrcpy/releases 下载 release；\n"
        "  2) 把解压出来的 scrcpy-server 复制到本目录，命名成 scrcpy-server-v<版本>；\n"
        "  3) 或者用 --scrcpy-jar 指定完整路径。"
    )


_VERSION_MARKER = rb"The server version \((\d+\.\d+(?:\.\d+)?)\)"


def detect_server_version(jar: Path) -> str | None:
    """从服务端自己的错误文案里抠版本号。

    scrcpy-server 是个 zip（内含 classes.dex），dex 的字符串池里有一条
    ``The server version (5.0.1) does not match the client``，比文件名可靠。
    """
    import zipfile

    try:
        with zipfile.ZipFile(jar) as zf:
            for name in ("classes.dex", "classes2.dex"):
                if name in zf.namelist():
                    match = re.search(_VERSION_MARKER, zf.read(name))
                    if match:
                        return match.group(1).decode()
    except (zipfile.BadZipFile, OSError):
        pass
    try:
        match = re.search(_VERSION_MARKER, jar.read_bytes())
    except OSError:
        return None
    return match.group(1).decode() if match else None


def guess_version(jar: Path) -> str | None:
    """先看文件名（scrcpy-server-v5.0.1），不行再读文件内容。"""
    match = re.search(r"v(\d+\.\d+(?:\.\d+)?)", jar.name)
    if match:
        return match.group(1)
    return detect_server_version(jar)


class ScrcpyServer:
    """负责 push / forward / 启动 / 连接 / 关闭整个生命周期。"""

    def __init__(
        self,
        adb: str = "adb",
        serial: str | None = None,
        jar: str | Path | None = None,
        version: str | None = None,
        port: int = 27183,
        screen: tuple[int, int] | None = None,
        show_touches: bool = False,
        audio: bool = False,
        logger=print,
    ):
        self.adb = find_adb(adb)
        self.serial = serial
        self.port = port
        self.show_touches = show_touches
        self.audio = audio
        self.logger = logger
        self.audio_socket: socket.socket | None = None
        self.jar = find_server_jar(jar)
        detected = version or guess_version(self.jar)
        if not detected:
            raise ValueError(
                f"认不出 {self.jar.name} 的版本号，请用 --scrcpy-version 指定"
                "（例如 5.0.1）"
            )
        self.version = detected
        self.screen = screen
        self._proc: subprocess.Popen[str] | None = None
        self._sock: socket.socket | None = None
        self._control: ScrcpyControl | None = None
        self._old_show_touches: str | None = None
        self._remote_jar = "/data/local/tmp/phigros_scrcpy.jar"

    # -- adb 辅助 -----------------------------------------------------
    def _adb(self, *args: str, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
        cmd = [self.adb]
        if self.serial:
            cmd += ["-s", self.serial]
        cmd += list(args)
        return subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    def _require_device(self) -> None:
        result = self._adb("devices")
        if result.returncode != 0:
            raise RuntimeError(
                f"adb 跑不起来：{result.stderr.strip() or result.stdout.strip()}\n"
                "确认已安装 platform-tools 并把 adb 加进 PATH。"
            )
        lines = [line for line in result.stdout.splitlines()[1:] if line.strip()]
        states = {line.split("\t")[0]: line.split("\t")[-1] for line in lines if "\t" in line}
        if self.serial:
            if states.get(self.serial) != "device":
                raise RuntimeError(f"设备 {self.serial} 状态是 {states.get(self.serial)!r}，不是 device")
        elif not any(state == "device" for state in states.values()):
            raise RuntimeError(f"没有已授权的设备：{states or '空'}")
        elif len(states) > 1:
            self.logger(f"[scrcpy] 检测到多台设备，使用第一台；也可以加 --serial")

    def detect_screen(self) -> tuple[int, int]:
        """取当前显示尺寸（会跟随横竖屏）。"""
        result = self._adb("shell", "dumpsys", "window", "displays")
        match = re.search(r"cur=(\d+)x(\d+)", result.stdout or "")
        if match:
            return int(match.group(1)), int(match.group(2))
        result = self._adb("shell", "wm", "size")
        match = re.search(r"(\d+)x(\d+)", result.stdout or "")
        if match:
            return int(match.group(1)), int(match.group(2))
        raise RuntimeError("读不到设备分辨率，请用 --screen 1080x2400 手动指定")

    # -- 生命周期 -----------------------------------------------------
    def start(self) -> ScrcpyControl:
        if self._control is not None:
            return self._control
        self._require_device()
        if self.screen is None:
            self.screen = self.detect_screen()
            self.logger(f"[scrcpy] 屏幕尺寸 {self.screen[0]}x{self.screen[1]}")

        pushed = self._adb("push", str(self.jar), self._remote_jar, timeout=120)
        if pushed.returncode != 0:
            raise RuntimeError(f"push scrcpy-server 失败：{pushed.stderr.strip()}")

        forwarded = self._adb("forward", f"tcp:{self.port}", "localabstract:scrcpy")
        if forwarded.returncode != 0:
            # 上一次残留的 forward 会占着端口，清掉再试一次
            self._adb("forward", "--remove", f"tcp:{self.port}")
            forwarded = self._adb("forward", f"tcp:{self.port}", "localabstract:scrcpy")
        if forwarded.returncode != 0:
            raise RuntimeError(
                f"adb forward 失败：{forwarded.stderr.strip()}\n"
                f"可能是端口 {self.port} 被占用，用 --port 换一个。"
            )

        if self.show_touches:
            # 服务端在 cleanup=false 时不会替我们改这个开关，直接自己写；
            # close() 里会还原。
            if self._old_show_touches is None:
                self._old_show_touches = self._read_show_touches()
            self._adb("shell", "settings", "put", "system", "show_touches", "1")

        options = [
            f"CLASSPATH={self._remote_jar}",
            "app_process",
            "/",
            "com.genymobile.scrcpy.Server",
            self.version,
            "log_level=info",
            "video=false",
            "audio=false",
            "control=true",
            "tunnel_forward=true",
            "cleanup=false",
            "send_device_meta=false",
            "send_dummy_byte=true",
            "power_on=false",
        ]
        if self.audio:
            # 原始 PCM：48kHz 立体声 16bit 小端，不要包头
            options += [
                "audio=true",
                "audio_codec=raw",
                "audio_source=output",
                "send_frame_meta=false",
                "send_codec_meta=false",
            ]
        else:
            options.append("audio=false")
        cmd = [self.adb]
        if self.serial:
            cmd += ["-s", self.serial]
        cmd += ["shell", *options]
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,   # 别抢控制台输入，否则 input() 收不到回车
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        self._sock = self._connect()
        if self.audio:
            # 关掉 video 时，服务端先 accept 音频、再 accept 控制
            self.audio_socket = self._sock
            self._sock = self._connect_plain()
        try:
            self._control = ScrcpyControl(self._sock, self.screen, logger=self.logger)
            return self._control
        except Exception:
            # 失败时把 forward 和进程都收掉，避免下次端口被占
            self.close()
            raise

    def _connect(self) -> socket.socket:
        """连上并完成握手。

        注意：``adb forward`` 会**先**接受本地 TCP 连接，再去连设备侧 socket；
        服务端 app_process 起来要一两秒，这期间连接会被立刻断开（recv 返回空）。
        所以不能把第一次 EOF 当失败，要一直重试到真的收到那 1 个握手字节。
        """
        deadline = time.monotonic() + 25.0
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                raise RuntimeError(self._server_log("scrcpy-server 启动后立即退出"))
            sock: socket.socket | None = None
            try:
                sock = socket.create_connection(("127.0.0.1", self.port), timeout=3.0)
                sock.settimeout(3.0)
                try:
                    dummy = sock.recv(1)
                except socket.timeout:
                    dummy = b""
                if dummy == b"\x00":
                    sock.settimeout(10.0)
                    return sock
                sock.close()
            except OSError as exc:
                last_error = exc
                if sock is not None:
                    sock.close()
            time.sleep(0.15)
        raise RuntimeError(self._server_log(f"连不上 scrcpy-server：{last_error}"))

    def _connect_plain(self) -> socket.socket:
        """控制 socket：服务端 accept 后不会再发握手字节。"""
        deadline = time.monotonic() + 10.0
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                sock = socket.create_connection(("127.0.0.1", self.port), timeout=3.0)
                sock.settimeout(10.0)
                return sock
            except OSError as exc:
                last_error = exc
                time.sleep(0.1)
        raise RuntimeError(self._server_log(f"控制 socket 连不上：{last_error}"))

    def _server_log(self, message: str) -> str:
        output = ""
        if self._proc is not None and self._proc.stdout is not None:
            try:
                self._proc.terminate()
                output = self._proc.stdout.read() or ""
            except Exception:  # noqa: BLE001
                output = ""
        return f"{message}\n--- scrcpy-server 输出 ---\n{output.strip() or '(空)'}"

    def _read_show_touches(self) -> str:
        result = self._adb("shell", "settings", "get", "system", "show_touches")
        value = (result.stdout or "").strip()
        return value if value in {"0", "1"} else "0"

    def _restore_show_touches(self) -> None:
        """把我们改过的“显示触摸点”开关还原，别给用户留副作用。"""
        if self._old_show_touches is None:
            return
        self._adb("shell", "settings", "put", "system", "show_touches", self._old_show_touches)
        self._old_show_touches = None

    def close(self) -> None:
        if self._control is not None:
            self._control.close()
            self._control = None
        elif self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        if self.audio_socket is not None:
            try:
                self.audio_socket.close()
            except OSError:
                pass
            self.audio_socket = None
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None
        self._adb("forward", "--remove", f"tcp:{self.port}", timeout=10)
        self._restore_show_touches()

    def __enter__(self) -> "ScrcpyServer":
        self.start()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()


class ScrcpyBackend(Backend):
    """把 Player 的 press/drag/release 转成 scrcpy 触控消息。"""

    name = "scrcpy"

    def __init__(self, server: ScrcpyServer):
        self.server = server
        self._control: ScrcpyControl | None = None
        self._last: dict[int, Point] = {}

    def wait_ready(self) -> None:
        if self._control is None:
            self._control = self.server.start()

    def _send(self, action: int, pointer: int, point: Point) -> None:
        if self._control is None:
            self.wait_ready()
        assert self._control is not None
        self._control.send_touch(action, pointer, point)

    def press(self, pointer: int, point: Point) -> None:
        self._last[pointer] = point
        self._send(ACTION_DOWN, pointer, point)

    def drag(self, pointer: int, point: Point) -> None:
        self._last[pointer] = point
        self._send(ACTION_MOVE, pointer, point)

    def release(self, pointer: int) -> None:
        if self._control is None:
            return
        # 抬起必须带上最后的位置：落在屏幕中心会被系统当成一次滑动
        point = self._last.pop(pointer, None)
        if point is None:
            width, height = self._control.screen
            point = Point(width // 2, height // 2)
        self._control.send_touch(ACTION_UP, pointer, point)

    def close(self) -> None:
        self.server.close()
        self._control = None


def tap_grid(control: ScrcpyControl, logger=print, *, countdown: int = 3, hold_touches: float = 3.0) -> None:
    """在四角和中心各点一下，用来确认「注入通道 + 分辨率」都对。"""
    width, height = control.screen
    points = [
        (width // 2, height // 2),
        (width // 4, height // 4),
        (width * 3 // 4, height // 4),
        (width // 4, height * 3 // 4),
        (width * 3 // 4, height * 3 // 4),
    ]
    logger(f"屏幕 {width}x{height}，依次点击：中心 / 左上 / 右上 / 左下 / 右下")
    logger("现在盯着手机屏幕（有些 ROM 不画白点，重点看界面有没有响应）")
    for remaining in range(countdown, 0, -1):
        logger(f"{remaining}...")
        time.sleep(1.0)

    for index, (x, y) in enumerate(points):
        control.send_touch(ACTION_DOWN, index, Point(x, y))
        time.sleep(0.08)
        control.send_touch(ACTION_UP, index, Point(x, y))
        logger(f"  点击 ({x}, {y})")
        time.sleep(0.35)

    if hold_touches > 0:
        logger(f"触摸反馈开关再保持 {hold_touches:.0f} 秒…")
        time.sleep(hold_touches)
    logger("完成：如果 5 下都点在预期的位置，说明通道和坐标都对。")
