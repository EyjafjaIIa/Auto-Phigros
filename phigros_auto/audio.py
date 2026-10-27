"""音频对齐：从手机实时音频里定位「现在播到歌曲第几秒」。

思路
----
1. scrcpy 用 ``audio_codec=raw`` 转发手机的原始 PCM（48kHz 立体声 16bit）；
2. 参考曲目用独立的 WAV（从游戏资源里解出来的 ``music.wav``）；
3. 两边都压成 ~100Hz 的能量包络，采样率不同也无所谓；
4. 用**归一化**互相关（NCC）找窗口在曲目里的位置 —— 不做归一化的话，
   长曲目尾部的响段会把峰值抢走（实测真值只排到 136 名）。

误差约 ±10ms（包络帧长 10ms + 抛物线插值）。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np

from .sync import envelope

ENV_RATE = 100.0


def _zscore(array: np.ndarray) -> np.ndarray:
    array = array.astype(np.float32)
    std = float(array.std())
    if std < 1e-9:
        return array - float(array.mean())
    return (array - float(array.mean())) / std


def ncc_position(song: np.ndarray, live: np.ndarray) -> tuple[float, float]:
    """归一化互相关：返回 (窗口起点在歌曲中的秒数, 得分 0~1)。

    ``song`` / ``live`` 都是**包络**（同一采样率），``live`` 必须比 ``song`` 短。
    """
    if len(live) < 4 or len(song) < len(live) + 2:
        raise ValueError("音频太短，无法定位")

    a = _zscore(song)
    b = _zscore(live)
    lb = len(b)
    la = len(a)

    n = 1 << (la + lb - 1).bit_length()
    corr = np.fft.irfft(np.fft.rfft(b, n) * np.conj(np.fft.rfft(a, n)), n)

    # 起点 p 对应 corr[-p]
    starts = np.arange(la - lb + 1)
    num = corr[(-starts) % n]

    # 窗口能量（滑动和）
    energy = np.concatenate(([0.0], np.cumsum(a * a)))
    window_energy = energy[starts + lb] - energy[starts]
    denom = np.sqrt(np.maximum(window_energy, 1e-9)) * float(np.linalg.norm(b))
    scores = num / np.maximum(denom, 1e-9)

    best = int(np.argmax(scores))
    # 抛物线插值，把 10ms 的粒度细化到 ~2ms
    offset = 0.0
    if 0 < best < len(scores) - 1:
        y0, y1, y2 = scores[best - 1], scores[best], scores[best + 1]
        den = y0 - 2 * y1 + y2
        if abs(den) > 1e-12:
            offset = float(np.clip(0.5 * (y0 - y2) / den, -0.5, 0.5))
    return (best + offset) / ENV_RATE, float(scores[best])


def estimate_position(
    song: np.ndarray,
    song_rate: int,
    live: np.ndarray,
    live_rate: int,
    *,
    max_seconds: float | None = None,
) -> dict[str, float]:
    """给一段实时录音，估计它结尾处对应歌曲的哪个时刻。"""
    env_song, rate_song = envelope(song, song_rate, ENV_RATE)
    env_live, rate_live = envelope(live, live_rate, ENV_RATE)
    if max_seconds is not None:
        limit = int(max_seconds * rate_song)
        env_song = env_song[-limit:] if limit < len(env_song) else env_song
    position, score = ncc_position(env_song, env_live)
    # 窗口结尾才是「现在」
    end = position + len(env_live) / rate_live
    return {"position": end, "start": position, "score": score}


@dataclass
class Slice:
    samples: np.ndarray  # float32 单声道
    start_time: float  # perf_counter，对应第一个样本
    end_time: float


class PcmStream:
    """持续读 scrcpy 的原始 PCM，保留最近若干秒，可随时取快照。"""

    def __init__(
        self,
        sock,
        *,
        rate: int = 48000,
        channels: int = 2,
        keep_seconds: float = 40.0,
        logger: Callable[[str], None] = print,
    ):
        self.sock = sock
        self.rate = rate
        self.channels = channels
        self.frame_bytes = 2 * channels
        self._keep = int(rate * keep_seconds) * self.frame_bytes
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._dropped_bytes = 0
        self._t0: float | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.logger = logger
        self.total_bytes = 0

    def start(self) -> "PcmStream":
        self._thread = threading.Thread(target=self._run, daemon=True, name="pcm")
        self._thread.start()
        return self

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                chunk = self.sock.recv(65536)
            except OSError:
                break
            if not chunk:
                break
            with self._lock:
                if self._t0 is None:
                    self._t0 = time.perf_counter()
                self._buf += chunk
                self.total_bytes += len(chunk)
                if len(self._buf) > self._keep:
                    drop = len(self._buf) - self._keep
                    drop -= drop % self.frame_bytes
                    del self._buf[:drop]
                    self._dropped_bytes += drop

    def seconds_captured(self) -> float:
        with self._lock:
            return len(self._buf) / self.frame_bytes / self.rate

    def snapshot(self, seconds: float) -> Slice | None:
        """取最近 ``seconds`` 秒的单声道样本，并给出它对应的时间轴。"""
        want = int(seconds * self.rate) * self.frame_bytes
        with self._lock:
            if len(self._buf) < want or self._t0 is None:
                return None
            data = bytes(self._buf[-want:])
            # 这段数据在总时间轴上的起点
            offset_bytes = self._dropped_bytes + len(self._buf) - want
            start = self._t0 + offset_bytes / self.frame_bytes / self.rate
        samples = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
        if self.channels > 1:
            samples = samples.reshape(-1, self.channels).mean(axis=1)
        end = start + len(samples) / self.rate
        return Slice(samples=samples, start_time=start, end_time=end)

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        try:
            self.sock.close()
        except OSError:
            pass
