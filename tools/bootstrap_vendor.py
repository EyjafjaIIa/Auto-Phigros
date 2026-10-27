"""把 UnityPy 及其依赖装进 out/vendor（只用于本机验证）。

沙箱里 pip 的临时目录不可写，所以直接按 PyPI 的 JSON 接口找 wheel、下载、解压。
正常环境下你用 ``pip install UnityPy`` 就行，不需要这个脚本。
"""

from __future__ import annotations

import json
import sys
import urllib.request
import zipfile
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "out" / "vendor"
PY_TAG = f"cp{sys.version_info.major}{sys.version_info.minor}"

WANTED = [
    "unitypy",
    "lz4",
    "brotli",
    "attrs",
    "fsspec",
    "tpk_ar",
    "texture2ddecoder",
    "etcpak",
    "archspec",
]


def get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=40) as resp:
        return json.load(resp)


def pick(urls: list[dict]) -> dict | None:
    for f in urls:
        name = f["filename"]
        if not name.endswith(".whl"):
            continue
        if name.endswith("-none-any.whl"):
            return f
        if "win_amd64" in name and (PY_TAG in name or "abi3" in name):
            return f
    return None


def install(pkg: str) -> bool:
    info = get_json(f"https://pypi.org/pypi/{pkg}/json")
    wheel = pick(info["urls"])
    if wheel is None:
        print(f"[skip] {pkg}: 没有适配 {PY_TAG} win_amd64 的 wheel")
        return False
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / wheel["filename"]
    if not target.exists():
        with urllib.request.urlopen(wheel["url"], timeout=180) as resp:
            target.write_bytes(resp.read())
    with zipfile.ZipFile(target) as zf:
        zf.extractall(OUT)
    print(f"[ok]   {pkg} {info['info']['version']} <- {wheel['filename']} ({target.stat().st_size // 1024} KB)")
    return True


def main() -> int:
    print(f"python {sys.version.split()[0]} -> {PY_TAG}, vendor = {OUT}")
    failed = [p for p in WANTED if not install(p)]
    print("失败:", failed or "无")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
