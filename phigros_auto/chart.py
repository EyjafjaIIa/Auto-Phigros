"""Phigros 谱面（RPE / formatVersion 3）解析。

内部统一使用「秒」。判定线坐标系保持谱面原样：

* ``position_x``：0.5 是判定线中点，0 和 1 是线的左右两端（可以越界）；
* ``position.y``：归一化到屏幕，0.5 是屏幕中心，0 是顶部；
* ``rotation``：角度（度），正值表示顺时针。

判定线的坐标最终怎么映射到像素由 :mod:`phigros_auto.schedule` 里的
``Calibration`` 决定，这里只负责把谱面读成结构化数据。
"""

from __future__ import annotations

import bisect
import json
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any, Iterable

from .timing import beat_to_sec, beat_value, ease

TAP, DRAG, HOLD, FLICK = 1, 2, 3, 4

KIND_NAMES = {TAP: "tap", DRAG: "drag", HOLD: "hold", FLICK: "flick"}


class ChartError(ValueError):
    """谱面结构有问题。"""


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Note:
    kind: int
    time: float
    position_x: float
    hold: float = 0.0
    speed: float = 1.0
    floor: float | None = None
    above: bool = True

    @property
    def kind_name(self) -> str:
        return KIND_NAMES.get(self.kind, f"unknown({self.kind})")

    @property
    def end_time(self) -> float:
        return self.time + self.hold


def _lerp(a: float, b: float, p: float) -> float:
    return a + (b - a) * p


def _progress(t: float, t0: float, t1: float) -> float:
    if t1 <= t0:
        return 1.0 if t >= t1 else 0.0
    p = (t - t0) / (t1 - t0)
    return 0.0 if p < 0.0 else 1.0 if p > 1.0 else p


@dataclass(frozen=True)
class MoveEvent:
    t0: float
    t1: float
    x0: float
    y0: float
    x1: float
    y1: float
    easing: int = 1

    def sample(self, t: float) -> tuple[float, float]:
        p = ease(_progress(t, self.t0, self.t1), self.easing)
        return _lerp(self.x0, self.x1, p), _lerp(self.y0, self.y1, p)


@dataclass(frozen=True)
class RotateEvent:
    t0: float
    t1: float
    a0: float
    a1: float
    easing: int = 1

    def sample(self, t: float) -> float:
        return _lerp(self.a0, self.a1, ease(_progress(t, self.t0, self.t1), self.easing))


@dataclass(frozen=True)
class AlphaEvent:
    t0: float
    t1: float
    a0: float
    a1: float
    easing: int = 1

    def sample(self, t: float) -> float:
        return _lerp(self.a0, self.a1, ease(_progress(t, self.t0, self.t1), self.easing))


@dataclass(frozen=True)
class SpeedEvent:
    t0: float
    t1: float
    v0: float
    v1: float
    easing: int = 1

    def sample(self, t: float) -> float:
        return _lerp(self.v0, self.v1, ease(_progress(t, self.t0, self.t1), self.easing))


Event = MoveEvent | RotateEvent | AlphaEvent | SpeedEvent


def _sample(events: list[Event], t: float, default: Any) -> Any:
    """在按 ``t0`` 排好序的事件表里取样；早于第一个事件就取它的起点值。"""
    if not events:
        return default
    lo, hi = 0, len(events)
    while lo < hi:
        mid = (lo + hi) // 2
        if events[mid].t0 <= t:
            lo = mid + 1
        else:
            hi = mid
    if lo == 0:
        return events[0].sample(t)
    return events[lo - 1].sample(t)


