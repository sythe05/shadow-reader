"""Step 2: dynamic audio synthesis.

Bulbul takes plain text and a global `pace`; it has no per-word SSML control. So the passage is
split into segments: runs of normal words are synthesised at the base pace, and every word that
contains a phoneme the student struggles with is synthesised on its own at a slower pace. The WAV
segments are then stitched together with short pauses, which also gives exact timings for every word.
"""
from __future__ import annotations

import base64
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import numpy as np

from . import audio
from .config import settings
from .phonemes import BY_ID, Token, label_for, normalize, phonemes_in, tokenize

_END_PUNCT = re.compile(r"[।.?!॥]")
_MID_PUNCT = re.compile(r"[,;:]")


@dataclass
class Segment:
    kind: str  # "normal" | "target"
    text: str
    token_indices: list[int] = field(default_factory=list)
    pace: float = 1.0


def slow_pace(error_rate: float) -> float:
    """error_rate 0.7 -> mildly slowed, 1.0 -> SLOW_MIN_PACE."""
    span = settings.base_pace - settings.slow_min_pace
    pace = settings.base_pace - span * max(0.0, min(1.0, error_rate))
    return round(max(settings.slow_min_pace, min(settings.base_pace, pace)), 2)


def plan_segments(text: str, tokens: list[Token], targets: dict[int, float]) -> list[Segment]:
    """Group tokens into segments. `targets` maps token index -> pace for that word."""
    segments: list[Segment] = []
    buf: list[Token] = []

    def flush() -> None:
        if not buf:
            return
        raw = text[buf[0].start : buf[-1].end]
        segments.append(Segment("normal", raw, [t.index for t in buf], settings.base_pace))
        buf.clear()

    for tok in tokens:
        if tok.index in targets:
            flush()
            segments.append(Segment("target", tok.text, [tok.index], targets[tok.index]))
        else:
            buf.append(tok)
    flush()
    return segments


def _pause_ms(text: str, prev: Segment, cur: Segment, tokens: list[Token]) -> int:
    gap = text[tokens[prev.token_indices[-1]].end : tokens[cur.token_indices[0]].start]
    ms = settings.target_pad_ms if "target" in (prev.kind, cur.kind) else 40
    if _END_PUNCT.search(gap):
        ms += 350
    elif _MID_PUNCT.search(gap):
        ms += 180
    return ms


def build_lesson(passage_text: str, struggles: list[dict], sarvam) -> dict:
    """Return the public lesson dict (includes base64 WAV audio and per-word timings)."""
    text = normalize(passage_text).strip()
    tokens = tokenize(text)
    if not tokens:
        raise ValueError("The passage has no readable words.")
    if len(tokens) > settings.max_passage_words:
        raise ValueError(
            f"Passage has {len(tokens)} words; the limit is {settings.max_passage_words} "
            "(Saaras' REST endpoint accepts at most 30 s of audio)."
        )

    rates = {s["id"]: float(s["error_rate"]) for s in struggles}
    word_info: dict[int, dict] = {}
    targets: dict[int, float] = {}
    for tok in tokens:
        hits = [p for p in phonemes_in(tok.text) if p in rates]
        if hits:
            pace = slow_pace(max(rates[p] for p in hits))
            targets[tok.index] = pace
            word_info[tok.index] = {
                "pace": pace,
                "phonemes": [{"id": p, "label": label_for(p), "error_rate": round(rates[p], 3)} for p in hits],
            }

    segments = plan_segments(text, tokens, targets)
    with ThreadPoolExecutor(max_workers=4) as pool:
        wavs = list(pool.map(lambda s: sarvam.tts(s.text, s.pace), segments))

    chunks: list[np.ndarray] = []
    seg_times: list[tuple[float, float]] = []
    sr: int | None = None
    cursor = 0
    for i, (seg, wav) in enumerate(zip(segments, wavs)):
        samples, this_sr = audio.decode_wav(wav)
        if sr is None:
            sr = this_sr
        elif this_sr != sr:
            raise audio.AudioError("Segments came back at different sample rates.")
        if i > 0:
            gap = audio.silence(_pause_ms(text, segments[i - 1], seg, tokens), sr)
            chunks.append(gap)
            cursor += len(gap)
        samples = audio.fade_edges(samples, sr)
        chunks.append(samples)
        seg_times.append((cursor / sr, (cursor + len(samples)) / sr))
        cursor += len(samples)

    full = np.concatenate(chunks)
    wav_bytes = audio.encode_wav(full, sr)

    # Word timings: targets own their segment; normal segments are split by character length.
    timings: dict[int, tuple[float, float]] = {}
    for seg, (t0, t1) in zip(segments, seg_times):
        total = sum(len(tokens[i].text) for i in seg.token_indices) or 1
        pos = t0
        for i in seg.token_indices:
            dur = (t1 - t0) * len(tokens[i].text) / total
            timings[i] = (pos, pos + dur)
            pos += dur

    words = []
    for tok in tokens:
        info = word_info.get(tok.index)
        words.append(
            {
                "index": tok.index,
                "text": tok.text,
                "start": round(timings[tok.index][0], 3),
                "end": round(timings[tok.index][1], 3),
                "slowed": info is not None,
                "pace": info["pace"] if info else settings.base_pace,
                "tracked": phonemes_in(tok.text),
                "struggles": info["phonemes"] if info else [],
            }
        )

    return {
        "id": uuid.uuid4().hex,
        "text": text,
        "duration": round(len(full) / sr, 3),
        "words": words,
        "struggles": [
            {"id": s["id"], "label": s.get("label") or BY_ID[s["id"]].label, "error_rate": round(float(s["error_rate"]), 3)}
            for s in struggles
        ],
        "audio_b64": base64.b64encode(wav_bytes).decode("ascii"),
    }
