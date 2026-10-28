"""从 Phigros 的 Addressables 目录里提取全部谱面。

Phigros 用 Unity Addressables 打包，结构是：

    aa/catalog.json              key -> 资源位置 的索引
    aa/Android/<hash>.bundle     UnityFS 资源包，谱面是里面的 TextAsset

``catalog.json`` 里三块二进制（都是 base64）：

* ``m_KeyDataString``：``int32 数量``，然后每个 key 是
  ``byte 类型(0=字符串) + int32 长度 + UTF-8``；
* ``m_BucketDataString``：``int32 桶数``，每桶 ``int32 key偏移, int32 n, int32[n] 条目下标``；
* ``m_EntryDataString``：``int32 条目数``，每条 28 字节，**第 3 个 int 是 internalId**。

key 长这样：``Assets/Tracks/<歌名>.<曲师>.0/Chart_IN.json``，
所以提取出来就能直接按「歌名 + 难度」命名。
"""

from __future__ import annotations

import base64
import json
import re
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

CHART_KEY = re.compile(r"^Assets/Tracks/(?P<folder>.+)/Chart_(?P<diff>[A-Za-z]+)\.json$")
ENTRY_RECORD = 28
ENTRY_INTERNAL_ID = 2  # 第 3 个 int

DIFF_ORDER = {"EZ": 0, "HD": 1, "IN": 2, "AT": 3, "SP": 4, "Legacy": 5}

_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class CatalogError(ValueError):
    """catalog.json 结构不是预期的样子。"""


@dataclass(frozen=True)
class TrackChart:
    """一份谱面：歌名、曲师、难度、以及它藏在哪个资源包里。"""

    key: str
    song: str
    artist: str
    difficulty: str
    bundle: Path

    @property
    def label(self) -> str:
        return f"{self.song} [{self.difficulty}]"

    @property
    def text_asset_name(self) -> str:
        return f"Chart_{self.difficulty}"


def _read_key(blob: bytes, offset: int) -> str | None:
    """key 是 ``byte 类型 + int32 长度 + 内容``。"""
    if offset < 0 or offset >= len(blob):
        return None
    kind = blob[offset]
    if kind != 0:  # 只处理字符串类型的 key
        return None
    (length,) = struct.unpack_from("<i", blob, offset + 1)
    start = offset + 5
    return blob[start : start + length].decode("utf-8", "replace")


def _parse_buckets(blob: bytes) -> list[tuple[int, tuple[int, ...]]]:
    if len(blob) < 4:
        raise CatalogError("m_BucketDataString 太短")
    (count,) = struct.unpack_from("<i", blob, 0)
    pos = 4
    buckets: list[tuple[int, tuple[int, ...]]] = []
    for _ in range(count):
        if pos + 8 > len(blob):
            raise CatalogError("m_BucketDataString 在桶头处提前结束")
        key_offset, entries_count = struct.unpack_from("<ii", blob, pos)
        pos += 8
        if entries_count < 0 or pos + 4 * entries_count > len(blob):
            raise CatalogError(f"bucket 条目数异常：{entries_count}")
        entries = struct.unpack_from(f"<{entries_count}i", blob, pos) if entries_count else ()
        pos += 4 * entries_count
        buckets.append((key_offset, entries))
    return buckets


def _parse_internal_ids(blob: bytes) -> list[int]:
    if len(blob) < 4:
        raise CatalogError("m_EntryDataString 太短")
    (count,) = struct.unpack_from("<i", blob, 0)
    expected = 4 + count * ENTRY_RECORD
    if expected != len(blob):
        raise CatalogError(
            f"m_EntryDataString 长度不匹配：预期 {expected}，实际 {len(blob)}"
            "（可能是 Unity 版本差异，需要重新分析格式）"
        )
    ids: list[int] = []
    for index in range(count):
        (internal_id,) = struct.unpack_from(
            "<i", blob, 4 + index * ENTRY_RECORD + ENTRY_INTERNAL_ID * 4
        )
        ids.append(internal_id)
    return ids


