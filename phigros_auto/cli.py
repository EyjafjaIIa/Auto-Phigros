"""命令行入口。"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import re
import sys
import time
from pathlib import Path

from . import __version__
from .chart import Chart, ChartError
from .play import make_backend, Player
from .calibration import get_shift, remember_shift
from .schedule import Calibration, Point, build_plan, plan_to_csv


def _add_calib_opts(parser: argparse.ArgumentParser, width: int, height: int) -> None:
    parser.add_argument("--width", type=int, default=width, help="屏幕宽度（像素）")
    parser.add_argument("--height", type=int, default=height, help="屏幕高度（像素）")
    parser.add_argument("--span", type=float, default=1.0, help="判定线长度 / 屏宽")
    parser.add_argument("--x-shift", type=float, default=0.0, help="整体横向平移（占屏宽比例）")
    parser.add_argument("--y-shift", type=float, default=0.0, help="整体纵向平移（占屏高比例）")
    parser.add_argument("--offset", type=float, default=0.0, help="时间偏移（秒），音频对齐结果填这里")
    parser.add_argument(
        "--note-unit",
        type=float,
        default=72.0,
        help="音符 x 的换算：1 单位 = 多少像素（720 高基准，官方谱面 72）",
    )
    parser.add_argument(
        "--note-x-mode",
        default="auto",
        choices=["auto", "centered", "unit"],
        help="音符 x 坐标系，默认按谱面自动判断",
    )


def _add_scrcpy_opts(parser: argparse.ArgumentParser, *, show_touches_default: bool = False) -> None:
    parser.add_argument("--adb", default="adb", help="adb 可执行文件路径")
    parser.add_argument("--serial", help="设备序列号（多台设备时）")
    parser.add_argument("--scrcpy-jar", help="scrcpy-server jar 路径")
    parser.add_argument("--scrcpy-version", help="服务端版本号，默认从 jar 文件名猜")
    parser.add_argument("--port", type=int, default=27183, help="adb forward 的本地端口")
    parser.add_argument("--screen", help="设备分辨率，如 2400x1080；默认自动检测")
    parser.add_argument(
        "--show-touches",
        action="store_true",
        default=show_touches_default,
        help="让系统画出触摸点（调试用）",
    )


def _calib_from(args: argparse.Namespace) -> Calibration:
    return Calibration(
        width=args.width,
        height=args.height,
        span=args.span,
        x_shift=args.x_shift,
        y_shift=args.y_shift,
        note_x_mode=args.note_x_mode,
        note_unit_px=args.note_unit,
    )


def _cmd_info(args: argparse.Namespace) -> int:
    chart = Chart.load(args.chart)
    print(json.dumps(chart.stats(), ensure_ascii=False, indent=2))
    return 0


def _cmd_plan(args: argparse.Namespace) -> int:
    chart = Chart.load(args.chart)
    calib = _calib_from(args)
    plan = build_plan(chart, calib, offset=args.offset)
    print(json.dumps(plan.summary(), ensure_ascii=False, indent=2))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        plan.write_json(out)
        print(f"时间轴 -> {out}")
    if args.csv:
        csv_path = Path(args.csv)
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        csv_path.write_text(plan_to_csv(plan), encoding="utf-8")
        print(f"CSV -> {csv_path}")
    return 0


def _cmd_preview(args: argparse.Namespace) -> int:
    # 只有画图才需要 Pillow，放到这里 import，别的子命令就不必装它
    from .render import default_times, render_contact_sheet, render_gif

    chart = Chart.load(args.chart)
    calib = _calib_from(args)
    plan = None if args.no_fingers else build_plan(chart, calib, offset=args.offset)
    out = Path(args.out)
    times = (
        [float(x) for x in args.times.split(",")]
        if args.times
        else default_times(chart, 4)
    )
    sheet = render_contact_sheet(chart, calib, plan, times, out / "preview.png")
    print(f"对比图 -> {sheet}")
    if args.gif:
        gif = render_gif(
            chart,
            calib,
            plan,
            out / "preview.gif",
            start=args.start,
            end=args.end,
            fps=args.fps,
            scale=args.scale,
        )
        print(f"GIF -> {gif} ({gif.stat().st_size / 1024:.0f} KB)")
    return 0


def _parse_screen(text: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d+)\s*[xX*×]\s*(\d+)", text.strip())
    if not match:
        raise ValueError(f"分辨率格式应为 宽x高，例如 2400x1080，收到：{text!r}")
    return int(match.group(1)), int(match.group(2))


def _parse_start_tap(text: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d+)\s*[,xX]\s*(\d+)", text.strip())
    if not match:
        raise ValueError(f"--start-tap 格式应为 X,Y，例如 2050,950，收到：{text!r}")
    return int(match.group(1)), int(match.group(2))


def _wait_enter(logger=print) -> float:
    """等一次回车或空格，返回**按下瞬间**的 perf_counter 时间戳。

    不用 ``input()``：scrcpy/adb 子进程可能和它抢控制台输入，表现就是
    「按两三下才有反应」。这里用全局按键轮询，完全不碰 stdin。
    """
    if os.name != "nt":
        input()
        return time.perf_counter()

    import ctypes

    user32 = ctypes.windll.user32
    keys = (0x0D, 0x20)  # 回车 / 空格

    def pressed() -> bool:
        return any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in keys)

    while pressed():  # 等上一次按键松开
        time.sleep(0.01)

    while True:
        if pressed():
            stamp = time.perf_counter()
            logger("[同步] 收到按键")
            while pressed():
                time.sleep(0.005)
            return stamp
        time.sleep(0.004)


def _wait_start(args: argparse.Namespace, backend, first_touch: float = 0.0, logger=print) -> float:
    """等到「歌曲第 0 秒」，返回对应的 time.perf_counter() 时刻。

    * ``manual``：点开始后，等你在第一个音符压到判定线时按回车/空格，反推 0 秒；
    * ``enter``：不点开始，音乐响起时按键；
    * ``tap``：点开始后等 ``--start-delay``；
    * ``immediate``：立刻开始。
    """
    for remaining in range(args.countdown, 0, -1):
        logger(f"{remaining}...", flush=True)
        time.sleep(1.0)

    mode = args.start

    def tap_start_button() -> float:
        x, y = _parse_start_tap(args.start_tap)
        logger(f"点击开始按钮 ({x}, {y})")
        backend.press(9, Point(x, y))
        time.sleep(0.06)
        backend.release(9)
        return time.perf_counter()

    if mode == "manual":
        if args.start_tap:
            tap_start_button()
            logger("等第一个音符快落到判定线时，按回车或空格")
        else:
            logger("请自己点「开始」；等第一个音符快落到判定线时，按回车或空格")
        moment = _wait_enter(logger)
        start_at = moment - first_touch + (args.start_delay or 0.0)
        logger(f"t=0 = 刚才那一刻往前 {first_touch:.2f}s")
        return start_at

    if mode == "immediate":
        moment = time.perf_counter()
    elif mode == "enter":
        logger("现在在手机上点「开始」——音乐响起的那一刻按回车。")
        moment = _wait_enter(logger)
    elif mode == "tap":
        if not args.start_tap:
            raise ValueError("--start tap 需要配合 --start-tap X,Y 指定「开始」按钮位置")
        tap_start_button()
        moment = time.perf_counter()
    else:  # pragma: no cover
        raise ValueError(f"未知的开始模式：{mode}")

    delay = args.start_delay or 0.0
    if mode == "tap" and args.start_delay is None:
        delay = 1.5  # 点完「开始」到音乐响，经验值
    logger(f"歌曲 0 秒 = {delay:+.2f}s 之后（可用 --start-delay / --offset 微调）")
    return moment + delay


def _cmd_play(args: argparse.Namespace) -> int:
    screen = _parse_screen(args.screen) if args.screen else None

    backend = None
    if args.backend == "scrcpy":
        from .scrcpy import ScrcpyBackend, ScrcpyServer

        server = ScrcpyServer(
            adb=args.adb,
            serial=args.serial,
            jar=args.scrcpy_jar,
            version=args.scrcpy_version,
            port=args.port,
            screen=screen,
            show_touches=args.show_touches,
            audio=args.audio_sync,
        )
        print("[scrcpy] 启动注入通道…", flush=True)
        control = server.start()
        screen = control.screen
        backend = ScrcpyBackend(server)
        print(f"[scrcpy] 通道就绪，屏幕 {screen[0]}x{screen[1]}")

    audio_stream = None
    song_audio = None
    song_rate = 0
    if args.audio_sync:
        if args.backend != "scrcpy":
            print("--audio-sync 只能用 scrcpy 后端")
            return 2
        from .audio import PcmStream

        audio_stream = PcmStream(server.audio_socket).start()
        if args.audio_sync == "ref":
            if not args.audio_ref:
                print("--audio-sync ref 需要 --audio-ref 指定参考音频")
                return 2
            from .sync import load_wav

            song_audio, song_rate = load_wav(args.audio_ref)
            print(f"[音频] 参考曲目 {len(song_audio)/song_rate:.1f}s，开始接收手机音频")
        else:
            print("[音频] 开始接收手机音频，等音乐响起…")
    else:
        backend = make_backend(args.backend, adb=args.adb, serial=args.serial, log_path=args.log)

    width = args.width or (screen[0] if screen else 1080)
    height = args.height or (screen[1] if screen else 2400)
    calib = Calibration(
        width=width,
        height=height,
        span=args.span,
        x_shift=args.x_shift,
        y_shift=args.y_shift,
        note_x_mode=args.note_x_mode,
        note_unit_px=args.note_unit,
    )

    chart = Chart.load(args.chart)
    plan = build_plan(chart, calib, offset=args.offset)
    print(json.dumps(plan.summary(), ensure_ascii=False))
    if plan.max_fingers() > calib.max_pointers:
        print(f"[警告] 峰值同时需要 {plan.max_fingers()} 根手指")
    for note in chart.warnings:
        print(f"[谱面] {note}")
    if chart.block_areas:
        print("[谱面] 请在游戏里关闭 Random，否则上面的区域会被打乱")

    if args.delay is not None:
        shift = args.delay
    else:
        shift = get_shift(args.chart)
        if shift:
            print(f"[校准] 使用记住的延迟 {shift:+.3f}s（可用 --delay 覆盖）")

    if audio_stream is not None:
        start_at = _start_with_audio(args, backend, audio_stream, song_audio, song_rate)
    else:
        start_at = _wait_start(
            args, backend, plan.touches[0].down_t if plan.touches else 0.0
        )

    player = Player(
        plan,
        backend,
        speed=args.speed,
        realtime=not args.fast,
        start_at=start_at,
        shift=shift,
        nudge=args.nudge,
        nudge_mode="line" if args.nudge_line else "keys",
    )
    result = player.run()
    print(json.dumps(result, ensure_ascii=False))

    # 只有真实跑过、且不是快进/演练，才把结果写回校准文件
    save_ok = args.backend != "dryrun" and not args.fast
    if save_ok and not args.no_save and isinstance(result.get("shift"), (int, float)):
        saved = remember_shift(args.chart, float(result["shift"]))
        print(f"[校准] 已记住 {Path(args.chart).stem} 的延迟 {saved:+.3f}s")

    return 0 if not result["errors"] else 1


def _cmd_scrcpy_test(args: argparse.Namespace) -> int:
    from .scrcpy import ScrcpyServer, tap_grid

    screen = _parse_screen(args.screen) if args.screen else None
    server = ScrcpyServer(
        adb=args.adb,
        serial=args.serial,
        jar=args.scrcpy_jar,
        version=args.scrcpy_version,
        port=args.port,
        screen=screen,
        show_touches=args.show_touches,
    )
    try:
        control = server.start()
        tap_grid(control, countdown=args.countdown)
    finally:
        server.close()
    return 0


def _cmd_sync(args: argparse.Namespace) -> int:
    # numpy 只在音频对齐时需要
    import numpy as np

    from .sync import estimate_offset, load_wav, song_time_at

    song, sr = load_wav(args.song)
    recording, rec_sr = load_wav(args.recording)
    if sr != rec_sr:
        print(f"采样率不同（{sr} vs {rec_sr}），先把录音重采样到 {sr}")
        idx = np.linspace(0, len(recording) - 1, max(2, int(len(recording) * sr / rec_sr)))
        recording = np.interp(idx, np.arange(len(recording)), recording).astype(np.float32)

    result = estimate_offset(song, recording, sr, max_offset=args.max_offset)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"歌曲第 0 秒出现在录音的第 {-result['offset']:.3f} 秒处")
    print(f"播放时把 --offset 设为 {result['offset']:.3f}")
    print(f"校验：录音第 {args.probe:.2f} 秒 = 歌曲第 {song_time_at(result['offset'], args.probe):.2f} 秒")
    return 0


def _audio_onset_t0(
    stream, *, logger=print, timeout: float = 40.0, threshold: float = 0.0012, skip: float = 3.0
) -> float:
    """等「静音 -> 有声音」的跳变，返回音乐起点对应的 perf_counter 时刻。

    加载界面是数字静音，音乐一响就是明确的跳变；实测这条比固定延时靠谱得多。
    """
    import numpy as np

    deadline = time.perf_counter() + timeout
    # 点击音效/转场声都在前几秒，跳过去只找「加载静音 -> 音乐」
    began = time.perf_counter()
    history: list[tuple[float, float]] = []
    streak: list[float] = []
    while time.perf_counter() < deadline:
        time.sleep(0.05)
        chunk = stream.snapshot(0.05)
        if chunk is None:
            continue
        rms = float(np.sqrt((chunk.samples.astype("float64") ** 2).mean()))
        now = chunk.end_time
        history.append((now, rms))
        history = [item for item in history if now - item[0] < 1.0]

        if time.perf_counter() - began < skip:
            continue
        if rms > threshold:
            streak.append(now)
            if len(streak) >= 3:
                start = streak[0]
                prior = [value for when, value in history if when < start - 0.02]
                if prior and max(prior) < threshold * 0.8:
                    logger(f"[音频] 检测到音乐起点 ≈{start:.3f}s（之前 1s 都是静音）")
                    return start
        else:
            streak = []
    raise RuntimeError("没检测到音乐起点；确认手机在放声音、媒体音量不是 0")


def _audio_sync_t0(stream, song, rate, window: float, *, logger=print, timeout: float = 30.0) -> float:
    """等实时音频和参考曲目匹配上，返回「歌曲 0 秒」的 perf_counter 时刻。"""
    from .audio import estimate_position

    deadline = time.perf_counter() + timeout
    best = 0.0
    while time.perf_counter() < deadline:
        time.sleep(0.4)
        chunk = stream.snapshot(window)
        if chunk is None:
            continue
        result = estimate_position(song, rate, chunk.samples, stream.rate)
        best = max(best, result["score"])
        if result["score"] >= 0.45:
            t0 = chunk.end_time - result["position"]
            logger(
                f"[音频] 匹配到歌曲第 {result['position']:6.2f}s（得分 {result['score']:.2f}），"
                f"误差应在 ±20ms 内"
            )
            return t0
        logger(f"[音频] 还在匹配… 当前得分 {result['score']:.2f}")
    raise RuntimeError(f"音频匹配失败（最好得分 {best:.2f}）；确认 --audio-ref 是同一首歌，且手机在放声音")


def _start_with_audio(args: argparse.Namespace, backend, stream, song, rate, *, logger=print) -> float:
    for remaining in range(args.countdown, 0, -1):
        logger(f"{remaining}...", flush=True)
        time.sleep(1.0)

    if args.start == "tap":
        if not args.start_tap:
            raise ValueError("--start tap 需要 --start-tap X,Y")
        x, y = _parse_start_tap(args.start_tap)
        logger(f"点击开始按钮 ({x}, {y})")
        backend.press(9, Point(x, y))
        time.sleep(0.06)
        backend.release(9)
    elif args.start == "enter":
        logger("在手机上点「开始」，然后按回车（音频会自动对齐，不用卡时机）")
        _wait_enter(logger)

    if args.audio_sync == "onset":
        return _audio_onset_t0(stream, logger=logger)
    return _audio_sync_t0(stream, song, rate, args.audio_window, logger=logger)


def _cmd_tracks_audio(args: argparse.Namespace) -> int:
    from .tracks import extract_audio

    path = extract_audio(args.aa_dir, args.song, args.out)
    print(f"参考音频 -> {path}")
    return 0


def _cmd_keys(args: argparse.Namespace) -> int:
    """诊断：把 10 秒内收到的按键原样打出来，确认终端是否把方向键传给程序。"""
    if os.name != "nt":
        print("这个诊断只对 Windows 有效")
        return 2
    import msvcrt

    print(f"stdin.isatty() = {sys.stdin.isatty()}")
    print(f"接下来 {args.seconds} 秒内请按 ↑ ↓ + - q，看是否打印出来…")
    end = time.time() + args.seconds
    seen = 0
    while time.time() < end:
        if msvcrt.kbhit():
            key = msvcrt.getwch()
            seen += 1
            print(f"  收到 {key!r}")
            if key in ("\x00", "\xe0"):
                print(f"   扩展码 {msvcrt.getwch()!r}（H=↑ P=↓）")
        time.sleep(0.02)
    print(f"共收到 {seen} 个按键。" + ("" if seen else " —— 说明终端没把按键交给程序"))
    return 0


def _cmd_identify(args: argparse.Namespace) -> int:
    from .recognize import identify

    try:
        result = identify(args.screen, args.ocr, args.charts, min_score=args.min_score)
    except Exception as exc:  # noqa: BLE001
        print(f"[识别失败] {exc}")
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


def _cmd_shot(args: argparse.Namespace) -> int:
    from .scrcpy import find_adb

    adb = find_adb(args.adb)
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as handle:
        subprocess.run([adb, "exec-out", "screencap", "-p"], stdout=handle, check=False)
    size = path.stat().st_size if path.exists() else 0
    if size < 1000:
        print(f"截图失败（只有 {size} 字节），检查设备是否解锁/授权")
        return 2
    print(f"截图 -> {path}（{path.parent} 下可以直接打开看）")
    return 0


def _cmd_tracks_list(args: argparse.Namespace) -> int:
    from .tracks import load_catalog

    charts = load_catalog(args.aa_dir)
    songs = {c.song for c in charts}
    print(f"共 {len(charts)} 份谱面，{len(songs)} 首歌")
    for chart in charts[: args.limit or None]:
        print(f"  {chart.song:36} {chart.artist:24} {chart.difficulty:7} {chart.bundle.name}")
    return 0


def _cmd_tracks_extract(args: argparse.Namespace) -> int:
    from .tracks import extract_charts, load_catalog

    charts = load_catalog(args.aa_dir)
    if args.limit:
        charts = charts[: args.limit]
    print(f"准备提取 {len(charts)} 份谱面 -> {args.out}")
    manifest = extract_charts(charts, args.out)
    total_notes = sum(item["notes"] for item in manifest)
    print(f"完成：{len(manifest)} 份，合计 {total_notes} 个音符")
    print(f"清单 -> {Path(args.out) / 'manifest.json'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="phigros_auto",
        description="Phigros 自动打歌核心：谱面解析 / 触控时间轴 / 音频对齐 / 预览",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("info", help="查看谱面统计")
    p.add_argument("chart")
    p.set_defaults(func=_cmd_info)

    p = sub.add_parser("plan", help="生成触控时间轴")
    p.add_argument("chart")
    _add_calib_opts(p, 1080, 2400)
    p.add_argument("--out", help="输出 JSON 路径")
    p.add_argument("--csv", help="输出 CSV 路径")
    p.set_defaults(func=_cmd_plan)

    p = sub.add_parser("preview", help="渲染谱面预览图 / GIF")
    p.add_argument("chart")
    _add_calib_opts(p, 960, 540)
    p.add_argument("--out", default="out", help="输出目录")
    p.add_argument("--times", help="逗号分隔的截图时刻（秒）")
    p.add_argument("--gif", action="store_true", help="额外输出 GIF")
    p.add_argument("--start", type=float, default=0.0, help="GIF 起始秒")
    p.add_argument("--end", type=float, default=None, help="GIF 结束秒")
    p.add_argument("--fps", type=int, default=12, help="GIF 帧率")
    p.add_argument("--scale", type=float, default=0.55, help="GIF 缩放")
    p.add_argument("--no-fingers", action="store_true", help="不画按下的手指")
    p.set_defaults(func=_cmd_preview)

    p = sub.add_parser("play", help="实时下发（dryrun / adb / scrcpy）")
    p.add_argument("chart")
    _add_calib_opts(p, 0, 0)
    p.add_argument("--backend", default="dryrun", choices=["dryrun", "adb", "scrcpy"])
    _add_scrcpy_opts(p)
    p.add_argument("--log", help="dryrun 事件日志路径")
    p.add_argument("--countdown", type=int, default=3, help="开始前倒计时秒数")
    p.add_argument(
        "--start",
        default="manual",
        choices=["manual", "enter", "tap", "immediate"],
        help="怎么开始：manual=点开始后、第一个音符到判定线时按回车/空格（推荐）；enter=音乐响起时按键；tap=点开始+固定延时；immediate=立刻",
    )
    p.add_argument("--start-tap", help="--start tap 模式下「开始」按钮的坐标，如 2050,950")
    p.add_argument(
        "--start-delay",
        type=float,
        default=None,
        help="触发后再等多少秒才是歌曲 0 秒（tap 模式默认 1.5，其它默认 0）",
    )
    p.add_argument(
        "--audio-sync",
        choices=["onset", "ref"],
        default=None,
        help="用 scrcpy 音频自动对齐起点：onset=检测音乐响起（推荐），ref=和参考曲目做互相关",
    )
    p.add_argument("--audio-ref", help="参考音频（wav），用 tracks audio 导出")
    p.add_argument("--audio-window", type=float, default=5.0, help="做互相关的窗口秒数（默认 5s）")
    p.add_argument("--speed", type=float, default=1.0, help="整体倍速（仅调试，正式打歌别动）")
    p.add_argument(
        "--nudge",
        dest="nudge",
        action="store_true",
        default=True,
        help="播放中按 ↑/↓ 实时微调（默认开启）",
    )
    p.add_argument(
        "--no-nudge",
        dest="nudge",
        action="store_false",
        help="关闭方向键微调",
    )
    p.add_argument("--delay", type=float, default=None, help="初始微调量（秒）；不填则用 calibration.json 里记住的值")
    p.add_argument("--no-save", action="store_true", help="结束后不把微调结果写进 calibration.json")
    p.add_argument("--nudge-line", action="store_true", help="用「输入数字+回车」的方式微调（方向键没反应时用）")
    p.add_argument("--fast", action="store_true", help="不等待，直接把事件刷给后端")
    p.set_defaults(func=_cmd_play)

    p = sub.add_parser("scrcpy-test", help="测试 scrcpy 注入通道（点 5 个点）")
    _add_scrcpy_opts(p, show_touches_default=True)
    p.add_argument("--countdown", type=int, default=3, help="开始前倒计时秒数")
    p.set_defaults(func=_cmd_scrcpy_test)

    p = sub.add_parser("sync", help="用音频对齐求偏移")
    p.add_argument("song", help="歌曲原文件（wav）")
    p.add_argument("recording", help="手机/麦克风录音（wav）")
    p.add_argument("--max-offset", type=float, default=15.0, help="最大偏移搜索范围（秒）")
    p.add_argument("--probe", type=float, default=5.0, help="用于打印校验的录音时刻（秒）")
    p.set_defaults(func=_cmd_sync)

    p = sub.add_parser("keys", help="诊断：终端是否把方向键传给程序")
    p.add_argument("--seconds", type=float, default=10.0)
    p.set_defaults(func=_cmd_keys)

    p = sub.add_parser("shot", help="截一张手机屏幕，用来找按钮坐标")
    p.add_argument("--out", default="out/screen.png")
    p.add_argument("--adb", default="adb")
    p.set_defaults(func=_cmd_shot)

    p = sub.add_parser("identify", help="识别截图里当前选中的曲目和难度")
    p.add_argument("screen", help="选歌界面截图（png）")
    p.add_argument("--ocr", required=True, help="OCR 结果 JSON（releases/scripts/ocr.ps1 生成）")
    p.add_argument("--charts", default="charts", help="谱面目录")
    p.add_argument("--min-score", type=float, default=0.75, help="匹配阈值")
    p.set_defaults(func=_cmd_identify)

    p = sub.add_parser("tracks", help="从 Addressables 目录提取全部谱面")
    tracks_sub = p.add_subparsers(dest="action", required=True)

    q = tracks_sub.add_parser("list", help="列出 catalog 里的谱面")
    q.add_argument("aa_dir", help="…/assets/aa 目录（含 catalog.json）")
    q.add_argument("--limit", type=int, default=20)
    q.set_defaults(func=_cmd_tracks_list)

    q = tracks_sub.add_parser("audio", help="导出某首歌的参考音频（wav）")
    q.add_argument("aa_dir", help="…/assets/aa 目录（含 catalog.json）")
    q.add_argument("--song", required=True, help="歌名，例如 Glaciaxion")
    q.add_argument("--out", required=True, help="输出 wav 路径")
    q.set_defaults(func=_cmd_tracks_audio)

    q = tracks_sub.add_parser("extract", help="导出谱面 JSON")
    q.add_argument("aa_dir", help="…/assets/aa 目录（含 catalog.json）")
    q.add_argument("--out", default="charts", help="输出目录")
    q.add_argument("--limit", type=int, default=0, help="只处理前 N 份（调试用）")
    q.set_defaults(func=_cmd_tracks_extract)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ChartError as exc:
        print(f"谱面错误：{exc}", file=sys.stderr)
        return 2
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
