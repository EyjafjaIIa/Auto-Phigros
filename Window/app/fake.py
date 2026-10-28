"""假课题：把谱面镜像/乱序/偏移，生成一份新谱面。"""

from __future__ import annotations

import json
import random
from pathlib import Path

MODES = {
    "Mirror": "镜像",
    "Shuffle": "乱序",
    "Shift": "偏移",
}


def _notes(data: dict):
    for line in data.get("judgeLineList", []):
        for key in ("notesAbove", "notesBelow"):
            for note in line.get(key) or []:
                yield note


def make_fake(chart_path: str | Path, out_dir: str | Path, mode: str = "Mirror", seed: int = 520) -> Path:
    source = Path(chart_path)
    data = json.loads(source.read_text(encoding="utf-8"))
    notes = list(_notes(data))
    if not notes:
        raise RuntimeError("这份谱面没有音符")

    centered = any(abs(note.get("positionX", 0)) > 1.5 for note in notes)

    if mode == "Mirror":
        for note in notes:
            x = float(note.get("positionX", 0))
            note["positionX"] = -x if centered else (1.0 - x)
    elif mode == "Shuffle":
        values = [float(note.get("positionX", 0)) for note in notes]
        random.Random(seed).shuffle(values)
        for note, value in zip(notes, values):
            note["positionX"] = value
    elif mode == "Shift":
        for note in notes:
            note["positionX"] = float(note.get("positionX", 0)) + (0.6 if centered else 0.08)
    else:
        raise ValueError(f"未知模式 {mode}")

    parts = source.stem.rsplit(".", 2)
    song = parts[0] if parts else source.stem
    artist = parts[1] if len(parts) > 1 else "fake"
    diff = parts[2] if len(parts) > 2 else "IN"
    target = Path(out_dir) / f"{song}_{mode}.{artist}.{diff}.json"
    target.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return target