@dataclass
class JudgeLine:
    index: int
    bpm: float
    moves: list[MoveEvent] = field(default_factory=list)
    rotates: list[RotateEvent] = field(default_factory=list)
    alphas: list[AlphaEvent] = field(default_factory=list)
    speeds: list[SpeedEvent] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)

    def position_at(self, t: float) -> tuple[float, float]:
        return _sample(self.moves, t, (0.5, 0.5))

    def rotation_at(self, t: float) -> float:
        return _sample(self.rotates, t, 0.0)

    def alpha_at(self, t: float) -> float:
        return _sample(self.alphas, t, 1.0)

    def speed_at(self, t: float) -> float:
        return _sample(self.speeds, t, 1.0)

    @cached_property
    def _floor_points(self) -> tuple[list[float], list[float]]:
        pairs = sorted((n.time, n.floor) for n in self.notes if n.floor is not None)
        return [p[0] for p in pairs], [p[1] for p in pairs]

    def floor_at(self, t: float) -> float | None:
        """判定线当前的 floor 值（由谱面自带的 ``floorPosition`` 反推）。

        音符的屏幕位置取决于 ``note.floor - line.floor(t)``，两者相减才
        有意义，所以这里用音符自带的 floor 值做线性插值即可，不需要重新
        积分 speed 事件。
        """
        times, values = self._floor_points
        if not times:
            return None
        if len(times) == 1:
            return values[0]
        i = bisect.bisect_right(times, t)
        if i <= 0:
            return values[0]
        if i >= len(times):
            return values[-1]
        t0, t1 = times[i - 1], times[i]
        if t1 <= t0:
            return values[i]
        return _lerp(values[i - 1], values[i], (t - t0) / (t1 - t0))

    @cached_property
    def floor_rate(self) -> float | None:
        """每秒推进多少 floor 单位，画 Hold 尾巴时要用。"""
        times, values = self._floor_points
        if len(times) < 2:
            return None
        span = times[-1] - times[0]
        if span <= 0:
            return None
        return (values[-1] - values[0]) / span


@dataclass
class Chart:
    path: str
    format_version: int
    offset: float
    lines: list[JudgeLine]
    warnings: list[str] = field(default_factory=list)
    tick: int = 32
    block_areas: list[dict[str, Any]] = field(default_factory=list)

    # -- 查询 ---------------------------------------------------------
    def notes(self) -> list[Note]:
        return [n for line in self.lines for n in line.notes]

    @cached_property
    def note_x_centered(self) -> bool:
        """官方谱面的 x 以 0 为中心、±7 到屏幕两侧；RPE 编辑器谱面是 0~1。"""
        return any(abs(n.position_x) > 1.5 for n in self.notes())

    @property
    def duration(self) -> float:
        return max((n.end_time for n in self.notes()), default=0.0)

    def note_count(self, kind: int | None = None) -> int:
        notes = self.notes()
        if kind is None:
            return len(notes)
        return sum(1 for n in notes if n.kind == kind)

    def stats(self) -> dict[str, Any]:
        counts = {KIND_NAMES[k]: self.note_count(k) for k in KIND_NAMES}
        return {
            "path": self.path,
            "formatVersion": self.format_version,
            "offset": round(self.offset, 4),
            "lines": len(self.lines),
            "bpm": sorted({round(line.bpm, 3) for line in self.lines}),
            "notes": counts,
            "duration": round(self.duration, 3),
            "randomAreas": len(self.block_areas),
            "warnings": self.warnings,
        }

    # -- 解析 ---------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path) -> "Chart":
        p = Path(path)
        try:
            text = p.read_text(encoding="utf-8")
        except OSError as exc:
            raise ChartError(f"读不到谱面文件 {p}：{exc}") from exc
        except UnicodeDecodeError as exc:
            raise ChartError(f"谱面不是 UTF-8 文本（{p}）：{exc}") from exc
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ChartError(f"谱面不是合法 JSON（{p} 第 {exc.lineno} 行）：{exc.msg}") from exc
        chart = cls.from_dict(raw, path=str(p))
        return chart

    @classmethod
    def from_dict(cls, raw: Any, *, path: str = "<memory>") -> "Chart":
        if not isinstance(raw, dict):
            raise ChartError("谱面根节点必须是对象")
        lines_raw = raw.get("judgeLineList")
        if not isinstance(lines_raw, list) or not lines_raw:
            raise ChartError("judgeLineList 缺失或为空")

        warnings: list[str] = []
        try:
            version = int(raw.get("formatVersion") or 3)
        except (TypeError, ValueError):
            version = 3
        try:
            offset = float(raw.get("offset") or 0.0)
        except (TypeError, ValueError):
            offset = 0.0
            warnings.append("offset 不是数字，按 0 处理")
        if abs(offset) > 2.0:
            warnings.append(f"offset={offset} 偏大，若对不上可能是毫秒单位")

        chart_bpm = 120.0
        try:
            chart_bpm = float(raw.get("bpm") or 120.0)
        except (TypeError, ValueError):
            pass

        lines: list[JudgeLine] = []
        tick = _detect_tick(raw)
        for index, line_raw in enumerate(lines_raw):
            if not isinstance(line_raw, dict):
                warnings.append(f"第 {index} 条判定线不是对象，已跳过")
                continue
            line = _parse_line(line_raw, index, chart_bpm, offset, tick, warnings)
            lines.append(line)

        if not lines:
            raise ChartError("没有任何可用的判定线")

        # Random 模式：这些区域里的音符会被游戏随机打乱位置
        block_areas_raw = raw.get("blockAreaList") or []
        block_areas: list[dict[str, Any]] = []
        if isinstance(block_areas_raw, list):
            for area in block_areas_raw:
                if isinstance(area, dict):
                    block_areas.append(area)
        if block_areas:
            warnings.append(
                f"含 {len(block_areas)} 个随机区域：游戏里若开启 Random，这些区域的音符会被打乱位置"
            )

        return cls(
            path=path,
            format_version=version,
            offset=offset,
            lines=lines,
            warnings=warnings,
            tick=tick,
            block_areas=block_areas,
        )