def load_catalog(aa_dir: str | Path) -> list[TrackChart]:
    """解析 ``aa/catalog.json``，返回所有谱面及其所在 bundle。"""
    aa_dir = Path(aa_dir)
    catalog_path = aa_dir / "catalog.json"
    if not catalog_path.is_file():
        raise CatalogError(f"找不到 {catalog_path}（应该在 apk/assets/base/assets/aa 下）")

    data = json.loads(catalog_path.read_text(encoding="utf-8"))
    for field in ("m_KeyDataString", "m_BucketDataString", "m_EntryDataString", "m_InternalIds"):
        if field not in data:
            raise CatalogError(f"catalog.json 缺少字段 {field}")

    internal_ids: list[str] = data["m_InternalIds"]
    keys_blob = base64.b64decode(data["m_KeyDataString"])
    bucket_blob = base64.b64decode(data["m_BucketDataString"])
    entry_blob = base64.b64decode(data["m_EntryDataString"])

    buckets = _parse_buckets(bucket_blob)
    internal = _parse_internal_ids(entry_blob)
    android_dir = aa_dir / "Android"

    charts: list[TrackChart] = []
    seen: set[tuple[str, str]] = set()
    for key_offset, entries in buckets:
        key = _read_key(keys_blob, key_offset)
        if not key:
            continue
        match = CHART_KEY.match(key)
        if not match:
            continue
        folder = match.group("folder")
        difficulty = match.group("diff")
        parts = folder.rsplit(".", 2)
        if len(parts) == 3:
            song, artist, _ = parts
        elif len(parts) == 2:
            song, artist = parts
        else:
            song, artist = folder, ""

        bundle: Path | None = None
        for entry in entries:
            if not (0 <= entry < len(internal)):
                continue
            internal_id = internal[entry]
            if not (0 <= internal_id < len(internal_ids)):
                continue
            target = internal_ids[internal_id]
            if not target.endswith(".bundle"):
                continue
            candidate = android_dir / Path(target).name
            if candidate.is_file():
                bundle = candidate
                break
        if bundle is None:
            continue

        marker = (song, difficulty)
        if marker in seen:
            continue
        seen.add(marker)
        charts.append(TrackChart(key, song, artist, difficulty, bundle))

    charts.sort(key=lambda c: (c.song.lower(), DIFF_ORDER.get(c.difficulty, 99)))
    return charts


def _setup_unity_env() -> None:
    """UnityPy 解码音频需要 FMOD 动态库；它自带了，指给它就行。"""
    import os
    import types

    vendor = Path(__file__).resolve().parents[1] / "out" / "vendor"
    for candidate in (
        vendor / "UnityPy" / "lib" / "FMOD" / "Windows" / "x64" / "fmod.dll",
        vendor / "fmod_toolkit" / "libfmod" / "Windows" / "x64" / "fmod.dll",
    ):
        if candidate.is_file():
            os.environ.setdefault("PYFMODEX_DLL_PATH", str(candidate))
            break
    # 只解析音频/文本时不需要贴图解码器
    if "astc_encoder" not in sys.modules:
        try:
            import astc_encoder  # noqa: F401
        except ImportError:
            sys.modules["astc_encoder"] = types.ModuleType("astc_encoder")


def _import_unitypy():
    _setup_unity_env()
    try:
        import UnityPy

        return UnityPy
    except ImportError:
        pass
    for root in (
        Path(__file__).resolve().parents[1] / "vendor",
        Path(__file__).resolve().parents[1] / "out" / "vendor",
    ):
        if root.is_dir() and str(root) not in sys.path:
            sys.path.insert(0, str(root))
    try:
        import UnityPy

        return UnityPy
    except ImportError as exc:  # pragma: no cover - 取决于环境
        raise RuntimeError(
            "解析 Unity 资源包需要 UnityPy：\n"
            "  python -m pip install UnityPy\n"
            "（也可以把 wheel 解压到仓库根的 vendor/ 目录）"
        ) from exc


def iter_entries(aa_dir: str | Path) -> dict[str, Path]:
    """catalog 里所有 key -> 它所在的 bundle 路径。"""
    aa_dir = Path(aa_dir)
    data = json.loads((aa_dir / "catalog.json").read_text(encoding="utf-8"))
    internal_ids: list[str] = data["m_InternalIds"]
    keys_blob = base64.b64decode(data["m_KeyDataString"])
    buckets = _parse_buckets(base64.b64decode(data["m_BucketDataString"]))
    internal = _parse_internal_ids(base64.b64decode(data["m_EntryDataString"]))
    android = aa_dir / "Android"

    result: dict[str, Path] = {}
    for key_offset, entries in buckets:
        key = _read_key(keys_blob, key_offset)
        if not key or key in result:
            continue
        for entry in entries:
            if not (0 <= entry < len(internal)):
                continue
            internal_id = internal[entry]
            if not (0 <= internal_id < len(internal_ids)):
                continue
            target = internal_ids[internal_id]
            if not target.endswith(".bundle"):
                continue
            candidate = android / Path(target).name
            if candidate.is_file():
                result[key] = candidate
                break
    return result


