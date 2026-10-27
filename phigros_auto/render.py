"""离线预览：把谱面渲染成图片，用来检查判定线、音符位置与时间轴。

这不是游戏本体，只是一张「我算出来的谱面长这样」的示意图：
* 判定线按位移/旋转事件动；
* 音符按 floorPosition 的差值做透视投影，靠近判定线时变大；
* 有触控计划时，会把按下的手指画出来。
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Iterable, Sequence

from PIL import Image, ImageDraw, ImageFont

from .chart import DRAG, FLICK, HOLD, TAP, Chart, JudgeLine, Note
from .schedule import FLOOR_PER_SEC, Calibration, Plan, ScreenMapper, resolve_note_x_centered

BG_TOP = (6, 8, 17)
BG_BOTTOM = (17, 25, 46)
LINE_GLOW = (96, 200, 255)
LINE_CORE = (176, 240, 255)
TEXT = (214, 232, 255)
DIM = (126, 150, 188)
NOTE_COLORS = {
    TAP: (236, 246, 255),
    DRAG: (255, 224, 100),
    FLICK: (255, 116, 180),
    HOLD: (128, 226, 255),
}

_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\arial.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
)


def _font(size: int) -> ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def _gradient(width: int, height: int) -> Image.Image:
    strip = Image.new("RGB", (1, height))
    pixels = strip.load()
    for y in range(height):
        p = y / max(1, height - 1)
        pixels[0, y] = tuple(
            int(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * p) for i in range(3)
        )
    return strip.resize((width, height))


def _dz(line: JudgeLine, note: Note, t: float) -> float:
    floor = line.floor_at(t)
    if floor is not None and note.floor is not None:
        return note.floor - floor
    return (note.time - t) * (line.floor_rate or FLOOR_PER_SEC)


def _screen_note(
    mapper: ScreenMapper,
    calib: Calibration,
    line: JudgeLine,
    note: Note,
    t: float,
    dz: float,
) -> tuple[float, float, float]:
    """返回 (x, y, 透视缩放 0~1)。"""
    p = 1.0 / (1.0 + max(dz, 0.0) * 0.5)
    dist = min(calib.height * 0.55, (1.0 - p) * calib.height * 0.46)
    base_x, base_y = mapper.raw_point(line, note, t)
    nx, ny = mapper.normal(line, t, note.above)
    return base_x + nx * dist, base_y + ny * dist, p


def render_frame(
    chart: Chart,
    t: float,
    calib: Calibration | None = None,
    plan: Plan | None = None,
    *,
    size: tuple[int, int] | None = None,
) -> Image.Image:
    calib = calib or Calibration()
    mapper = ScreenMapper(calib, note_x_centered=resolve_note_x_centered(calib, chart))
    width, height = calib.width, calib.height

    base = _gradient(width, height).convert("RGBA")
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    # 背景网格
    for y in range(0, height, 60):
        draw.line([(0, y), (width, y)], fill=(120, 170, 255, 12), width=1)

    # 判定线
    for line in chart.lines:
        alpha = max(0.0, min(1.0, line.alpha_at(t)))
        if alpha < 0.02:
            continue
        rot = math.radians(line.rotation_at(t)) * calib.rotate_sign
        cx, cy = mapper.line_center(line, t)
        half = calib.span * width * 0.5
        dx, dy = math.cos(rot) * half, math.sin(rot) * half
        draw.line([(cx - dx, cy - dy), (cx + dx, cy + dy)], fill=(120, 210, 255, int(26 * alpha)), width=11)
        draw.line([(cx - dx, cy - dy), (cx + dx, cy + dy)], fill=(*LINE_CORE, int(230 * alpha)), width=3)

    # 音符
    for line in chart.lines:
        for note in line.notes:
            dz = _dz(line, note, t)
            if dz < -0.8 or dz > 16.0:
                continue
            x, y, p = _screen_note(mapper, calib, line, note, t, dz)
            if x < -200 or x > width + 200 or y < -200 or y > height + 200:
                continue
            color = NOTE_COLORS.get(note.kind, (255, 255, 255))
            radius = 6.0 + 17.0 * p

            if note.kind == HOLD and note.hold > 0:
                tail_dz = dz + note.hold * (line.floor_rate or FLOOR_PER_SEC)
                tx, ty, _ = _screen_note(mapper, calib, line, note, t, tail_dz)
                draw.line([(tx, ty), (x, y)], fill=(*color, 110), width=max(2, int(radius * 0.9)))
                draw.ellipse(
                    [tx - radius * 0.55, ty - radius * 0.55, tx + radius * 0.55, ty + radius * 0.55],
                    fill=(*color, 120),
                )

            if note.kind == FLICK:
                nx, ny = mapper.normal(line, t, note.above)
                tx, ty = -ny, nx
                tip = (x + nx * radius * 1.9, y + ny * radius * 1.9)
                b1 = (x + tx * radius * 1.1, y + ty * radius * 1.1)
                b2 = (x - tx * radius * 1.1, y - ty * radius * 1.1)
                draw.polygon([tip, b1, b2], fill=(*color, 235))
            elif note.kind == DRAG:
                draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=(*color, 90))
                draw.ellipse(
                    [x - radius * 0.55, y - radius * 0.55, x + radius * 0.55, y + radius * 0.55],
                    fill=(*color, 245),
                )
            else:
                draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=(*color, 235))
                draw.ellipse(
                    [x - radius * 0.4, y - radius * 0.4, x + radius * 0.4, y + radius * 0.4],
                    fill=(30, 40, 60, 235),
                )

    # 按下的手指
    if plan is not None:
        for touch in plan.touches:
            if not (touch.down_t <= t <= touch.up_t):
                continue
            age = max(0.0, t - touch.down_t)
            spread = 14 + 90 * min(1.0, age / 0.18)
            fade = int(200 * max(0.0, 1.0 - age / 0.18))
            px, py = touch.down.x, touch.down.y
            draw.ellipse(
                [px - spread, py - spread, px + spread, py + spread],
                outline=(255, 255, 255, fade),
                width=2,
            )
            draw.ellipse([px - 7, py - 7, px + 7, py + 7], fill=(255, 255, 255, 230))

    image = Image.alpha_composite(base, layer)
    draw = ImageDraw.Draw(image)

    # HUD
    title_font = _font(20)
    small = _font(16)
    big = _font(30)

    draw.text((24, 20), "PHIGROS AUTO", font=title_font, fill=TEXT)
    draw.text((24, 46), "谱面预览 / AUTO PREVIEW", font=small, fill=DIM)
    draw.text((width - 24, 20), f"{t:6.2f}s", font=title_font, fill=TEXT, anchor="ra")
    draw.text(
        (width - 24, height - 58),
        f"BPM {chart.lines[0].bpm:g}   {len(chart.notes())} notes",
        font=small,
        fill=DIM,
        anchor="ra",
    )

    combo = sum(1 for line in chart.lines for note in line.notes if note.time <= t)
    draw.text((24, height - 58), "COMBO", font=small, fill=DIM)
    draw.text((24, height - 96), f"{combo:04d}", font=big, fill=TEXT)

    # 进度条
    duration = max(chart.duration, 1e-3)
    progress = min(1.0, max(0.0, t / duration))
    bar_y = height - 22
    draw.line([(24, bar_y), (width - 24, bar_y)], fill=(70, 90, 125, 255), width=4)
    draw.line([(24, bar_y), (24 + (width - 48) * progress, bar_y)], fill=(*LINE_CORE, 255), width=4)

    if size is not None and size != (width, height):
        image = image.resize(size, Image.LANCZOS)
    return image.convert("RGB")


def default_times(chart: Chart, count: int = 4) -> list[float]:
    duration = max(chart.duration, 1.0)
    return [round(duration * (i + 1) / (count + 1), 2) for i in range(count)]


def render_contact_sheet(
    chart: Chart,
    calib: Calibration,
    plan: Plan | None,
    times: Sequence[float],
    out_path: str | Path,
    *,
    cols: int = 2,
    frame_size: tuple[int, int] | None = None,
    padding: int = 14,
) -> Path:
    frames = [render_frame(chart, t, calib, plan) for t in times]
    if frame_size:
        frames = [f.resize(frame_size, Image.LANCZOS) for f in frames]
    fw, fh = frames[0].size
    rows = (len(frames) + cols - 1) // cols
    sheet = Image.new(
        "RGB",
        (cols * fw + padding * (cols + 1), rows * fh + padding * (rows + 1)),
        (3, 4, 9),
    )
    for i, frame in enumerate(frames):
        r, c = divmod(i, cols)
        sheet.paste(frame, (padding + c * (fw + padding), padding + r * (fh + padding)))
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path)
    return path


def render_gif(
    chart: Chart,
    calib: Calibration,
    plan: Plan | None,
    out_path: str | Path,
    *,
    start: float = 0.0,
    end: float | None = None,
    fps: int = 12,
    scale: float = 0.55,
) -> Path:
    end = end if end is not None else min(chart.duration + 0.8, start + 8.0)
    count = max(2, int(round((end - start) * fps)))
    frames: list[Image.Image] = []
    for i in range(count):
        t = start + i / fps
        frame = render_frame(chart, t, calib, plan)
        if scale != 1.0:
            frame = frame.resize(
                (max(2, int(frame.width * scale)), max(2, int(frame.height * scale))),
                Image.LANCZOS,
            )
        frames.append(frame.convert("P", palette=Image.ADAPTIVE, colors=96))
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        path,
        save_all=True,
        append_images=frames[1:],
        duration=int(1000 / fps),
        loop=0,
        optimize=True,
    )
    return path
