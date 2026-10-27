from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from phigros_auto.chart import Chart, ChartError, JudgeLine, Note
from phigros_auto.render import render_frame
from phigros_auto.schedule import Calibration, ScreenMapper, build_plan
from phigros_auto.sync import estimate_offset, song_time_at
from phigros_auto.timing import beat_to_sec, beat_value, ease
from tools.make_demo_chart import build as build_demo


class TimingTests(unittest.TestCase):
    def test_beat_forms(self) -> None:
        self.assertEqual(beat_value(8.0), 8.0)
        self.assertEqual(beat_value(8), 8.0)
        self.assertEqual(beat_value([3, 1, 4]), 3.25)
        self.assertEqual(beat_value([10, 2, 4]), 10.5)
        self.assertEqual(beat_value([0, 0, 1]), 0.0)
        self.assertEqual(beat_value([]), 0.0)

    def test_beat_to_sec(self) -> None:
        self.assertAlmostEqual(beat_to_sec(4, 120), 2.0)
        self.assertAlmostEqual(beat_to_sec(1, 150), 0.4)

    def test_ease_endpoints(self) -> None:
        for mode in range(0, 30):
            self.assertAlmostEqual(ease(0.0, mode), 0.0, places=6)
            self.assertAlmostEqual(ease(1.0, mode), 1.0, places=6)

    def test_ease_clamps(self) -> None:
        self.assertEqual(ease(-1.0, 1), 0.0)
        self.assertEqual(ease(2.0, 1), 1.0)


class ChartTests(unittest.TestCase):
    def test_load_demo(self) -> None:
        # 写到工作区里而不是系统临时目录：受限环境对临时目录的删除有额外限制
        path = ROOT / "out" / "test_demo.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(build_demo()), encoding="utf-8")
        chart = Chart.load(path)
        self.assertEqual(len(chart.lines), 1)
        self.assertGreater(chart.note_count(), 30)
        self.assertGreater(chart.duration, 10.0)
        self.assertFalse([w for w in chart.warnings if "未知" in w])

    def test_missing_judge_lines(self) -> None:
        with self.assertRaises(ChartError):
            Chart.from_dict({"formatVersion": 3})

    def test_move_event_midpoint(self) -> None:
        chart = Chart.from_dict(
            {
                "formatVersion": 3,
                "judgeLineList": [
                    {
                        "bpm": 120,
                        "judgeLineMoveEvents": [
                            {
                                "startTime": [0, 0, 1],
                                "endTime": [2, 0, 1],
                                "start": 0.5,
                                "end": 0.5,
                                "start2": 0.6,
                                "end2": 0.4,
                            }
                        ],
                        "notesAbove": [{"type": 1, "time": [0, 0, 1], "positionX": 0.5}],
                    }
                ],
            }
        )
        line = chart.lines[0]
        # 120 BPM：2 拍 = 1 秒，所以 0.5 秒正好中点
        self.assertAlmostEqual(line.position_at(0.5)[1], 0.5, places=6)
        self.assertAlmostEqual(line.position_at(0.0)[1], 0.6, places=6)
        self.assertAlmostEqual(line.position_at(1.0)[1], 0.4, places=6)

    def test_floor_interpolation(self) -> None:
        chart = Chart.from_dict(
            {
                "judgeLineList": [
                    {
                        "bpm": 60,
                        "notesAbove": [
                            {"type": 1, "time": [0, 0, 1], "floorPosition": 0.0},
                            {"type": 1, "time": [2, 0, 1], "floorPosition": 12.0},
                        ],
                    }
                ]
            }
        )
        line = chart.lines[0]
        self.assertAlmostEqual(line.floor_at(1.0), 6.0, places=6)
        self.assertAlmostEqual(line.floor_at(0.0), 0.0, places=6)


class ScheduleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.chart = Chart.from_dict(build_demo())

    def test_plan_covers_every_note(self) -> None:
        calib = Calibration(width=720, height=1280)
        plan = build_plan(self.chart, calib)
        self.assertEqual(len(plan.touches), self.chart.note_count())
        self.assertEqual(plan.dropped, [])
        downs = [t.down_t for t in plan.touches]
        self.assertEqual(downs, sorted(downs))
        self.assertLessEqual(plan.max_fingers(), calib.max_pointers)

    def test_hold_duration_matches_chart(self) -> None:
        plan = build_plan(self.chart, Calibration(width=720, height=1280))
        holds = [t for t in plan.touches if t.kind == "hold"]
        self.assertTrue(holds)
        for touch in holds:
            self.assertAlmostEqual(touch.up_t - touch.down_t, touch.note.hold, places=6)
            self.assertGreater(len(touch.moves), 0)

    def test_flick_moves_are_dense(self) -> None:
        chart = Chart.from_dict(
            {
                "judgeLineList": [
                    {
                        "bpm": 60,
                        "notesAbove": [{"type": 4, "time": [0, 0, 1], "positionX": 0.5}],
                    }
                ]
            }
        )
        plan = build_plan(chart, Calibration(width=720, height=1280))
        flick = plan.touches[0]
        self.assertEqual(flick.kind, "flick")
        # 提前 30ms 按下、30ms 划过 → 约 60 个 1ms 的 MOVE
        self.assertGreaterEqual(len(flick.moves), 55)
        self.assertLessEqual(len(flick.moves), 65)
        times = [t for t, _ in flick.moves]
        self.assertEqual(times, sorted(times))
        self.assertAlmostEqual(times[-1], flick.up_t, delta=0.002)

    def test_hold_follows_moving_line_densely(self) -> None:
        chart = Chart.from_dict(
            {
                "judgeLineList": [
                    {
                        "bpm": 60,
                        "judgeLineMoveEvents": [
                            {
                                "startTime": [1, 0, 1],
                                "endTime": [3, 0, 1],
                                "start": 0.5,
                                "end": 0.5,
                                "start2": 0.2,
                                "end2": 0.8,
                            }
                        ],
                        "notesAbove": [{"type": 3, "time": [1, 0, 1], "positionX": 0.5, "holdTime": 4}],
                    }
                ]
            }
        )
        plan = build_plan(chart, Calibration(width=720, height=1280))
        hold = plan.touches[0]
        self.assertEqual(hold.kind, "hold")
        # 判定线在动：1ms 采样下每个像素变化都发一次 MOVE，间隔不超过几毫秒
        self.assertGreater(len(hold.moves), 500)
        times = [t for t, _ in hold.moves]
        gaps = [b - a for a, b in zip(times, times[1:])]
        self.assertLessEqual(max(gaps), 0.006)

    def test_center_note_maps_to_center(self) -> None:
        line = JudgeLine(index=0, bpm=150.0)
        note = Note(kind=1, time=0.0, position_x=0.5)
        mapper = ScreenMapper(Calibration(width=1080, height=2400), note_x_centered=False)
        point = mapper.point(line, note, 0.0)
        self.assertEqual((point.x, point.y), (540, 1200))

    def test_x_range_is_symmetric(self) -> None:
        line = JudgeLine(index=0, bpm=150.0)
        mapper = ScreenMapper(Calibration(width=1000, height=1000, span=1.0), note_x_centered=False)
        left = mapper.point(line, Note(kind=1, time=0.0, position_x=0.0), 0.0)
        right = mapper.point(line, Note(kind=1, time=0.0, position_x=1.0), 0.0)
        self.assertEqual(left.x, 0)
        self.assertEqual(right.x, 1000 - 1)

    def test_rotation_moves_notes_along_line(self) -> None:
        chart = Chart.from_dict(
            {
                "judgeLineList": [
                    {
                        "bpm": 60,
                        "judgeLineRotateEvents": [
                            {"startTime": [0, 0, 1], "endTime": [0, 0, 1], "start": 90, "end": 90}
                        ],
                        "notesAbove": [{"type": 1, "time": [0, 0, 1], "positionX": 0.75}],
                    }
                ]
            }
        )
        line = chart.lines[0]
        note = line.notes[0]
        point = ScreenMapper(Calibration(width=1000, height=1000), note_x_centered=False).point(line, note, 0.0)
        # 旋转 90 度后，x 方向的偏移变成 y 方向；phisap 用 -angle，所以是向上
        self.assertAlmostEqual(point.x, 500, delta=2)
        self.assertAlmostEqual(point.y, 250, delta=2)

    def test_render_frame_size(self) -> None:
        image = render_frame(self.chart, 4.0, Calibration(width=320, height=180))
        self.assertEqual(image.size, (320, 180))
        self.assertEqual(image.mode, "RGB")


class SyncTests(unittest.TestCase):
    def test_offset_recovery(self) -> None:
        sr = 8000
        rng = np.random.default_rng(11)
        song = rng.normal(0.0, 0.01, int(sr * 16)).astype(np.float32)
        for at in np.sort(rng.uniform(0.0, 15.5, 40)):
            start = int(at * sr)
            burst = rng.normal(0.0, 0.6, 200).astype(np.float32) * np.hanning(200).astype(np.float32)
            song[start : start + 200] += burst

        lead = 3.2  # 录音先空 3.2 秒
        song_from = 4.0  # 然后从歌曲第 4 秒开始录
        recording = np.concatenate(
            [np.zeros(int(lead * sr), dtype=np.float32), song[int(song_from * sr) :]]
        )
        recording += rng.normal(0.0, 0.005, recording.shape).astype(np.float32)

        result = estimate_offset(song, recording, sr, max_offset=10.0)
        expected = lead - song_from  # = -0.8
        self.assertAlmostEqual(result["offset"], expected, delta=0.04)
        self.assertGreater(result["confidence"], 0.0)

    def test_song_time_roundtrip(self) -> None:
        self.assertAlmostEqual(song_time_at(-0.8, 2.0), 2.8)
        self.assertAlmostEqual(song_time_at(0.0, 2.0), 2.0)