def _song_of_key(key: str) -> str | None:
    parts = key.split("/")
    if len(parts) >= 4 and parts[0] == "Assets" and parts[1] == "Tracks":
        return parts[2].rsplit(".", 2)[0]
    return None


def extract_audio(
    aa_dir: str | Path,
    song: str,
    out_path: str | Path,
    *,
    logger=print,
) -> Path:
    """把某首歌的 ``music.wav`` 从资源包里导出来（做互相关的参考音频）。"""
    entries = iter_entries(aa_dir)
    key = next(
        (
            k
            for k in entries
            if k.endswith("/music.wav") and _song_of_key(k) == song
        ),
        None,
    )
    if key is None:
        raise FileNotFoundError(f"catalog 里找不到 {song} 的 music.wav")
    logger(f"[audio] {key} -> {entries[key].name}")

    UnityPy = _import_unitypy()
    env = UnityPy.load(str(entries[key]))
    for obj in env.objects:  # type: ignore[union-attr]
        if obj.type.name != "AudioClip":
            continue
        clip = obj.read()
        samples = clip.samples
        if not samples:
            break
        _, raw = next(iter(samples.items()))
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(raw)
        logger(f"[audio] 写出 {out}（{len(raw) / 1024 / 1024:.1f} MB，{getattr(clip, 'm_Length', 0):.1f}s）")
        return out
    raise RuntimeError(f"{entries[key].name} 里没有可解码的 AudioClip")


def _safe(name: str) -> str:
    cleaned = _BAD_CHARS.sub("_", name).strip(" .")
    return cleaned or "unknown"


def extract_charts(
    charts: Iterable[TrackChart],
    out_dir: str | Path,
    *,
    logger=print,
    on_step=None,
    verify: bool = True,
) -> list[dict]:
    """把每份谱面从 bundle 里读出来，写成 ``<歌名>.<曲师>.<难度>.json``。"""
    UnityPy = _import_unitypy()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    manifest: list[dict] = []
    total = len(charts) if isinstance(charts, list) else None
    cache: dict[Path, object] = {}

    for index, chart in enumerate(charts, 1):
        env = cache.get(chart.bundle)
        if env is None:
            env = UnityPy.load(str(chart.bundle))
            cache = {chart.bundle: env}  # 只缓存当前 bundle
        script: bytes | None = None
        for obj in env.objects:  # type: ignore[union-attr]
            if obj.type.name != "TextAsset":
                continue
            data = obj.read()
            if data.m_Name != chart.text_asset_name:
                continue
            raw = data.m_Script
            script = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
            break
        if script is None:
            logger(f"[跳过] {chart.label}: 包里没有 {chart.text_asset_name}")
            continue

        payload = json.loads(script.decode("utf-8-sig"))
        if verify and "judgeLineList" not in payload:
            logger(f"[跳过] {chart.label}: 不是谱面 JSON")
            continue

        filename = f"{_safe(chart.song)}.{_safe(chart.artist)}.{chart.difficulty}.json"
        target = out / filename
        if target.exists():
            target = out / f"{_safe(chart.song)}.{_safe(chart.artist)}.{chart.difficulty}.dup.json"
        target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        notes = sum(
            len(line.get("notesAbove") or []) + len(line.get("notesBelow") or [])
            for line in payload.get("judgeLineList") or []
        )
        bpm = None
        for line in payload.get("judgeLineList") or []:
            if line.get("bpm"):
                bpm = float(line["bpm"])
                break

        manifest.append(
            {
                "song": chart.song,
                "artist": chart.artist,
                "difficulty": chart.difficulty,
                "file": target.name,
                "bpm": bpm,
                "notes": notes,
                "bundle": chart.bundle.name,
            }
        )
        if on_step is not None:
            on_step(index, total, chart)
        elif total and (index % 50 == 0 or index == total):
            logger(f"  已提取 {index}/{total}")

    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest
