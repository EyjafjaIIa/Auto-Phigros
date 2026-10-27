"""把谱面变成「第几秒、按屏幕哪个点」的触控时间轴。

这一层负责三件事：

1. 判定线在某一时刻的位置 / 旋转（来自谱面事件）；
2. 音符落点 -> 屏幕像素（``Calibration`` 里的屏幕参数）；
3. 手指分配与按压/移动/抬起的时间序列（含 Hold 跟随、Flick 滑动）。

时间全部是「秒」，相对于歌曲第 0 秒。真正下发时再加一个
``offset``（对齐游戏实际播放位置）即可。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .chart import FLICK, HOLD, Chart, JudgeLine, Note

FLOOR_PER_SEC = 6.0  # speed=1 时音符每秒移动的 floor 单位，仅用于预览与兜底


def _clamp(value: float, lo: float, hi: float) -> float:
    return lo if value < lo else hi if value > hi else value


@dataclass(frozen=True)
class Point:
    x: int
    y: int

    def as_tuple(self) -> tuple[int, int]:
        return self.x, self.y


@dataclass(frozen=True)
class Calibration:
    """屏幕映射参数。真机上一旦判定线位置对不上，就调这里。"""

    width: int = 1080
    height: int = 2400
    span: float = 1.0          # 判定线长度 = 屏宽 * span
    x_shift: float = 0.0       # 额外横向平移（占屏宽比例）
    y_shift: float = 0.0       # 额外纵向平移（占屏高比例）
    rotate_sign: float = -1.0  # phisap 用 -angle；和游戏方向一致
    mirror_x: bool = False     # 左右镜像
    tap_hold: float = 0.045    # Tap/Drag 按下后保持多久（秒）
    flick_lead: float = 0.030  # Flick 提前多久按下
    flick_radius_px: float = 30.0  # Flick 滑动半径（720 高基准，phisap 用 30）
    flick_span: float = 0.030  # Flick 滑动耗时（秒，phisap 用 30）
    hold_step: float = 0.001   # Hold / Flick 跟随判定线的采样间隔（秒，phisap 是 1ms）
    max_pointers: int = 10     # 同时按下的手指上限
    note_x_mode: str = "auto"  # auto / centered（0 为中心）/ unit（0~1）
    note_unit_px: float = 72.0  # 官方谱面：1 个 x 单位 = 72px（以 720 高为基准）
    field_aspect: float = 16 / 9  # 游戏视口比例，居中不铺满

    def to_dict(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "span": self.span,
            "x_shift": self.x_shift,
            "y_shift": self.y_shift,
            "rotate_sign": self.rotate_sign,
            "mirror_x": self.mirror_x,
            "tap_hold": self.tap_hold,
            "flick_lead": self.flick_lead,
            "flick_radius_px": self.flick_radius_px,
            "flick_span": self.flick_span,
            "hold_step": self.hold_step,
            "max_pointers": self.max_pointers,
            "note_x_mode": self.note_x_mode,
            "note_unit_px": self.note_unit_px,
            "field_aspect": self.field_aspect,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Calibration":
        allowed = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in raw.items() if k in allowed})


class ScreenMapper:
    """判定线坐标 -> 屏幕像素。"""

    def __init__(self, calib: Calibration, *, note_x_centered: bool = True):
        self.calib = calib
        self.note_x_centered = note_x_centered

    def _u(self, position_x: float) -> float:
        """音符沿判定线方向的像素偏移。

        官方谱面：1 个 x 单位 = 72px（以 720 高为基准），并随分辨率等比缩放。
        这个常数来自 phisap（``off_x = note.x * 72``，内部坐标 1280x720）。
        """
        c = self.calib
        if self.note_x_centered:
            scale = c.height / 720.0
            return position_x * c.note_unit_px * scale * c.span
        # RPE 编辑器谱面：0~1，0.5 是中心
        return (position_x - 0.5) * c.span * c.width

    def field_box(self) -> tuple[float, float]:
        """游戏视口（16:9，居中）的 (左边距, 宽度)。"""
        c = self.calib
        field_w = c.height * c.field_aspect
        return (c.width - field_w) / 2.0, field_w

    def line_center(self, line: JudgeLine, t: float) -> tuple[float, float]:
        """判定线中心的屏幕像素。

        y 要翻转：谱面 y=0 在屏幕**底部**（实测 y=0.2 的线在 1080 高的屏幕上
        渲染于 y≈864）。x 要在居中的 16:9 视口里换算，不是铺满屏幕。
        """
        c = self.calib
        x_norm, y_norm = line.position_at(t)
        field_x, field_w = self.field_box()
        cx = field_x + (x_norm + c.x_shift) * field_w
        cy = (1.0 - y_norm + c.y_shift) * c.height
        return cx, cy

    def raw_point(self, line: JudgeLine, note: Note, t: float) -> tuple[float, float]:
        """不做裁剪的屏幕坐标，预览里画屏幕外的音符会用到。"""
        c = self.calib
        rot = math.radians(line.rotation_at(t)) * c.rotate_sign
        cx, cy = self.line_center(line, t)
        u = self._u(note.position_x)
        if c.mirror_x:
            u = -u
        cos_r, sin_r = math.cos(rot), math.sin(rot)
        return cx + u * cos_r, cy + u * sin_r

    def _limit_offset(self, cx: float, cy: float, u: float, cos_r: float, sin_r: float) -> float:
        """把偏移限制在屏幕内，同时保持点还在判定线上。

        官方谱面里有些 note 的 x 会超出屏幕（比如 ±9），直接裁剪会离开判定线，
        所以沿线的方向求可行区间再夹住 —— 和 phisap 的 ``recalc_pos`` 一个思路。
        """
        c = self.calib
        lo, hi = -1e18, 1e18
        for origin, direction, bound in ((cx, cos_r, c.width - 1), (cy, sin_r, c.height - 1)):
            if abs(direction) < 1e-9:
                continue
            t0 = (0.0 - origin) / direction
            t1 = (bound - origin) / direction
            lo = max(lo, min(t0, t1))
            hi = min(hi, max(t0, t1))
        if lo > hi:
            return 0.0
        return _clamp(u, lo, hi)

    def point(self, line: JudgeLine, note: Note, t: float) -> Point:
        c = self.calib
        cx, cy = self.line_center(line, t)
        rot = math.radians(line.rotation_at(t)) * c.rotate_sign
        u = self._u(note.position_x)
        if c.mirror_x:
            u = -u
        cos_r, sin_r = math.cos(rot), math.sin(rot)
        u = self._limit_offset(cx, cy, u, cos_r, sin_r)
        return Point(
            int(_clamp(round(cx + u * cos_r), 0, c.width - 1)),
            int(_clamp(round(cy + u * sin_r), 0, c.height - 1)),
        )

    def normal(self, line: JudgeLine, t: float, above: bool) -> tuple[float, float]:
        """判定线的法向（屏幕坐标）。上方音符取朝上的那一侧。"""
        rot = math.radians(line.rotation_at(t)) * self.calib.rotate_sign
        nx, ny = math.sin(rot), -math.cos(rot)
        if not above:
            nx, ny = -nx, -ny
        return nx, ny


@dataclass
class Touch:
    """一根手指从按下到抬起的一整段动作。"""

    pointer: int
    note: Note
    down_t: float
    up_t: float
    down: Point
    moves: tuple[tuple[float, Point], ...] = ()
    kind: str = "tap"

    def planned_points(self) -> list[tuple[float, Point]]:
        points = [(self.down_t, self.down)]
        points.extend(self.moves)
        return points


@dataclass
class Plan:
    touches: list[Touch]
    dropped: list[Note]
    offset: float
    chart: Chart | None = None
    calib: Calibration | None = None

    @property
    def duration(self) -> float:
        return max((t.up_t for t in self.touches), default=0.0)

    def max_fingers(self) -> int:
        events: list[tuple[float, int]] = []
        for touch in self.touches:
            events.append((touch.down_t, 1))
            events.append((touch.up_t, -1))
        events.sort(key=lambda e: (e[0], e[1]))
        cur = peak = 0
        for _, delta in events:
            cur += delta
            peak = max(peak, cur)
        return peak

    def summary(self) -> dict[str, Any]:
        kinds: dict[str, int] = {}
        for touch in self.touches:
            kinds[touch.kind] = kinds.get(touch.kind, 0) + 1
        total_notes = len(self.touches) + len(self.dropped)
        return {
            "touches": len(self.touches),
            "notes": total_notes,
            "dropped": len(self.dropped),
            "kinds": kinds,
            "duration": round(self.duration, 3),
            "offset": round(self.offset, 3),
            "max_fingers": self.max_fingers(),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary(),
            "calib": self.calib.to_dict() if self.calib else None,
            "touches": [
                {
                    "pointer": t.pointer,
                    "kind": t.kind,
                    "down_t": round(t.down_t, 4),
                    "up_t": round(t.up_t, 4),
                    "down": [t.down.x, t.down.y],
                    "moves": [[round(ts, 4), p.x, p.y] for ts, p in t.moves],
                }
                for t in self.touches
            ],
        }

    def write_json(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )


def resolve_note_x_centered(calib: Calibration, chart: Chart) -> bool:
    """决定音符 x 用哪种坐标系（见 Calibration.note_x_mode）。"""
    if calib.note_x_mode == "centered":
        return True
    if calib.note_x_mode == "unit":
        return False
    return chart.note_x_centered


def build_plan(
    chart: Chart,
    calib: Calibration | None = None,
    *,
    offset: float = 0.0,
    mapper: ScreenMapper | None = None,
) -> Plan:
    """生成完整触控时间轴。

    ``offset`` 会加到所有时间上：音频对齐算出的偏移量从这里进来。
    """
    calib = calib or Calibration()
    mapper = mapper or ScreenMapper(
        calib, note_x_centered=resolve_note_x_centered(calib, chart)
    )

    pairs = [(note, line) for line in chart.lines for note in line.notes]
    pairs.sort(key=lambda item: (item[0].time, item[0].position_x))
    touches: list[Touch] = []
    dropped: list[Note] = []
    busy: dict[int, float] = {}

    for note, line in pairs:
        base = note.time + offset

        if note.kind == FLICK:
            down_t = base - calib.flick_lead
            move_t = base
            up_t = base + calib.flick_span
        elif note.kind == HOLD and note.hold > 0:
            down_t = base
            move_t = None
            up_t = base + note.hold
        else:
            down_t = base
            move_t = None
            up_t = base + calib.tap_hold

        # 释放已经结束的手指
        for pid in [p for p, end in busy.items() if end <= down_t]:
            del busy[pid]

        free = min((p for p, end in busy.items() if end <= down_t), default=None)
        if free is not None:
            pointer = free
        elif len(busy) < calib.max_pointers:
            pointer = 0
            while pointer in busy:
                pointer += 1
        else:
            dropped.append(note)
            continue

        down_point = mapper.point(line, note, down_t)
        moves: list[tuple[float, Point]] = []

        if note.kind == HOLD and note.hold > 0:
            step = max(0.001, calib.hold_step)
            t = down_t + step
            last = down_point
            while t < up_t - 1e-6:
                p = mapper.point(line, note, t)
                if p != last:
                    moves.append((t, p))
                    last = p
                t += step
        elif note.kind == FLICK:
            # phisap 的手势：提前 30ms 从音符一侧按下，之后逐毫秒划到另一侧
            centre = mapper.point(line, note, base)
            nx, ny = mapper.normal(line, base, note.above)
            radius = calib.flick_radius_px * (calib.height / 720.0)
            start = Point(
                int(_clamp(centre.x - nx * radius, 0, calib.width - 1)),
                int(_clamp(centre.y - ny * radius, 0, calib.height - 1)),
            )
            end = Point(
                int(_clamp(centre.x + nx * radius, 0, calib.width - 1)),
                int(_clamp(centre.y + ny * radius, 0, calib.height - 1)),
            )
            down_point = start
            step = max(0.001, calib.hold_step)
            span = max(step, up_t - down_t)
            steps = max(1, int(round(span / step)))
            for i in range(1, steps + 1):
                p = i / steps
                moves.append(
                    (
                        down_t + span * p,
                        Point(
                            int(round(start.x + (end.x - start.x) * p)),
                            int(round(start.y + (end.y - start.y) * p)),
                        ),
                    )
                )

        busy[pointer] = up_t
        touches.append(
            Touch(
                pointer=pointer,
                note=note,
                down_t=down_t,
                up_t=up_t,
                down=down_point,
                moves=tuple(moves),
                kind=note.kind_name,
            )
        )

    touches.sort(key=lambda t: (t.down_t, t.pointer))
    return Plan(touches=touches, dropped=dropped, offset=offset, chart=chart, calib=calib)


def plan_to_csv(plan: Plan) -> str:
    rows = ["pointer,kind,down_t,up_t,x,y"]
    for t in plan.touches:
        rows.append(f"{t.pointer},{t.kind},{t.down_t:.4f},{t.up_t:.4f},{t.down.x},{t.down.y}")
    return "\n".join(rows) + "\n"
