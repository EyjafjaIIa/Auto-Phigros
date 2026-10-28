"""在能 import UnityPy 的解释器里执行真正的提取。"""

from __future__ import annotations

import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))

for candidate in (APP_DIR / "vendor", APP_DIR.parents[2] / "out" / "vendor"):
    if candidate.is_dir():
        sys.path.insert(0, str(candidate))

from engine.tracks import extract_charts, load_catalog  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("用法: extract_tool.py <aa_dir> <out_dir>")
        return 2
    aa_dir = Path(argv[1])
    out_dir = Path(argv[2])

    charts = load_catalog(aa_dir)
    print(f"catalog 里共 {len(charts)} 份谱面")

    # 先删掉同名旧文件，避免 extract_charts 写 .dup.json
    removed = 0
    for chart in charts:
        safe = f"{chart.song}.{chart.artist}.{chart.difficulty}.json"
        for name in (f"{chart.song}.{chart.artist}.{chart.difficulty}.json",):
            path = out_dir / name
            if path.exists():
                path.unlink()
                removed += 1
    if removed:
        print(f"清理同名旧谱面 {removed} 个")

    result = extract_charts(charts, out_dir, logger=print)
    print(f"完成：{len(result)} 份")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
