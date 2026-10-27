"""节拍换算与缓动函数。

RPE 谱面里的时间字段有两种写法：

* 数字：直接就是拍数，例如 ``8``、``8.5``；
* 三元组 ``[整数拍, 分子, 分母]``：表示 ``整数拍 + 分子/分母`` 拍，
  例如 ``[3, 1, 4]`` 表示 3.25 拍。

本模块把这些写法统一换算成拍数，再按 BPM 换算成秒。
"""

from __future__ import annotations

import math
from typing import Sequence, Union

BeatLike = Union[int, float, Sequence[float]]


def beat_value(value: BeatLike) -> float:
    """把 RPE 的时间字段换算成拍数。"""
    if isinstance(value, bool):
        raise TypeError(f"时间字段不能是布尔值: {value!r}")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, (list, tuple)):
        if not value:
            return 0.0
        if len(value) == 1:
            return float(value[0])
        whole = float(value[0])
        num = float(value[1])
        den = float(value[2]) if len(value) > 2 else 1.0
        if den == 0:
            return whole
        return whole + num / den
    raise TypeError(f"无法解析的时间字段: {value!r}")


def beat_to_sec(beats: float, bpm: float) -> float:
    """拍数 -> 秒。"""
    if bpm <= 0:
        raise ValueError("BPM 必须为正数")
    return beats * 60.0 / bpm


# --------------------------------------------------------------------------
# 缓动：RPE 的 easingType 共 29 种，这里按「族 + 进/出/进出」的规律近似实现。
# 少数 chart 会用 23~29 的 back/elastic/bounce，只影响判定线运动的观感，
# 不影响音符的打击时刻，所以近似实现足够。
# -----------------------------------------------------------------


def _sine_in(t: float) -> float:
    return 1.0 - math.cos(t * math.pi / 2.0)


def _quad_in(t: float) -> float:
    return t * t


def _cubic_in(t: float) -> float:
    return t ** 3


def _quart_in(t: float) -> float:
    return t ** 4


def _quint_in(t: float) -> float:
    return t ** 5


def _expo_in(t: float) -> float:
    return 0.0 if t <= 0 else 2.0 ** (10.0 * t - 10.0)


def _circ_in(t: float) -> float:
    return 1.0 - math.sqrt(max(0.0, 1.0 - t * t))


_BACK_C1 = 1.70158
_BACK_C3 = _BACK_C1 + 1.0


def _back_in(t: float) -> float:
    return _BACK_C3 * t ** 3 - _BACK_C1 * t * t


_ELASTIC_C4 = (2.0 * math.pi) / 3.0


def _elastic_in(t: float) -> float:
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    return -(2.0 ** (10.0 * t - 10.0)) * math.sin((10.0 * t - 10.75) * _ELASTIC_C4)


def _bounce_out(t: float) -> float:
    n1, d1 = 7.5625, 2.75
    if t < 1.0 / d1:
        return n1 * t * t
    if t < 2.0 / d1:
        t -= 1.5 / d1
        return n1 * t * t + 0.75
    if t < 2.5 / d1:
        t -= 2.25 / d1
        return n1 * t * t + 0.9375
    t -= 2.625 / d1
    return n1 * t * t + 0.984375


_FAMILIES = (
    _sine_in,
    _quad_in,
    _cubic_in,
    _quart_in,
    _quint_in,
    _expo_in,
    _circ_in,
    _back_in,
    _elastic_in,
)


def ease(t: float, mode: int = 1) -> float:
    """把 0~1 的进度按 RPE 的 easingType 变换。未知类型按线性处理。"""
    t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else float(t)
    if mode in (0, 1):
        return t
    if mode == 29:  # bounce 只在「进出」形态下提供
        return 0.5 * _bounce_out(2.0 * t) if t < 0.5 else 1.0 - 0.5 * _bounce_out(2.0 - 2.0 * t)
    if 2 <= mode <= 28:
        family = _FAMILIES[(mode - 2) // 3]
        form = (mode - 2) % 3
        if form == 0:
            return family(t)
        if form == 1:
            return 1.0 - family(1.0 - t)
        return 0.5 * family(2.0 * t) if t < 0.5 else 1.0 - 0.5 * family(2.0 - 2.0 * t)
    return t
