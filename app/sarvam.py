"""Thin client for Sarvam AI: Bulbul (text-to-speech) and Saaras (speech-to-text)."""
from __future__ import annotations

import base64
import hashlib
import time
from pathlib import Path

import httpx

from .config import settings


class SarvamError(RuntimeError):
    pass


_RETRY_STATUS = {429, 500, 502, 503, 504}


class SarvamClient:
    def __init__(self) -> None:
        self._http = httpx.Client(
            base_url=settings.sarvam_base_url,
            timeout=httpx.Timeout(60.0, connect=10.0),
        )
        self._cache = Path(settings.cache_dir) / "tts"
        self._cache.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ plumbing
    def _headers(self) -> dict[str, str]:
        if not settings.sarvam_api_key:
            raise SarvamError("SARVAM_API_KEY is empty. Add your key to .env and restart the server.")
        return {"api-subscription-key": settings.sarvam_api_key}

    def _post(self, path: str, **kwargs) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                resp = self._http.post(path, headers=self._headers(), **kwargs)
            except httpx.TransportError as e:
                last_exc = e
                time.sleep(1.0 * (attempt + 1))
                continue
            if resp.status_code in _RETRY_STATUS and attempt < 2:
                time.sleep(1.0 * (attempt + 1))
                continue
            return resp
        raise SarvamError(f"Could not reach Sarvam AI: {last_exc}")

    # ------------------------------------------------------------------ Bulbul
    def tts(self, text: str, pace: float) -> bytes:
        """Synthesize `text` at `pace` and return WAV bytes. Results are cached on disk."""
        pace = round(min(2.0, max(0.5, pace)), 2)
        key = hashlib.sha1(
            f"{settings.tts_model}|{settings.tts_speaker}|{settings.tts_sample_rate}|{settings.language_code}|{pace}|{text}".encode()
        ).hexdigest()
        cached = self._cache / f"{key}.wav"
        if cached.exists():
            return cached.read_bytes()

        payload = {
            "text": text,
            "target_language_code": settings.language_code,
            "model": settings.tts_model,
            "speaker": settings.tts_speaker,
            "pace": pace,
            "speech_sample_rate": settings.tts_sample_rate,
        }
        resp = self._post("/text-to-speech", json=payload)
        if resp.status_code != 200:
            raise SarvamError(f"Bulbul TTS failed ({resp.status_code}): {resp.text[:300]}")
        body = resp.json()
        audios = body.get("audios") or ([body["audio"]] if body.get("audio") else [])
        if not audios:
            raise SarvamError("Bulbul TTS returned no audio.")
        wav = base64.b64decode(audios[0])
        cached.write_bytes(wav)
        return wav

    # ------------------------------------------------------------------ Saaras
    def stt(self, audio: bytes, filename: str, content_type: str) -> dict:
        """Transcribe audio. Returns {"transcript": str, "timed_words": list[dict], "raw": dict}."""
        data = {"model": settings.stt_model, "language_code": settings.language_code, "with_timestamps": "true"}
        if settings.stt_model.startswith("saaras"):
            data["mode"] = settings.stt_mode

        resp = self._post("/speech-to-text", data=data, files={"file": (filename, audio, content_type)})
        if resp.status_code in (400, 422) and "with_timestamps" in data:
            # Some model/endpoint combinations reject the timestamps flag; retry without it.
            data.pop("with_timestamps")
            resp = self._post("/speech-to-text", data=data, files={"file": (filename, audio, content_type)})
        if resp.status_code != 200:
            raise SarvamError(f"Saaras STT failed ({resp.status_code}): {resp.text[:300]}")

        body = resp.json()
        transcript = (body.get("transcript") or "").strip()
        ts = body.get("timestamps") or {}
        words, starts, ends = ts.get("words") or [], ts.get("start_time_seconds") or [], ts.get("end_time_seconds") or []
        timed = (
            [{"word": w, "start": float(s), "end": float(e)} for w, s, e in zip(words, starts, ends)]
            if words and len(words) == len(starts) == len(ends)
            else []
        )
        return {"transcript": transcript, "timed_words": timed, "raw": body}
