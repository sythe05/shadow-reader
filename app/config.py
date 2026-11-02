"""All configuration comes from environment variables (loaded from .env)."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _s(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


def _f(name: str, default: float) -> float:
    return float(os.getenv(name, default))


def _i(name: str, default: int) -> int:
    return int(os.getenv(name, default))


@dataclass(frozen=True)
class Settings:
    # Sarvam
    sarvam_api_key: str
    sarvam_base_url: str
    tts_model: str
    tts_speaker: str
    tts_sample_rate: int
    stt_model: str
    stt_mode: str
    language_code: str
    # Neo4j
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: str
    neo4j_database: str
    # Adaptive behaviour
    struggle_threshold: float
    base_pace: float
    slow_min_pace: float
    error_rate_alpha: float
    phoneme_prior: float
    # Scoring / lesson
    word_match_threshold: float
    max_passage_words: int
    target_pad_ms: int
    # Misc
    cache_dir: str


def load() -> Settings:
    return Settings(
        sarvam_api_key=_s("SARVAM_API_KEY", ""),
        sarvam_base_url=_s("SARVAM_BASE_URL", "https://api.sarvam.ai"),
        tts_model=_s("SARVAM_TTS_MODEL", "bulbul:v3"),
        tts_speaker=_s("SARVAM_TTS_SPEAKER", "shubh"),
        tts_sample_rate=_i("SARVAM_TTS_SAMPLE_RATE", 24000),
        stt_model=_s("SARVAM_STT_MODEL", "saaras:v3"),
        stt_mode=_s("SARVAM_STT_MODE", "transcribe"),
        language_code=_s("LANGUAGE_CODE", "hi-IN"),
        neo4j_uri=_s("NEO4J_URI", "bolt://localhost:7687"),
        neo4j_user=_s("NEO4J_USER", "neo4j"),
        neo4j_password=_s("NEO4J_PASSWORD", "shadowreader123"),
        neo4j_database=_s("NEO4J_DATABASE", "neo4j"),
        struggle_threshold=_f("STRUGGLE_THRESHOLD", 0.7),
        base_pace=_f("BASE_PACE", 1.0),
        slow_min_pace=_f("SLOW_MIN_PACE", 0.55),
        error_rate_alpha=_f("ERROR_RATE_ALPHA", 0.12),
        phoneme_prior=_f("PHONEME_PRIOR", 0.5),
        word_match_threshold=_f("WORD_MATCH_THRESHOLD", 0.85),
        max_passage_words=_i("MAX_PASSAGE_WORDS", 40),
        target_pad_ms=_i("TARGET_PAD_MS", 140),
        cache_dir=_s("CACHE_DIR", ".cache"),
    )


settings = load()
