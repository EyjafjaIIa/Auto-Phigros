from __future__ import annotations

import socket
import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from phigros_auto.scrcpy import (
    ACTION_DOWN,
    ACTION_MOVE,
    ACTION_UP,
    ScrcpyBackend,
    ScrcpyControl,
    detect_server_version,
    encode_touch,
    guess_version,
)
from phigros_auto.schedule import Point

LAYOUT = ">BBqiiHHHii"


class EncodeTests(unittest.TestCase):
    def test_layout_matches_protocol(self) -> None:
        data = encode_touch(ACTION_DOWN, 0, 100, 200, 1080, 2400, 1.0)
        self.assertEqual(len(data), 32)
        self.assertEqual(struct.calcsize(LAYOUT), 32)

        msg_type, action, pointer, x, y, w, h, pressure, action_button, buttons = struct.unpack(
            LAYOUT, data
        )
        self.assertEqual(msg_type, 2)
        self.assertEqual(action, ACTION_DOWN)
        self.assertEqual(pointer, 0)
        self.assertEqual((x, y), (100, 200))
        self.assertEqual((w, h), (1080, 2400))
        self.assertEqual(pressure, 0xFFFF)
        self.assertEqual((action_button, buttons), (0, 0))

    def test_pointer_and_pressure(self) -> None:
        data = encode_touch(ACTION_UP, 7, 3, 4, 2400, 1080, 0.0)
        _, action, pointer, x, y, w, h, pressure, _, _ = struct.unpack(LAYOUT, data)
        self.assertEqual((action, pointer), (ACTION_UP, 7))
        self.assertEqual((x, y, w, h), (3, 4, 2400, 1080))
        self.assertEqual(pressure, 0)


class ControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.peer, self.client = socket.socketpair()
        self.peer.settimeout(2.0)
        self.control = ScrcpyControl(self.client, (1080, 2400))

    def tearDown(self) -> None:
        self.control.close()
        self.peer.close()

    def _recv(self) -> tuple:
        raw = b""
        while len(raw) < 32:
            chunk = self.peer.recv(32 - len(raw))
            if not chunk:
                break
            raw += chunk
        return struct.unpack(LAYOUT, raw)

    def test_send_touch_sequence(self) -> None:
        self.control.send_touch(ACTION_DOWN, 2, Point(120, 340))
        self.control.send_touch(ACTION_MOVE, 2, Point(130, 350))
        self.control.send_touch(ACTION_UP, 2, Point(130, 350))

        down = self._recv()
        move = self._recv()
        up = self._recv()

        self.assertEqual(down[1:3], (ACTION_DOWN, 2))
        self.assertEqual(down[3:5], (120, 340))
        self.assertEqual(down[7], 0xFFFF)
        self.assertEqual(move[1], ACTION_MOVE)
        self.assertEqual(move[3:5], (130, 350))
        self.assertEqual(up[1], ACTION_UP)
        self.assertEqual(up[7], 0)


class FakeServer:
    def __init__(self, control: ScrcpyControl):
        self.control = control
        self.closed = False

    def start(self) -> ScrcpyControl:
        return self.control

    def close(self) -> None:
        self.closed = True


class BackendTests(unittest.TestCase):
    def test_release_keeps_last_position(self) -> None:
        peer, client = socket.socketpair()
        peer.settimeout(2.0)
        control = ScrcpyControl(client, (1080, 2400))
        backend = ScrcpyBackend(FakeServer(control))  # type: ignore[arg-type]

        backend.press(1, Point(500, 600))
        backend.drag(1, Point(520, 620))
        backend.release(1)

        messages = []
        for _ in range(3):
            raw = b""
            while len(raw) < 32:
                raw += peer.recv(32 - len(raw))
            messages.append(struct.unpack(LAYOUT, raw))

        self.assertEqual(messages[0][1], ACTION_DOWN)
        self.assertEqual(messages[1][1], ACTION_MOVE)
        self.assertEqual(messages[2][1], ACTION_UP)
        # 抬起必须落在最后的坐标上，否则系统会当成滑动
        self.assertEqual(messages[2][3:5], (520, 620))

        backend.close()
        self.assertTrue(backend.server.closed)
        peer.close()


class VersionTests(unittest.TestCase):
    def test_guess_from_filename(self) -> None:
        self.assertEqual(guess_version(Path("scrcpy-server-v2.4")), "2.4")
        self.assertEqual(guess_version(Path("scrcpy-server-v2.4.1")), "2.4.1")
        self.assertEqual(guess_version(Path("scrcpy-server-v5.0.1")), "5.0.1")

    def test_guess_without_version_in_name(self) -> None:
        # 文件名没有版本号，且文件不存在 → 认不出，返回 None（由调用方报错）
        self.assertIsNone(guess_version(Path("scrcpy-server")))

    def test_detect_version_from_dex(self) -> None:
        import zipfile

        target = ROOT / "out" / "fake_server"
        payload = (
            b"some dex bytes\x00"
            b"The server version (9.9.9) does not match the client (x)\x00"
        )
        with zipfile.ZipFile(target, "w") as zf:
            zf.writestr("classes.dex", payload)
        self.assertEqual(detect_server_version(target), "9.9.9")
        # 名字里没版本号时，应该退回到读文件
        self.assertEqual(guess_version(target), "9.9.9")


if __name__ == "__main__":
    unittest.main(verbosity=2)
