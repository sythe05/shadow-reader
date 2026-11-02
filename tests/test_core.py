import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import audio, scoring
from app.config import settings
from app.graph import next_error_rate
from app.lesson import build_lesson, slow_pace
from app.phonemes import normalize, phonemes_in, tokenize
from tests.conftest import FakeSarvam, sine_wav


# ------------------------------------------------------------------ phonemes
def test_detects_ksha_and_rha():
    assert phonemes_in("पक्षी") == ["ksha"]
    assert phonemes_in("पेड़") == ["rha"]
    assert "ssa" not in phonemes_in("पक्षी")          # ष inside क्ष is not counted twice
    assert phonemes_in("ष") == ["ssa"]


def test_precomposed_nukta_is_equivalent():
    precomposed = "पे\u095c"                            # ड़ as a single code point
    assert phonemes_in(precomposed) == ["rha"]
    assert normalize(precomposed) == "पेड\u093c"


def test_tokenize_keeps_matras_and_drops_danda():
    toks = tokenize(normalize("पेड़ पर एक पक्षी बैठा है।"))
    assert [t.text for t in toks] == ["पेड़", "पर", "एक", "पक्षी", "बैठा", "है"]


# ------------------------------------------------------------------ graph maths
def test_error_rate_moves_toward_outcome_and_decays_slowly():
    e = 0.9
    for n in range(30):
        e = next_error_rate(e, n + 5, True, 0.12)
    assert e < 0.05
    assert next_error_rate(0.5, 0, False, 0.12) == pytest.approx(0.75)   # first miss: running average
    assert next_error_rate(0.5, 0, True, 0.12) == pytest.approx(0.25)


# ------------------------------------------------------------------ lesson / audio
def test_slow_pace_mapping():
    assert slow_pace(1.0) == settings.slow_min_pace
    assert slow_pace(0.0) == settings.base_pace
    assert settings.slow_min_pace < slow_pace(0.8) < settings.base_pace


def test_only_struggle_words_are_slowed_and_timings_are_ordered():
    fake = FakeSarvam()
    lesson = build_lesson("पेड़ पर एक पक्षी बैठा है।", [{"id": "ksha", "label": "क्ष", "error_rate": 0.9}], fake)
    slowed = [w["text"] for w in lesson["words"] if w["slowed"]]
    assert slowed == ["पक्षी"]
    assert ("पक्षी", slow_pace(0.9)) in fake.calls
    assert all(p == settings.base_pace for t, p in fake.calls if t != "पक्षी")
    starts = [w["start"] for w in lesson["words"]]
    assert starts == sorted(starts)
    assert lesson["words"][-1]["end"] <= lesson["duration"] + 0.01


def test_no_struggles_means_single_natural_segment():
    fake = FakeSarvam()
    lesson = build_lesson("पेड़ पर एक पक्षी बैठा है।", [], fake)
    assert len(fake.calls) == 1 and fake.calls[0][1] == settings.base_pace
    assert not any(w["slowed"] for w in lesson["words"])


def test_passage_too_long_is_rejected():
    with pytest.raises(ValueError):
        build_lesson(" ".join(["शब्द"] * (settings.max_passage_words + 1)), [], FakeSarvam())


def test_wav_roundtrip():
    samples, sr = audio.decode_wav(sine_wav(0.5))
    assert sr == 24000 and len(samples) == 12000
    assert audio.wav_duration(audio.encode_wav(samples, sr)) == pytest.approx(0.5)


# ------------------------------------------------------------------ scoring
WORDS = [
    {"index": 0, "text": "पेड़", "slowed": False, "tracked": ["rha"]},
    {"index": 1, "text": "पर", "slowed": False, "tracked": []},
    {"index": 2, "text": "पक्षी", "slowed": True, "tracked": ["ksha"]},
]


def test_perfect_reading():
    r = scoring.score(WORDS, "पेड़ पर पक्षी", [], 6.0)
    assert r["correct"] == 3 and r["accuracy"] == 1.0
    assert r["wcpm"] == pytest.approx(30.0)
    assert all(ok for _, ok in r["observations"])


