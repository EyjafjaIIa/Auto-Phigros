from __future__ import annotations

import gzip
import json
import sys
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.import_charts import describe_chart, try_decode_chart
from tools.make_demo_chart import build as build_demo

CHART = build_demo()
PLAIN = json.dumps(CHART, ensure_ascii=False).encode("utf-8")


def xor(data: bytes, key: bytes) -> bytes:
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


class DecodeTests(unittest.TestCase):
    def test_plain(self) -> None:
        hit = try_decode_chart(PLAIN)
        self.assertIsNotNone(hit)
        raw, how = hit  # type: ignore[misc]
        self.assertEqual(how, "plain")
        self.assertIn("judgeLineList", raw)

    def test_gzip(self) -> None:
        hit = try_decode_chart(gzip.compress(PLAIN))
        self.assertIsNotNone(hit)
        raw, how = hit  # type: ignore[misc]
        self.assertEqual(how, "gzip")
        self.assertIn("judgeLineList", raw)

    def test_zlib(self) -> None:
        hit = try_decode_chart(zlib.compress(PLAIN))
        self.assertIsNotNone(hit)
        raw, how = hit  # type: ignore[misc]
        self.assertIn("judgeLineList", raw)

    def test_single_byte_xor(self) -> None:
        hit = try_decode_chart(xor(PLAIN, b"\x5a"))
        self.assertIsNotNone(hit)
        raw, how = hit  # type: ignore[misc]
        self.assertTrue(how.startswith("xor1"))
        self.assertIn("judgeLineList", raw)

    def test_repeating_key_xor(self) -> None:
        key = b"phigros!"
        hit = try_decode_chart(xor(PLAIN, key))
        self.assertIsNotNone(hit)
        raw, how = hit  # type: ignore[misc]
        self.assertTrue(how.startswith("xor8"), how)
        self.assertIn("judgeLineList", raw)

    def test_garbage_returns_none(self) -> None:
        self.assertIsNone(try_decode_chart(bytes(range(256)) * 4))

    def test_describe(self) -> None:
        info = describe_chart(CHART, Path("demo.json"))
        self.assertGreater(info["notes"], 30)
        self.assertEqual(info["bpm"], 150.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
