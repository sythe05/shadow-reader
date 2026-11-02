"""Steps 3-4 (scoring half): compare the Saaras transcript with the expected passage.

Sarvam's speech-to-text returns a transcript (and, depending on model/endpoint, optional timestamps)
but no per-word confidence. Correctness is therefore decided by aligning heard words to expected
words and checking (a) spelling similarity and (b) that each tracked phoneme's grapheme sequence
is still present in the heard word.
"""
from __future__ import annotations

from difflib import SequenceMatcher

from .config import settings
from .phonemes import BY_ID, label_for, normalize, tokenize


def fold(word: str) -> str:
    w = normalize(word).replace("\u200d", "").replace("\u200c", "")
    return w.replace("\u0901", "\u0902").strip().lower()  # chandrabindu ~ anusvara


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def align(expected: list[str], heard: list[str]) -> list[int | None]:
    """Needleman-Wunsch over words. Returns, for each expected word, the heard index or None."""
    n, m = len(expected), len(heard)
    gap = -0.6
    score = [[0.0] * (m + 1) for _ in range(n + 1)]
    back = [[""] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        score[i][0], back[i][0] = i * gap, "u"
    for j in range(1, m + 1):
        score[0][j], back[0][j] = j * gap, "l"
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            diag = score[i - 1][j - 1] + 2.0 * similarity(expected[i - 1], heard[j - 1]) - 0.8
            up, left = score[i - 1][j] + gap, score[i][j - 1] + gap
            best = max(diag, up, left)
            score[i][j] = best
            back[i][j] = "d" if best == diag else ("u" if best == up else "l")
    mapping: list[int | None] = [None] * n
    i, j = n, m
    while i > 0 or j > 0:
        move = back[i][j]
        if move == "d":
            mapping[i - 1] = j - 1
            i, j = i - 1, j - 1
        elif move == "u":
            i -= 1
        else:
            j -= 1
    return mapping


def score(lesson_words: list[dict], transcript: str, timed_words: list[dict], fallback_duration: float) -> dict:
    heard_tokens = [t.text for t in tokenize(normalize(transcript))]
    expected = [fold(w["text"]) for w in lesson_words]
    heard = [fold(h) for h in heard_tokens]
    mapping = align(expected, heard) if heard else [None] * len(expected)

    words_out: list[dict] = []
    observations: list[tuple[str, bool]] = []
    correct = 0
    for w, exp, hi in zip(lesson_words, expected, mapping):
        heard_w = heard[hi] if hi is not None else None
        sim = similarity(exp, heard_w) if heard_w else 0.0
        ph_results = []
        for pid in w["tracked"]:
            ok = bool(heard_w) and sim >= 0.5 and bool(BY_ID[pid].regex.search(heard_w))
            ph_results.append({"id": pid, "label": label_for(pid), "ok": ok})
            observations.append((pid, ok))
        word_ok = sim >= settings.word_match_threshold and all(p["ok"] for p in ph_results)
        correct += int(word_ok)
        words_out.append(
            {
                "index": w["index"],
                "expected": w["text"],
                "heard": heard_tokens[hi] if hi is not None else None,
                "similarity": round(sim, 3),
                "correct": word_ok,
                "slowed": w["slowed"],
                "phonemes": ph_results,
            }
        )

    # Speaking time: prefer word timestamps from Saaras, else the recording length.
    speaking = fallback_duration
    if len(timed_words) >= 2:
        span = timed_words[-1]["end"] - timed_words[0]["start"]
        if span >= 1.0:
            speaking = span
    wcpm = correct / (speaking / 60.0) if speaking > 0 else 0.0
    coverage = len(heard_tokens) / max(1, len(expected))

    return {
        "transcript": transcript,
        "words": words_out,
        "correct": correct,
        "total": len(expected),
        "accuracy": round(correct / max(1, len(expected)), 3),
        "wcpm": round(wcpm, 1),
        "speaking_seconds": round(speaking, 2),
        "coverage": round(coverage, 2),
        "observations": observations,
    }