def test_dropped_phoneme_fails_word_and_phoneme():
    r = scoring.score(WORDS, "पेड़ पर पखी", [], 6.0)    # क्ष -> ख
    by_word = {w["expected"]: w for w in r["words"]}
    assert not by_word["पक्षी"]["correct"]
    assert ("ksha", False) in r["observations"] and ("rha", True) in r["observations"]


def test_missing_nukta_counts_as_phoneme_miss_even_if_spelling_is_close():
    r = scoring.score(WORDS, "पेड पर पक्षी", [], 6.0)
    assert ("rha", False) in r["observations"]
    assert r["correct"] == 2


def test_alignment_survives_extra_and_missing_words():
    m = scoring.align(["a", "बड़ा", "पेड़"], ["अरे", "a", "पेड़"])
    assert m[0] == 1 and m[2] == 2


# ------------------------------------------------------------------ API end-to-end (fakes)
def make_client(fake_graph, fake_sarvam):
    from app.main import app
    from app.services import get_graph, get_sarvam

    app.dependency_overrides[get_graph] = lambda: fake_graph
    app.dependency_overrides[get_sarvam] = lambda: fake_sarvam
    return TestClient(app)


def test_full_loop_graph_evolves(fake_graph, monkeypatch):
    monkeypatch.setattr(audio, "transcode_to_wav16k", lambda raw, suffix: sine_wav(8.0, 16000))
    fake_sarvam = FakeSarvam(transcript="पेड़ पर एक पक्षी बैठा है")
    client = make_client(fake_graph, fake_sarvam)

    sid = client.post("/api/students", json={"name": "Japika"}).json()["id"]
    client.post(f"/api/students/{sid}/seed", json={"phoneme_id": "ksha", "error_rate": 0.9})
    client.post(f"/api/students/{sid}/seed", json={"phoneme_id": "rha", "error_rate": 0.85})

    lesson = client.post("/api/lesson", json={"student_id": sid, "text": "पेड़ पर एक पक्षी बैठा है।"}).json()
    assert {w["text"] for w in lesson["words"] if w["slowed"]} == {"पेड़", "पक्षी"}

    res = client.post(
        f"/api/lessons/{lesson['id']}/submit",
        files={"audio": ("rec.webm", b"x" * 5000, "audio/webm")},
        data={"client_duration": "8"},
    ).json()
    assert res["graph_updated"] and res["accuracy"] == 1.0
    changes = {c["id"]: c for c in res["changes"]}
    assert changes["ksha"]["after"] < changes["ksha"]["before"] == 0.9

    # Repeated clean reads eventually drop ksha under the threshold -> the next lesson stops slowing it.
    for _ in range(25):
        lesson = client.post("/api/lesson", json={"student_id": sid, "text": "पेड़ पर एक पक्षी बैठा है।"}).json()
        client.post(f"/api/lessons/{lesson['id']}/submit", files={"audio": ("r.webm", b"x" * 5000, "audio/webm")}, data={"client_duration": "8"})
    lesson = client.post("/api/lesson", json={"student_id": sid, "text": "पेड़ पर एक पक्षी बैठा है।"}).json()
    assert not any(w["slowed"] for w in lesson["words"])


def test_silence_does_not_punish_the_student(fake_graph, monkeypatch):
    monkeypatch.setattr(audio, "transcode_to_wav16k", lambda raw, suffix: sine_wav(5.0, 16000))
    client = make_client(fake_graph, FakeSarvam(transcript=""))
    sid = client.post("/api/students", json={"name": "Asha"}).json()["id"]
    client.post(f"/api/students/{sid}/seed", json={"phoneme_id": "ksha", "error_rate": 0.9})
    lesson = client.post("/api/lesson", json={"student_id": sid, "text": "एक पक्षी"}).json()
    res = client.post(f"/api/lessons/{lesson['id']}/submit", files={"audio": ("r.webm", b"x" * 5000, "audio/webm")}).json()
    assert res["graph_updated"] is False
    assert fake_graph.edges[(sid, "ksha")]["error_rate"] == 0.9
