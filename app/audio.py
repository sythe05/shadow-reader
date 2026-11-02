"""WAV helpers: decode, stitch segments with pauses/fades, optional ffmpeg transcoding."""
from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np


class AudioError(RuntimeError):
    pass


def decode_wav(data: bytes) -> tuple[np.ndarray, int]:
    """Return (mono int16 samples, sample_rate)."""
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            ch, width, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            raw = w.readframes(n)
    except (wave.Error, EOFError) as e:
        raise AudioError(f"Could not read WAV audio returned by the TTS service: {e}") from e
    if width != 2:
        raise AudioError(f"Unsupported WAV sample width ({width * 8}-bit); expected 16-bit PCM.")
    arr = np.frombuffer(raw, dtype="<i2")
    if ch > 1:
        arr = arr.reshape(-1, ch).mean(axis=1).astype(np.int16)
    return arr.copy(), sr


def encode_wav(samples: np.ndarray, sr: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(samples.astype("<i2").tobytes())
    return buf.getvalue()


def silence(ms: int, sr: int) -> np.ndarray:
    return np.zeros(int(sr * ms / 1000), dtype=np.int16)


def fade_edges(samples: np.ndarray, sr: int, ms: int = 8) -> np.ndarray:
    n = min(int(sr * ms / 1000), len(samples) // 2)
    if n <= 0:
        return samples
    x = samples.astype(np.float32)
    ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
    x[:n] *= ramp
    x[-n:] *= ramp[::-1]
    return x.astype(np.int16)


def wav_duration(data: bytes) -> float:
    with wave.open(io.BytesIO(data), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def transcode_to_wav16k(data: bytes, suffix: str = ".webm") -> bytes | None:
    """Convert browser audio (webm/mp4/ogg) to 16 kHz mono WAV. Returns None if ffmpeg is unavailable."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = Path(tmp) / f"in{suffix}", Path(tmp) / "out.wav"
        src.write_bytes(data)
        proc = subprocess.run(
            [ffmpeg, "-v", "error", "-y", "-i", str(src), "-ac", "1", "-ar", "16000", str(dst)],
            capture_output=True,
        )
        if proc.returncode != 0 or not dst.exists():
            raise AudioError("ffmpeg could not decode the recording: " + proc.stderr.decode(errors="replace")[:300])
        return dst.read_bytes()
