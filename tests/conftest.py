import io
import math
import struct
import sys
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.graph import next_error_rate  # noqa: E402
from app.config import settings  # noqa: E402


def sine_wav(seconds: float, sr: int = 24000, freq: float = 220.0) -> bytes:
    n = int(seconds * sr)
    frames = b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * freq * i / sr))) for i in range(n))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(frames)
    return buf.getvalue()


class FakeSarvam:
    """Duration of fake speech = 0.08 s per character / pace, so slowed words are longer."""

    def __init__(self, transcript: str = ""):
        self.calls: list[tuple[str, float]] = []
        self.transcript = transcript

    def tts(self, text, pace):
        self.calls.append((text, pace))
        return sine_wav(0.08 * len(text) / pace)

    def stt(self, audio, filename, content_type):
        return {"transcript": self.transcript, "timed_words": [], "raw": {}}


class FakeGraph:
    """In-memory stand-in with the same interface as app.graph.Graph."""

    def __init__(self):
        self.students, self.edges, self.sessions = {}, {}, []

    def ping(self): return True
    def init_schema(self): pass

    def ensure_student(self, sid, name):
        self.students.setdefault(sid, name)
        return {"id": sid, "name": self.students[sid]}

    def student_exists(self, sid): return sid in self.students

    def struggles(self, sid, threshold):
        return sorted(
            [{"id": p, "label": p, "error_rate": e["error_rate"], "attempts": e["attempts"]}
             for (s, p), e in self.edges.items() if s == sid and e["error_rate"] > threshold],
            key=lambda r: -r["error_rate"],
        )

    def profile(self, sid):
        return [{"id": p, "label": p, "ipa": "", **e} for (s, p), e in self.edges.items() if s == sid]

    def record_observation(self, sid, pid, success):
        e = self.edges.setdefault((sid, pid), {"error_rate": settings.phoneme_prior, "attempts": 0, "successes": 0})
        before = e["error_rate"]
        e["error_rate"] = next_error_rate(before, e["attempts"], success, settings.error_rate_alpha)
        e["attempts"] += 1
        e["successes"] += int(success)
        return {"before": before, "after": e["error_rate"], "attempts": e["attempts"]}

    def seed(self, sid, pid, rate):
        e = self.edges.setdefault((sid, pid), {"error_rate": rate, "attempts": 0, "successes": 0})
        e["error_rate"] = rate
        return {"id": pid, "error_rate": rate}

    def log_session(self, sid, passage, wcpm, acc): self.sessions.append((sid, passage, wcpm, acc))
    def recent_sessions(self, sid, limit=10): return []


@pytest.fixture
def fake_graph(): return FakeGraph()
