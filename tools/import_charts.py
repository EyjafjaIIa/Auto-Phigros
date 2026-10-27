"""从你自己的设备里取出 Phigros 谱面（尽最大努力）。

Phigros 是 Unity 游戏：谱面打包在 APK 的 ``assets/`` 里的 AssetBundle 中，
以 **TextAsset** 形式存在。完整链路是：

    pull（adb 拉安装包） → unpack（解出 assets/） → 解析 Unity 资源 → decode（还原 JSON）

本脚本负责 ``pull`` / ``unpack`` / ``decode``；中间那步「解析 Unity 资源」
需要 AssetStudio（GUI，一次性操作）或 UnityPy（可选）。脚本会在找不到谱面时
给出具体步骤。

用法：
    python tools/import_charts.py pull
    python tools/import_charts.py unpack
    python tools/import_charts.py scan apk/assets
    python tools/import_charts.py scan D:\\export\\TextAsset --copy
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import shutil
import subprocess
import sys
import zipfile
import zlib
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from phigros_auto.chart import Chart, ChartError

PACKAGE = "com.PigeonGames.Phigros"

# 谱面 JSON 的开头，用来做 XOR 已知明文攻击
KNOWN_HEAD = b'{"formatVersion":'
MARKER = b'"judgeLineList"'

MAX_XOR_SIZE = 8 * 1024 * 1024


# ---------------------------------------------------------------------------
# 解码
# ---------------------------------------------------------------------------


def _unwrap_json(blob: bytes) -> dict[str, Any] | None:
    """把字节当文本解析，成功且像谱面就返回 dict。"""
    for encoding in ("utf-8-sig", "utf-16-le", "utf-16-be", "gbk"):
        try:
            text = blob.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        text = text.strip("\x00 \r\n\t")
        start = text.find("{")
        if start < 0:
            continue
        try:
            obj = json.loads(text[start:])
        except json.JSONDecodeError:
            # 可能尾部有垃圾字节，退一步只保留到最后一个 }
            end = text.rfind("}")
            if end <= start:
                continue
            try:
                obj = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
        if isinstance(obj, dict) and "judgeLineList" in obj:
            return obj
    return None


def _xor(data: bytes, key: bytes) -> bytes:
    period = len(key)
    if period == 1:
        return bytes(b ^ key[0] for b in data)
    out = bytearray(len(data))
    for i in range(period):
        out[i::period] = bytes(b ^ key[i] for b in data[i::period])
    return bytes(out)


def _candidates(data: bytes) -> Iterable[tuple[str, bytes]]:
    yield "plain", data
    for name, wbits in (("gzip", 16 + zlib.MAX_WBITS), ("zlib", zlib.MAX_WBITS), ("deflate", -zlib.MAX_WBITS)):
        try:
            yield name, zlib.decompress(data, wbits)
        except zlib.error:
            pass
    # 去掉可能的长度头
    for offset in range(1, 5):
        if len(data) > offset + 16:
            yield f"skip{offset}", data[offset:]


def try_decode_chart(data: bytes, *, deep: bool = True) -> tuple[dict[str, Any], str] | None:
    """尝试还原一份谱面。返回 (谱面 dict, 用了哪种解码) 或 None。"""
    if MARKER in data:
        obj = _unwrap_json(data)
        if obj:
            return obj, "plain"

    for name, blob in _candidates(data):
        if MARKER in blob:
            obj = _unwrap_json(blob)
            if obj:
                return obj, name

    if not deep or len(data) > MAX_XOR_SIZE:
        return None

    # 单字节 XOR
    for key in range(256):
        if data[:1] and (data[0] ^ key) == ord("{"):
            decoded = _xor(data, bytes([key]))
            if MARKER in decoded:
                obj = _unwrap_json(decoded)
                if obj:
                    return obj, f"xor1(0x{key:02x})"

    # 重复密钥 XOR：用已知明文开头反推密钥，再验证
    for offset in range(0, 6):
        chunk = data[offset : offset + len(KNOWN_HEAD)]
        if len(chunk) < 8:
            break
        key = bytes(c ^ p for c, p in zip(chunk, KNOWN_HEAD))
        for period in range(1, len(key) + 1):
            if any(key[i] != key[i % period] for i in range(len(key))):
                continue
            decoded = _xor(data[offset:], key[:period])
            if MARKER in decoded:
                obj = _unwrap_json(decoded)
                if obj:
                    return obj, f"xor{period}(off={offset})"
            break
    return None


def describe_chart(raw: dict[str, Any], path: Path) -> dict[str, Any]:
    """用项目里的解析器给谱面做个摘要。"""
    try:
        chart = Chart.from_dict(raw, path=str(path))
        stats = chart.stats()
        return {
            "bpm": stats["bpm"][0] if stats["bpm"] else None,
            "notes": sum(stats["notes"].values()),
            "duration": stats["duration"],
            "lines": stats["lines"],
        }
    except (ChartError, ValueError) as exc:
        return {"error": str(exc)[:80]}


# ---------------------------------------------------------------------------
# adb
# ---------------------------------------------------------------------------


def _adb(adb: str, *args: str, timeout: float = 600.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run([adb, *args], capture_output=True, text=True, timeout=timeout, check=False)


def cmd_pull(args: argparse.Namespace) -> int:
    result = _adb(args.adb, "shell", "pm", "path", PACKAGE)
    if result.returncode != 0 or "package:" not in result.stdout:
        print(f"找不到已安装的 {PACKAGE}。先确认手机已连接、已授权 USB 调试。")
        print(result.stdout, result.stderr)
        return 2

    apks = [line.split("package:", 1)[1].strip() for line in result.stdout.splitlines() if "package:" in line]
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"发现 {len(apks)} 个 apk/split：")
    for apk in apks:
        print("  ", apk)

    for apk in apks:
        name = Path(apk).name or "split.apk"
        temp = f"/data/local/tmp/phigros_pull_{name}"
        copy = _adb(args.adb, "shell", "cp", apk, temp, timeout=300)
        if copy.returncode != 0:
            print(f"[跳过] {name}（没有读取权限，试试把 apk 手动复制到 /sdcard/Download）")
            continue
        target = out_dir / name
        pulled = _adb(args.adb, "pull", temp, str(target), timeout=1800)
        _adb(args.adb, "shell", "rm", "-f", temp)
        if pulled.returncode == 0:
            size = target.stat().st_size / 1024 / 1024
            print(f"  ✔ {target}  ({size:.1f} MB)")
        else:
            print(f"  ✘ {name}: {pulled.stderr.strip()}")
    return 0


def cmd_unpack(args: argparse.Namespace) -> int:
    apk_dir = Path(args.apk_dir)
    if not apk_dir.exists():
        print(f"没有目录 {apk_dir}，先跑 pull")
        return 2
    out_dir = Path(args.out)
    total = 0
    for apk in sorted(apk_dir.glob("*.apk")):
        try:
            with zipfile.ZipFile(apk) as zf:
                names = [n for n in zf.namelist() if n.startswith("assets/")]
                print(f"{apk.name}：assets/ 下 {len(names)} 个文件")
                for name in names:
                    target = out_dir / apk.stem / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if target.exists() and target.stat().st_size > 0:
                        continue
                    with zf.open(name) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    total += 1
        except zipfile.BadZipFile:
            print(f"{apk.name} 不是 zip（可能是 split 或加密），跳过")
    print(f"解出 {total} 个文件 -> {out_dir}")
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    root = Path(args.dir)
    if not root.exists():
        print(f"没有目录 {root}")
        return 2
    chart_dir = Path(args.copy_dir) if args.copy else None
    if chart_dir:
        chart_dir.mkdir(parents=True, exist_ok=True)

    found = 0
    scanned = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        scanned += 1
        if args.limit and scanned > args.limit:
            break
        try:
            data = path.read_bytes()
        except OSError:
            continue
        hit = try_decode_chart(data)
        if hit is None:
            continue
        raw, how = hit
        info = describe_chart(raw, path)
        found += 1
        label = f"{path.name}  [{how}]  {info}"
        print(label)
        if chart_dir:
            target = chart_dir / f"{found:04d}_{path.name}.json"
            target.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    print(f"\n扫描 {scanned} 个文件，识别出 {found} 份谱面。")
    if found == 0:
        print(
            "没找到可解析的谱面 —— 说明它们还在 AssetBundle 里，或者被加了一层更复杂的封装。\n"
            "下一步（任选其一）：\n"
            "  1) 用 AssetStudio 打开 apk/assets：File → Load folder → Export → All assets，\n"
            "     然后把导出的 TextAsset 目录交给本脚本：scan <TextAsset 目录> --copy\n"
            "  2) 把一个 TextAsset 文件的前 64 字节 hexdump 发给我，我针对它写解码。"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="从自己的设备里提取 Phigros 谱面")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("pull", help="通过 adb 拉取 Phigros 安装包")
    p.add_argument("--adb", default="adb")
    p.add_argument("--out", default="apk")
    p.set_defaults(func=cmd_pull)

    p = sub.add_parser("unpack", help="解出 apk 里的 assets/")
    p.add_argument("--apk-dir", default="apk")
    p.add_argument("--out", default="apk/assets")
    p.set_defaults(func=cmd_unpack)

    p = sub.add_parser("scan", help="扫描目录，尝试还原谱面 JSON")
    p.add_argument("dir")
    p.add_argument(
        "--copy",
        nargs="?",
        const="charts",
        default=None,
        help="把识别到的谱面另存到这个目录（不写值就是 charts/）",
    )
    p.add_argument("--limit", type=int, default=0, help="最多扫描多少个文件")
    p.set_defaults(func=cmd_scan)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
