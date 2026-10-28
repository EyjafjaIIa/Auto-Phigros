"""一键自动打歌：导出并推送计划 -> 拉起手机面板 -> 毫秒级同步开始。

优先走 App 的本地控制端口（毫秒级）；连不上时回退到「点面板按钮 + 视觉同步」。
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from phone_autoplay import PhoneAutoplay
from phone_control import PhoneControl

APP_DIR = Path(__file__).resolve().parent
PLAN_DIR = "/sdcard/Android/data/io.phigros.auto/files/plans"


def _adb(device, *args: str) -> subprocess.CompletedProcess:
    base = [device.adb] + (["-s", device.serial] if device.serial else [])
    return subprocess.run(
        base + list(args),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
    )


JAR_LOCAL = APP_DIR / "scrcpy-server-v5.0.1"
JAR_REMOTE = "/data/local/tmp/phigros_scrcpy.jar"


OCR_SCRIPT = APP_DIR.parent.parent.parent / "releases" / "scripts" / "ocr.ps1"


def _reset_control(device) -> None:
    """尽力通知 App 关闭旧连接（失败无所谓）。"""
    import socket

    try:
        subprocess.run(
            [device.adb] + (["-s", device.serial] if device.serial else [])
            + ["forward", "tcp:27184", "tcp:27184"],
            capture_output=True,
            check=False,
        )
        sock = socket.create_connection(("127.0.0.1", 27184), timeout=2)
        sock.sendall(b"reset\n")
        sock.recv(16)
        sock.close()
    except Exception:  # noqa: BLE001
        pass


def game_song_matches(device, song: str, log) -> bool:
    """截图 + OCR，检查游戏里当前选中的曲名是否包含计划曲名。OCR 不可用时返回 True。"""
    try:
        import json
        import tempfile

        from PIL import Image

        base = [device.adb] + (["-s", device.serial] if device.serial else [])
        data = subprocess.run(
            base + ["exec-out", "screencap", "-p"], capture_output=True, check=False
        ).stdout
        shot = Path(tempfile.gettempdir()) / "phigros_game_check.png"
        shot.write_bytes(data)
        ocr_json = Path(tempfile.gettempdir()) / "phigros_game_check.json"
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(OCR_SCRIPT),
             "-ImagePath", str(shot), "-OutPath", str(ocr_json)],
            capture_output=True,
            check=False,
        )
        lines = json.loads(ocr_json.read_text(encoding="utf-8"))
        text = "".join(str(item.get("text", "")) for item in lines).replace(" ", "").lower()
        target = song.replace(" ", "").lower()
        if target and target in text:
            log(f"游戏曲目核对：{song} ✓")
            return True
        log(f"[警告] 游戏里似乎不是 {song}，请先在游戏内选中同一首再点一键打歌")
        return False
    except Exception:  # noqa: BLE001
        return True


def ensure_channel(device, log) -> bool:
    """确保手机上有一个 scrcpy 服务端在监听（App 会直接连它，不需要 App 自己的 root）。"""
    probe = _adb(device, "shell", "cat /proc/net/unix | grep -c scrcpy")
    count = (probe.stdout or b"").decode("utf-8", "replace").strip()
    if count and count != "0":
        # 可能残留了多个服务端（抢同一个 socket），统一清掉再起一个
        log("清理旧的注入通道…")
        _reset_control(device)
        _adb(device, "shell", "pkill -f com.genymobile.scrcpy.Server")
        time.sleep(1.0)

    if not JAR_LOCAL.is_file():
        log(f"[错误] 缺少注入服务端文件：{JAR_LOCAL.name}")
        return False
    pushed = _adb(device, "push", str(JAR_LOCAL), JAR_REMOTE)
    if pushed.returncode != 0:
        log("[错误] 推送注入服务端失败")
        return False

    command = (
        f"CLASSPATH={JAR_REMOTE} app_process / com.genymobile.scrcpy.Server 5.0.1 "
        "log_level=info video=false audio=false control=true tunnel_forward=true "
        "cleanup=false send_device_meta=false send_dummy_byte=true power_on=false"
    )
    # nohup + & ：脱离 adb shell，电脑端退出也不会把服务端带走
    wrapped = f"nohup env {command} >/dev/null 2>&1 &"
    _adb(device, "shell", wrapped)
    time.sleep(2.5)
    probe = _adb(device, "shell", "cat /proc/net/unix | grep -c scrcpy")
    ok = (probe.stdout or b"").decode("utf-8", "replace").strip() not in ("", "0")
    log("注入通道已启动（电脑端）" if ok else "[错误] 注入通道启动失败")
    return ok


def run(device, item: dict, screen: tuple[int, int], log, delay_ms: int = 0,
        show_touch: bool = True) -> bool:
    """item 至少包含 song/diff/path；delay_ms 会同时写给手机端与同步逻辑。"""
    from export_plan import build_events

    log(f"编译计划：{item['song']} [{item['diff']}] {screen[0]}x{screen[1]}")
    data = build_events(item["path"], screen[0], screen[1])
    local = APP_DIR / "out" / f"{item['song']}.{item['diff']}.plan.json"
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(f"本地计划：{local.name}（{len(data['events'])} 事件）")

    _adb(device, "shell", f"mkdir -p {PLAN_DIR}")
    target = f"{PLAN_DIR}/{item['song']}.{item['diff']}.json"
    pushed = _adb(device, "push", str(local), target)
    if pushed.returncode != 0:
        log(f"[错误] 推送失败：{pushed.stderr.decode('utf-8', 'replace').strip()}")
        return False
    log(f"已推送到手机：{target}")

    import tempfile

    delay_file = Path(tempfile.gettempdir()) / "phigros_delay_ms.txt"
    delay_file.write_text(str(int(delay_ms or 0)), encoding="utf-8")
    _adb(device, "push", str(delay_file), "/sdcard/Android/data/io.phigros.auto/files/delay_ms.txt")
    log(f"判定延迟已写入手机：{int(delay_ms or 0)} ms")

    value = "1" if show_touch else "0"
    _adb(device, "shell", "settings", "put", "system", "show_touches", value)
    _adb(device, "shell", "settings", "put", "system", "pointer_location", value)
    log("运行时显示触摸操作：" + ("开" if show_touch else "关"))

    # 仅做提示：OCR 认不准时不能挡住正常打歌
    if not game_song_matches(device, item["song"], log):
        log("（继续执行；如果成绩很低，先确认游戏内选的是同一首歌）")

    if not ensure_channel(device, log):
        return False

    # 重启后 adbd 会退回普通权限，起前台的非导出服务需要 root
    _adb(device, "root")
    time.sleep(2)
    _adb(device, "wait-for-device")

    started = _adb(
        device, "shell", "am", "start-foreground-service", "-n", "io.phigros.auto/.OverlayService"
    )
    message = (started.stdout or b"").decode("utf-8", "replace") + (
        started.stderr or b""
    ).decode("utf-8", "replace")
    if "Error" in message or "Exception" in message:
        log(f"[错误] 手机面板拉不起来：{message.strip()[:120]}")
        log("       试试在手机上手动打开一次 PhigrosAuto")
        return False
    time.sleep(3)

    # 首选：控制端口（毫秒级，不需要点面板按钮）
    control = PhoneControl(
        device.adb, device.serial, log, plan_events=data.get("events"), delay_ms=int(delay_ms or 0)
    )
    if control.connect():
        control.close()
        if control.run():
            return True
        log("控制端口方式没成功，回退到面板点击方式")

    first_event = data["events"][0] if data.get("events") else None
    first_x = int(first_event[3]) if first_event else None
    helper = PhoneAutoplay(
        device.adb,
        device.serial,
        log,
        first_x=first_x,
        plan_events=data.get("events"),
        delay_ms=int(delay_ms or 0),
    )
    return helper.run()
