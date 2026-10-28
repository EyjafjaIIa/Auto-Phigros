"""按谱面记住校准结果。

每首曲子存两项（都可缺省）：

* ``shift``：手动同步（在第一个音符处按键）后的残余偏移；
* ``delay``：**点「开始」按钮到歌曲第 0 秒**之间的秒数。

第一次跑用手动同步 + 微调，跑完把 ``delay = t0 + shift - 点击时刻`` 记下来，
之后就直接用 ``delay`` 开打，不必再按键。

存储位置：仓库根的 ``calibration.json``。旧格式（一个数字）按 ``shift`` 兼容。
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "calibration.json"


def chart_key(chart_path: str | Path) -> str:
    return Path(chart_path).stem


def _normalise(value) -> dict[str, float | None]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return {"shift": float(value), "delay": None}
    if isinstance(value, dict):
        shift = value.get("shift")
        delay = value.get("delay")
        return {
            "shift": float(shift) if isinstance(shift, (int, float)) else None,
            "delay": float(delay) if isinstance(delay, (int, float)) else None,
        }
    return {"shift": None, "delay": None}


def load_calibration() -> dict[str, dict[str, float | None]]:
    try:
        raw = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): _normalise(v) for k, v in raw.items()}


def save_calibration(data: dict[str, dict[str, float | None]]) -> None:
    out: dict[str, dict[str, float]] = {}
    for key, entry in sorted(data.items()):
        item: dict[str, float] = {}
        if entry.get("shift") is not None:
            item["shift"] = round(float(entry["shift"]), 3)
        if entry.get("delay") is not None:
            item["delay"] = round(float(entry["delay"]), 2)
        if item:
            out[key] = item
    PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")


def get_shift(chart_path: str | Path) -> float:
    value = load_calibration().get(chart_key(chart_path), {}).get("shift")
    return float(value) if value is not None else 0.0


def get_delay(chart_path: str | Path) -> float | None:
    value = load_calibration().get(chart_key(chart_path), {}).get("delay")
    return float(value) if value is not None else None


def _update(chart_path: str | Path, **changes: float | None) -> dict[str, float | None]:
    data = load_calibration()
    key = chart_key(chart_path)
    entry = data.get(key, {"shift": None, "delay": None})
    entry.update(changes)
    data[key] = entry
    save_calibration(data)
    return entry


def remember_shift(chart_path: str | Path, shift: float) -> float:
    _update(chart_path, shift=round(float(shift), 3))
    return round(float(shift), 3)


def remember_delay(chart_path: str | Path, delay: float) -> float:
    _update(chart_path, delay=round(float(delay), 2))
    return round(float(delay), 2)
