"""从「选歌界面」截图识别当前曲目与难度。

识别分两步：

1. **曲名**：由 Windows 自带 OCR（离线）给出文字行，这里拿标题区域的文字去和
   ``charts/`` 里的谱面做模糊匹配；
2. **难度**：OCR 只给文字位置，难度靠颜色判断 —— 被选中的那个难度标签有
   高饱和度的底色（EZ 绿 / HD 蓝 / IN 红 / AT 紫），未选中的是白底。

用法::

    python run.py identify out/screen.png --ocr out/ocr.json
"""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

DIFFICULTIES = ("EZ", "HD", "IN", "AT")

# 标题区域（相对坐标）：左边那块黑色高亮框
TITLE_BOX = (0.05, 0.17, 0.62, 0.50)

# 选中的难度标签底色饱和度下限
MIN_SATURATION = 45


def _norm(text: str) -> str:
    """只保留字母数字并小写，抹平 OCR 的空格/标点差异。"""
    text = text.lower()
    text = text.replace("1n", "in").replace("0", "o")
    return re.sub(r"[^a-z0-9]", "", text)


def _score(text: str, song: str) -> float:
    a, b = _norm(text), _norm(song)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ratio = SequenceMatcher(None, a, b).ratio()
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if shorter in longer:
        ratio = max(ratio, len(shorter) / len(longer))
    return ratio


def load_index(charts_dir: str | Path) -> list[dict[str, str]]:
    """从谱面文件名 ``song.artist.DIFF.json`` 建索引。"""
    entries: list[dict[str, str]] = []
    for path in sorted(Path(charts_dir).glob("*.json")):
        if path.name == "manifest.json":
            continue
        parts = path.stem.rsplit(".", 2)
        if len(parts) != 3 or parts[2] not in DIFFICULTIES:
            continue
        entries.append(
            {"song": parts[0], "artist": parts[1], "diff": parts[2], "path": str(path)}
        )
    return entries


def _saturation(pixel: tuple[int, int, int]) -> int:
    return max(pixel) - min(pixel)


def _tab_strip(image) -> tuple[int, int, tuple[int, int, int]] | None:
    """在难度标签那一条带上找饱和度最高的区域。

    返回 ``(x0, x1, 平均色)``：被选中的标签有一块高饱和底色，未选中的是白底。
    """
    width, height = image.size
    y0, y1 = int(height * 0.74), int(height * 0.85)
    x0, x1 = int(width * 0.38), int(width * 0.68)

    saturations: list[tuple[int, float]] = []
    for x in range(x0, x1, 2):
        column = [image.getpixel((x, y)) for y in range(y0, y1, 2)]
        saturations.append((x, sum(_saturation(p) for p in column) / len(column)))

    if not saturations:
        return None
    peak = max(sat for _, sat in saturations)
    if peak < 50:
        return None

    hot = [x for x, sat in saturations if sat >= peak * 0.55]
    if not hot:
        return None
    left, right = min(hot), max(hot)

    pixels = [
        image.getpixel((x, y))
        for x in range(left, max(left + 2, right) + 1, 2)
        for y in range(y0, y1, 2)
    ]
    mean = tuple(sum(p[i] for p in pixels) // len(pixels) for i in range(3))
    return left, right, mean


def _hue_name(rgb: tuple[int, int, int]) -> str | None:
    r, g, b = (v / 255.0 for v in rgb)
    high, low = max(r, g, b), min(r, g, b)
    if high - low < 0.2:
        return None
    if high == r:
        hue = (60 * ((g - b) / (high - low))) % 360
    elif high == g:
        hue = 60 * ((b - r) / (high - low)) + 120
    else:
        hue = 60 * ((r - g) / (high - low)) + 240
    if hue < 20 or hue >= 330:
        return "IN"      # 红
    if 20 <= hue < 70:
        return None      # 橙黄（没有这种难度，当作没认出来）
    if 70 <= hue < 160:
        return "EZ"      # 绿
    if 160 <= hue < 250:
        return "HD"      # 蓝
    if 250 <= hue < 330:
        return "AT"      # 紫
    return None


def detect_difficulty(lines: list[dict[str, Any]], image) -> str | None:
    """难度 = 那块高饱和底色的颜色；如果 OCR 恰好读到了标签文字，用文字复核。"""
    strip = _tab_strip(image)
    if strip is None:
        return None
    left, right, mean = strip

    # 1) OCR 读到的标签正好落在彩色区域内 → 直接用它
    for line in lines:
        label = _norm(line.get("text", ""))
        match = re.search(r"(ez|hd|in|at)$", label)
        if not match:
            continue
        center = line.get("x", 0) + line.get("w", 0) / 2
        if left - 4 <= center <= right + 4:
            return match.group(1).upper()

    # 2) 否则按底色色相判断
    return _hue_name(mean)


def identify(
    screen: str | Path,
    ocr_json: str | Path,
    charts_dir: str | Path = "charts",
    *,
    min_score: float = 0.75,
) -> dict[str, Any]:
    from PIL import Image

    lines = json.loads(Path(ocr_json).read_text(encoding="utf-8-sig"))
    if isinstance(lines, dict):
        lines = [lines]
    index = load_index(charts_dir)
    if not index:
        raise RuntimeError(f"在 {charts_dir} 里没有找到谱面，先跑「1-导入谱面」")

    image = Image.open(screen).convert("RGB")
    width, height = image.size
    x0, y0, x1, y1 = TITLE_BOX

    def inside(line: dict[str, Any]) -> bool:
        cx = line.get("x", 0) + line.get("w", 0) / 2
        cy = line.get("y", 0) + line.get("h", 0) / 2
        return x0 * width <= cx <= x1 * width and y0 * height <= cy <= y1 * height

    title_lines = sorted(
        (line for line in lines if inside(line)), key=lambda it: (it.get("y", 0), it.get("x", 0))
    )
    texts = [str(line.get("text", "")).strip() for line in title_lines]
    texts = [text for text in texts if text]

    # 曲名可能折成两行，所以单行和相邻两行拼接都试一遍
    candidates = list(texts)
    candidates += [f"{texts[i]} {texts[i + 1]}" for i in range(len(texts) - 1)]

    best: dict[str, Any] | None = None
    for text in candidates:
        for entry in index:
            score = _score(text, entry["song"])
            if best is None or score > best["score"]:
                best = {**entry, "score": score, "matched": text}

    if best is None or best["score"] < min_score:
        raise RuntimeError(
            "没能认出曲名（也可能这首不在已导入的谱面里）。"
            "把手机停在选歌界面、曲名完整可见时再试一次；"
            f"OCR 读到的标题文字：{texts!r}"
        )

    difficulty = detect_difficulty(lines, image)
    chart = Path(charts_dir) / f"{best['song']}.{best['artist']}.{best['diff']}.json"
    if difficulty:
        preferred = Path(charts_dir) / f"{best['song']}.{best['artist']}.{difficulty}.json"
        if preferred.exists():
            chart = preferred
        else:
            difficulty = None

    return {
        "song": best["song"],
        "artist": best["artist"],
        "difficulty": difficulty or "?",
        "chart": str(chart),
        "score": round(best["score"], 3),
        "matched": best["matched"],
        "title_lines": texts,
    }
