"""音频对齐：算出「游戏里的歌曲播到第几秒」。

自动打歌最大的坑不是算不出音符时刻，而是**不知道游戏从哪一刻开始播**。
常见做法是在 PC 上放麦克风、或在手机上录音，然后和歌曲原文件做对齐。

做法很土但很稳：两边都压成 ~100Hz 的短时能量包络，做互相关。
包络对音质、音量、房间混响都不敏感，只关心「哪里变响了」。

约定：返回值 ``offset`` = 录音时间 − 歌曲时间。
也就是说，歌曲第 0 秒出现在录音的第 ``-offset`` 秒处。
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

DEFAULT_RATE = 100.0


def load_wav(path: str | Path) -> tuple[np.ndarray, int]:
    """读单声道 float 波形。支持 8/16/32 bit PCM。"""
    p = Path(path)
    with wave.open(str(p), "rb") as f:
        channels = f.getnchannels()
        width = f.getsampwidth()
        rate = f.getframerate()
        frames = f.readframes(f.getnframes())

    if width == 1:
        data = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 2:
        data = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        data = np.frombuffer(frames, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"{p.name} 是 {width * 8} bit PCM，暂不支持（请转成 16 bit）")

    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data.astype(np.float32, copy=False), rate


def envelope(samples: np.ndarray, rate: int, target: float = DEFAULT_RATE) -> tuple[np.ndarray, float]:
    """短时 RMS 能量包络（对数压缩），返回包络与其采样率。"""
    hop = max(1, int(round(rate / target)))
    usable = len(samples) // hop
    if usable < 2:
        raise ValueError("音频太短，无法计算包络")
    frames = samples[: usable * hop].reshape(usable, hop)
    rms = np.sqrt(np.mean(frames.astype(np.float32) ** 2, axis=1))
    env = np.log1p(rms * 300.0)
    return env.astype(np.float32), rate / hop


def _zscore(x: np.ndarray) -> np.ndarray:
    x = x.astype(np.float32)
    mean = float(x.mean())
    std = float(x.std())
    if std < 1e-9:
        return x - mean
    return (x - mean) / std


def estimate_offset(
    song: np.ndarray,
    recording: np.ndarray,
    rate: int,
    *,
    target_rate: float = DEFAULT_RATE,
    max_offset: float | None = None,
    min_overlap: float = 1.0,
) -> dict[str, float]:
    """估计录音相对歌曲的偏移。

    返回 ``{"offset": 秒, "confidence": 0~1, "lag_frames": 帧}``。
    """
    env_a, env_rate_a = envelope(song, rate, target_rate)
    env_b, env_rate_b = envelope(recording, rate, target_rate)
    env_rate = 0.5 * (env_rate_a + env_rate_b)

    a = _zscore(env_a)
    b = _zscore(env_b)

    n = 1 << int(len(a) + len(b) - 1).bit_length()
    fa = np.fft.rfft(a, n)
    fb = np.fft.rfft(b, n)
    corr = np.fft.irfft(fb * np.conj(fa), n)

    lags = np.arange(n)
    lags = np.where(lags < n // 2, lags, lags - n)

    # 只保留有足够重叠的 lag
    overlap = np.where(
        lags >= 0,
        np.minimum(len(b) - lags, len(a)),
        np.minimum(len(b), len(a) + lags),
    )
    valid = (lags >= -(len(a) - 1)) & (lags <= len(b) - 1)
    valid &= overlap * env_rate >= min_overlap
    if max_offset is not None:
        valid &= np.abs(lags) <= max_offset * env_rate
    if not np.any(valid):
        raise ValueError("没有可用的对齐区间，检查录音长度或 max_offset")

    scores = np.where(valid, corr, -np.inf)
    best = int(np.argmax(scores))

    # 抛物线插值，把精度从 10ms 提到 ~2ms
    delta = 0.0
    if 0 < best < n - 1:
        y0, y1, y2 = float(corr[best - 1]), float(corr[best]), float(corr[best + 1])
        denom = y0 - 2.0 * y1 + y2
        if abs(denom) > 1e-12:
            delta = 0.5 * (y0 - y2) / denom
            delta = float(np.clip(delta, -0.5, 0.5))

    lag = float(lags[best]) + delta
    peak = float(corr[best])
    confidence = float(np.clip(peak / (len(a) + len(b)) * 4.0, 0.0, 1.0))
    return {
        "offset": lag / env_rate,
        "confidence": confidence,
        "lag_frames": lag,
        "env_rate": env_rate,
    }


def song_time_at(offset: float, recording_time: float) -> float:
    """把录音时间换算成歌曲时间。"""
    return recording_time - offset