class OfficialFormatTests(unittest.TestCase):
    """官方谱面的两种坐标系：time 是 1/32 拍，positionX 以 0 为中心、±7 到屏幕两侧。"""

    RAW = {
        "formatVersion": 3,
        "offset": 0.0,
        "judgeLineList": [
            {
                "bpm": 180.0,
                "notesAbove": [
                    {"type": 1, "time": 688, "positionX": -3.0},
                    {"type": 1, "time": 784, "positionX": 0.0, "holdTime": 32.0, "type": 3},
                    {"type": 1, "time": 14336, "positionX": 7.0},
                ],
            }
        ],
    }

    def test_tick_detected(self) -> None:
        chart = Chart.from_dict(self.RAW)
        self.assertEqual(chart.tick, 32)

    def test_time_conversion(self) -> None:
        chart = Chart.from_dict(self.RAW)
        notes = chart.notes()
        # 688 / 32 = 21.5 拍；180 BPM => 7.1667 秒
        self.assertAlmostEqual(notes[0].time, 21.5 * 60.0 / 180.0, places=6)
        self.assertAlmostEqual(notes[2].time, 448 * 60.0 / 180.0, places=6)

    def test_hold_time_converted(self) -> None:
        chart = Chart.from_dict(self.RAW)
        hold = [n for n in chart.notes() if n.kind == 3][0]
        self.assertAlmostEqual(hold.hold, 32 / 32 * 60.0 / 180.0, places=6)

    def test_note_x_centered(self) -> None:
        chart = Chart.from_dict(self.RAW)
        self.assertTrue(chart.note_x_centered)

    def test_x_mapping_to_screen(self) -> None:
        chart = Chart.from_dict(self.RAW)
        calib = Calibration(width=1400, height=800)
        plan = build_plan(chart, calib)
        # 720 高基准：1 单位 = 72px，缩放到 800 高 => 80px/单位
        # 视口 16:9 居中 => field_x = (1400 - 800*16/9)/2 ≈ -11.1，中心 700
        self.assertAlmostEqual(plan.touches[0].down.x, 700 - 3 * 80, delta=2)
        self.assertAlmostEqual(plan.touches[1].down.x, 700, delta=2)
        self.assertAlmostEqual(plan.touches[2].down.x, 700 + 7 * 80, delta=2)


class CliTests(unittest.TestCase):
    def test_sync_cli_end_to_end(self) -> None:
        import wave

        sr = 8000
        rng = np.random.default_rng(3)
        song = rng.normal(0.0, 0.01, int(sr * 12)).astype(np.float32)
        for at in np.sort(rng.uniform(0.0, 11.5, 30)):
            start = int(at * sr)
            burst = rng.normal(0.0, 0.7, 200).astype(np.float32) * np.hanning(200).astype(np.float32)
            song[start : start + 200] += burst
        lead = 2.0
        recording = np.concatenate(
            [np.zeros(int(lead * sr), dtype=np.float32), song[int(3.0 * sr) :]]
        )

        out = ROOT / "out"
        out.mkdir(parents=True, exist_ok=True)
        song_path = out / "cli_song.wav"
        rec_path = out / "cli_rec.wav"
        for path, data in ((song_path, song), (rec_path, recording)):
            pcm = (np.clip(data, -1.0, 1.0) * 32767.0).astype(np.int16)
            with wave.open(str(path), "wb") as f:
                f.setnchannels(1)
                f.setsampwidth(2)
                f.setframerate(sr)
                f.writeframes(pcm.tobytes())

        from phigros_auto.cli import main

        self.assertEqual(main(["sync", str(song_path), str(rec_path), "--max-offset", "10"]), 0)


class RandomAreaTests(unittest.TestCase):
    """blockAreaList = 游戏里 Random 模式会打乱的区域。"""

    def test_block_area_list_is_parsed(self) -> None:
        chart = Chart.from_dict(
            {
                "judgeLineList": [{"bpm": 120, "notesAbove": []}],
                "blockAreaList": [
                    {
                        "appearTime": 1.0,
                        "enableTime": 2.0,
                        "disableTime": 3.0,
                        "disappearTime": 4.0,
                    }
                ],
            }
        )
        self.assertEqual(len(chart.block_areas), 1)
        self.assertEqual(chart.stats()["randomAreas"], 1)
        self.assertTrue(any("随机" in w for w in chart.warnings))

    def test_chart_without_block_area_is_clean(self) -> None:
        chart = Chart.from_dict({"judgeLineList": [{"bpm": 120, "notesAbove": []}]})
        self.assertEqual(chart.block_areas, [])
        self.assertEqual(chart.stats()["randomAreas"], 0)
        self.assertFalse(any("随机" in w for w in chart.warnings))


if __name__ == "__main__":
    unittest.main(verbosity=2)