def _detect_tick(raw: dict[str, Any]) -> int:
    """判断 ``time`` 的单位：官方谱面是 1/32 拍，RPE 编辑器谱面是拍。

    做法是把两种解释都换算成秒，看哪个落在合理曲长里。
    """
    times: list[float] = []
    for line in raw.get("judgeLineList") or []:
        if not isinstance(line, dict):
            continue
        for key in ("notesAbove", "notesBelow"):
            for note in line.get(key) or []:
                value = note.get("time") if isinstance(note, dict) else None
                if isinstance(value, (int, float)):
                    times.append(float(value))
    if not times:
        return 1
    try:
        bpm = float((raw["judgeLineList"][0] or {}).get("bpm") or 120.0)
    except (TypeError, ValueError, AttributeError, IndexError):
        bpm = 120.0
    if bpm <= 0:
        bpm = 120.0

    longest = max(times)
    as_tick = longest / 32.0 * 60.0 / bpm
    as_beat = longest * 60.0 / bpm
    if 20.0 <= as_tick <= 900.0 and as_beat > 900.0:
        return 32
    if 20.0 <= as_beat <= 900.0:
        return 1
    return 32 if longest > 1000 else 1


# ---------------------------------------------------------------------------
# 解析辅助
# ---------------------------------------------------------------------------


def _num(obj: dict[str, Any], key: str, default: float) -> float:
    value = obj.get(key, default)
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _times(raw: dict[str, Any], bpm: float, offset: float, tick: int) -> tuple[float, float]:
    t0 = offset + beat_to_sec(beat_value(raw.get("startTime", 0.0)) / tick, bpm)
    end = raw.get("endTime")
    t1 = offset + beat_to_sec(beat_value(end) / tick, bpm) if end is not None else t0
    return t0, t1


