from __future__ import annotations

import base64
import json
import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from phigros_auto.tracks import load_catalog

KEY = "Assets/Tracks/TestSong.TestArtist.0/Chart_IN.json"


def _keys_blob(keys: list[str]) -> bytes:
    out = bytearray(struct.pack("<i", len(keys)))
    for text in keys:
        raw = text.encode("utf-8")
        out += b"\x00" + struct.pack("<i", len(raw)) + raw
    return bytes(out)


def _buckets_blob(entries: list[tuple[int, list[int]]]) -> bytes:
    out = bytearray(struct.pack("<i", len(entries)))
    for key_offset, indexes in entries:
        out += struct.pack("<ii", key_offset, len(indexes))
        out += struct.pack(f"<{len(indexes)}i", *indexes)
    return bytes(out)


def _entries_blob(internal_ids: list[int]) -> bytes:
    out = bytearray(struct.pack("<i", len(internal_ids)))
    for internal_id in internal_ids:
        out += struct.pack("<7i", 0, 2, internal_id, 0, -1, 0, 5)
    return bytes(out)


class CatalogTests(unittest.TestCase):
    def _make_catalog(self, tmp: Path) -> Path:
        aa = tmp / "aa"
        (aa / "Android").mkdir(parents=True, exist_ok=True)
        (aa / "Android" / "abc.bundle").write_bytes(b"UnityFS")

        keys = _keys_blob([KEY])
        # key 的数据从偏移 4 开始（前面是 int32 数量）
        buckets = _buckets_blob([(4, [0])])
        entries = _entries_blob([0])
        catalog = {
            "m_InternalIds": ["{UnityEngine.AddressableAssets.Addressables.RuntimePath}/Android/abc.bundle"],
            "m_KeyDataString": base64.b64encode(keys).decode(),
            "m_BucketDataString": base64.b64encode(buckets).decode(),
            "m_EntryDataString": base64.b64encode(entries).decode(),
        }
        (aa / "catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
        return aa

    def test_parses_chart_key(self) -> None:
        aa = self._make_catalog(ROOT / "out")
        charts = load_catalog(aa)
        self.assertEqual(len(charts), 1)
        chart = charts[0]
        self.assertEqual(chart.song, "TestSong")
        self.assertEqual(chart.artist, "TestArtist")
        self.assertEqual(chart.difficulty, "IN")
        self.assertEqual(chart.bundle.name, "abc.bundle")

    def test_missing_catalog(self) -> None:
        with self.assertRaises(Exception):
            load_catalog(ROOT / "out" / "does-not-exist")


if __name__ == "__main__":
    unittest.main(verbosity=2)
