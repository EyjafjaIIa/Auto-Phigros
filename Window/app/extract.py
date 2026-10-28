"""从 APK 提取谱面（桌面端点按钮时调用）。

解包 assets/aa 到缓存目录，然后用 UnityPy 把 TextAsset 里的谱面读出来。
需要 Python 3.12 + out/vendor 里的 UnityPy，所以这里自动挑解释器。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
REPO = APP_DIR.parents[2]
VENDOR = REPO / "out" / "vendor"

_CANDIDATES = [
    r"C:\Users\GJS\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe",
    sys.executable,
    shutil.which("python") or "",
]


def find_interpreter() -> str | None:
    """找一个能 import UnityPy 的解释器（vendored 包在 out/vendor）。"""
    for exe in _CANDIDATES:
        if not exe or not Path(exe).is_file():
            continue
        code = f"import sys; sys.path.insert(0, {str(VENDOR)!r}); import UnityPy; print('ok')"
        try:
            result = subprocess.run(
                [exe, "-c", code], capture_output=True, text=True, timeout=30, check=False
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0 and "ok" in result.stdout:
            return exe
    return None


def unpack_assets(apk: Path, cache: Path, log=print) -> Path:
    """把 apk 里的 assets/aa 解到缓存目录，返回 aa 目录。"""
    target_root = cache / apk.stem
    aa_dir = target_root / "assets" / "aa"
    if (aa_dir / "catalog.json").exists():
        log(f"复用已解包的资源：{aa_dir}")
        return aa_dir

    log(f"解包 {apk.name} 的 assets/aa …")
    with zipfile.ZipFile(apk) as archive:
        names = [
            name
            for name in archive.namelist()
            if name.startswith("assets/aa/") and not name.endswith("/")
        ]
        total = len(names)
        log(f"共 {total} 个文件，需要一点时间（只解一次，之后复用）")
        for index, name in enumerate(names, 1):
            target = target_root / name
            if target.exists() and target.stat().st_size > 0:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(name) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            if index % 200 == 0:
                log(f"  解包 {index}/{total}")
    return aa_dir


def run_extract(apk: Path, out_dir: Path, cache: Path, log=print) -> None:
    exe = find_interpreter()
    if exe is None:
        raise RuntimeError(
            "找不到能用的 Python 3.12（提取谱面需要 UnityPy）。\n"
            "  仓库 out/vendor 里已经带了一份，需要 Python 3.12 解释器。"
        )
    log(f"使用解释器：{exe}")
    aa_dir = unpack_assets(apk, cache, log=log)

    tool = APP_DIR / "extract_tool.py"
    env = dict(__import__("os").environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(VENDOR) + ((";" + env["PYTHONPATH"]) if env.get("PYTHONPATH") else "")
    log("开始解码谱面 …")
    process = subprocess.Popen(
        [exe, str(tool), str(aa_dir), str(out_dir)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO),
        env=env,
    )
    assert process.stdout is not None
    for line in process.stdout:
        log(line.rstrip())
    code = process.wait()
    if code != 0:
        raise RuntimeError(f"提取进程退出码 {code}")