def _parse_line(
    raw: dict[str, Any],
    index: int,
    chart_bpm: float,
    offset: float,
    tick: int,
    warnings: list[str],
) -> JudgeLine:
    bpm = _num(raw, "bpm", chart_bpm)
    if bpm <= 0:
        bpm = chart_bpm if chart_bpm > 0 else 120.0
        warnings.append(f"第 {index} 条判定线的 bpm 非法，回退到 {bpm}")

    line = JudgeLine(index=index, bpm=bpm)

    for item in raw.get("judgeLineMoveEvents") or []:
        if not isinstance(item, dict):
            continue
        try:
            t0, t1 = _times(item, bpm, offset, tick)
        except TypeError:
            continue
        line.moves.append(
            MoveEvent(
                t0,
                t1,
                _num(item, "start", 0.5),
                _num(item, "start2", 0.5),
                _num(item, "end", 0.5),
                _num(item, "end2", 0.5),
                int(_num(item, "easingType", 1)),
            )
        )

    for item in raw.get("judgeLineRotateEvents") or []:
        if not isinstance(item, dict):
            continue
        try:
            t0, t1 = _times(item, bpm, offset, tick)
        except TypeError:
            continue
        line.rotates.append(
            RotateEvent(
                t0,
                t1,
                _num(item, "start", 0.0),
                _num(item, "end", 0.0),
                int(_num(item, "easingType", 1)),
            )
        )

    for item in raw.get("judgeLineDisappearEvents") or []:
        if not isinstance(item, dict):
            continue
        try:
            t0, t1 = _times(item, bpm, offset, tick)
        except TypeError:
            continue
        line.alphas.append(
            AlphaEvent(
                t0,
                t1,
                _num(item, "start", 1.0),
                _num(item, "end", 1.0),
                int(_num(item, "easingType", 1)),
            )
        )

    for item in raw.get("speedEvents") or []:
        if not isinstance(item, dict):
            continue
        try:
            t0, t1 = _times(item, bpm, offset, tick)
        except TypeError:
            continue
        line.speeds.append(
            SpeedEvent(
                t0,
                t1,
                _num(item, "start", 1.0),
                _num(item, "end", 1.0),
                int(_num(item, "easingType", 1)),
            )
        )

    line.moves.sort(key=lambda e: e.t0)
    line.rotates.sort(key=lambda e: e.t0)
    line.alphas.sort(key=lambda e: e.t0)
    line.speeds.sort(key=lambda e: e.t0)

    notes: list[Note] = []
    for key, above in (("notesAbove", True), ("notesBelow", False)):
        for item in raw.get(key) or []:
            if not isinstance(item, dict):
                continue
            note = _parse_note(item, bpm, offset, tick, above, index, warnings)
            if note is not None:
                notes.append(note)
    notes.sort(key=lambda n: (n.time, n.position_x))
    line.notes = notes
    return line


def _parse_note(
    item: dict[str, Any],
    bpm: float,
    offset: float,
    tick: int,
    above: bool,
    line_index: int,
    warnings: list[str],
) -> Note | None:
    try:
        raw_type = int(item.get("type", 1))
    except (TypeError, ValueError):
        raw_type = 1
    if raw_type not in KIND_NAMES:
        warnings.append(f"第 {line_index} 条线出现未知音符类型 {raw_type}，已按 Tap 处理")
        raw_type = TAP

    try:
        beat = beat_value(item.get("time", 0.0))
    except TypeError:
        warnings.append(f"第 {line_index} 条线有音符的 time 无法解析，已跳过")
        return None

    hold = 0.0
    if raw_type == HOLD:
        try:
            hold = max(0.0, beat_to_sec(beat_value(item.get("holdTime", 0.0)) / tick, bpm))
        except TypeError:
            hold = 0.0
            warnings.append(f"第 {line_index} 条线的 Hold 音符缺少 holdTime")

    floor = None
    if item.get("floorPosition") is not None:
        try:
            floor = float(item["floorPosition"])
        except (TypeError, ValueError):
            floor = None

    x = _num(item, "positionX", 0.5)
    if abs(x) > 50.0:
        warnings.append(f"第 {line_index} 条线的音符 positionX={x} 超出常见范围")

    return Note(
        kind=raw_type,
        time=offset + beat_to_sec(beat / tick, bpm),
        position_x=x,
        hold=hold,
        speed=max(0.01, _num(item, "speed", 1.0)),
        floor=floor,
        above=above,
    )


def summarize_touches(plan_notes: Iterable[Note]) -> str:
    """给 CLI 用的一行人话摘要。"""
    counts: dict[str, int] = {}
    for n in plan_notes:
        counts[n.kind_name] = counts.get(n.kind_name, 0) + 1
    return "、".join(f"{k}×{v}" for k, v in sorted(counts.items()))
