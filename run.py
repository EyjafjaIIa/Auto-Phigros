"""免安装启动器：``python run.py <子命令> ...``

做四件事：

1. 把仓库根塞进 ``sys.path``，任何目录下都能跑，不用 pip install；
2. 把 ``out/vendor``（以及 ``vendor``）加进 ``PYTHONPATH``，让 vendored 的
   UnityPy 等包能被导入，连子进程也带上；
3. 当前解释器缺依赖（numpy / PIL / UnityPy）时，**自动换一个能用的重跑**；
4. 出错时给出可读的提示。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENDOR_DIRS = [ROOT / "out" / "vendor", ROOT / "vendor"]

# 已知可能装了依赖的解释器（按优先级）
_CANDIDATES = [
    os.environ.get("PHIGROS_PYTHON"),
    sys.executable,
    r"C:\Users\GJS\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe",
]

_MARK = "PHIGROS_REEXEC"

_HINTS = {
    "numpy": "音频对齐需要 numpy",
    "PIL": "预览/识图需要 Pillow",
    "UnityPy": "解码谱面/音频需要 UnityPy（仓库的 out/vendor 里已带一份，需要 Python 3.12）",
}


def _vendor_path() -> str:
    parts = [str(path) for path in VENDOR_DIRS if path.is_dir()]
    current = os.environ.get("PYTHONPATH", "")
    if current:
        parts.append(current)
    return os.pathsep.join(parts)


def _has_module(exe: str, module: str) -> bool:
    env = dict(os.environ)
    path = _vendor_path()
    if path:
        env["PYTHONPATH"] = path
    try:
        result = subprocess.run(
            [exe, "-c", f"import {module}"],
            capture_output=True,
            timeout=30,
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _required_modules(argv: list[str]) -> list[str]:
    """按子命令判断需要哪些第三方包。"""
    if not argv:
        return []
    command = argv[0]
    needed: list[str] = []
    if command == "sync" or (command == "play" and "--audio-sync" in argv):
        needed.append("numpy")
    if command == "preview" or command == "identify":
        needed.append("PIL")
    if command == "tracks":
        needed.append("UnityPy")
    return needed


def _maybe_reexec(argv: list[str]) -> None:
    """缺依赖时换一个装了的解释器重跑（只切一次，避免递归）。"""
    if os.environ.get(_MARK):
        return
    needed = _required_modules(argv)
    if not needed:
        return

    # 先把 vendored 包放进来，否则 UnityPy 这种「装在仓库里」的判断会失败
    for path in VENDOR_DIRS:
        if path.is_dir() and str(path) not in sys.path:
            sys.path.insert(0, str(path))

    for module in needed:
        try:
            __import__(module)
        except ImportError:
            break
    else:
        return

    current = os.path.normcase(os.path.abspath(sys.executable))
    for exe in _CANDIDATES:
        if not exe or not Path(exe).is_file():
            continue
        if os.path.normcase(os.path.abspath(exe)) == current:
            continue
        if all(_has_module(exe, module) for module in needed):
            print(f"[run] 当前解释器缺依赖，自动切到 {exe}", file=sys.stderr)
            env = dict(os.environ)
            env[_MARK] = "1"
            path = _vendor_path()
            if path:
                env["PYTHONPATH"] = path
            raise SystemExit(
                subprocess.run([exe, str(ROOT / "run.py"), *argv], env=env, check=False).returncode
            )

    lines = ["[run] 没找到能用的 Python："]
    for module in needed:
        lines.append(f"      - {_HINTS.get(module, module)}")
        lines.append(f"        {sys.executable} -m pip install {module if module != 'PIL' else 'Pillow'}")
    lines.append("      或设置环境变量 PHIGROS_PYTHON 指向一个装好依赖的解释器。")
    print("\n".join(lines), file=sys.stderr)


def main() -> int:
    argv = sys.argv[1:]
    _maybe_reexec(argv)

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    from phigros_auto.cli import main as cli_main  # noqa: E402

    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
