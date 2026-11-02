"""FastAPI app: ties together Neo4j (context), Bulbul (audio), Saaras (scoring)."""
from __future__ import annotations

import hashlib
import logging
import re
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import audio, scoring
from .audio import AudioError
from .config import settings
from .lesson import build_lesson
from .passages import PASSAGES
from .phonemes import BY_ID, CATALOG
from .sarvam import SarvamError
from .services import get_graph, get_sarvam

log = logging.getLogger("shadow-reader")
STATIC = Path(__file__).resolve().parent.parent / "static"

# Lessons are kept server-side so scoring can't be spoofed by the client.
LESSONS: "OrderedDict[str, dict]" = OrderedDict()
MAX_LESSONS = 200
MIN_COVERAGE = 0.3  # below this we assume the student barely read and skip graph updates


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        graph = get_graph()
        graph.ping()  # fails fast if the database is down
        graph.init_schema()
        log.info("Neo4j schema ready")
    except Exception as e:  # keep the server up so /api/health can explain what's wrong
        log.warning("Neo4j not ready: %s", e)
    yield


app = FastAPI(title="Shadow Reader", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


# ---------------------------------------------------------------------- models
class StudentIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class SeedIn(BaseModel):
    phoneme_id: str
    error_rate: float = Field(ge=0.0, le=1.0)


class LessonIn(BaseModel):
    student_id: str
    passage_id: str | None = None
    text: str | None = None


def student_id_for(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "s-" + hashlib.sha1(name.encode()).hexdigest()[:8]


def graph_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except HTTPException:
        raise
    except LookupError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(503, f"Neo4j error: {e}. Is the database running and are the NEO4J_* values in .env correct?")


# ---------------------------------------------------------------------- routes
@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health(graph=Depends(get_graph)):
    neo4j_ok, neo4j_err = True, None
    try:
        graph.ping()
    except Exception as e:
        neo4j_ok, neo4j_err = False, str(e)
    return {
        "neo4j": neo4j_ok,
        "neo4j_error": neo4j_err,
        "sarvam_key_set": bool(settings.sarvam_api_key),
        "ffmpeg": audio.shutil.which("ffmpeg") is not None,
        "threshold": settings.struggle_threshold,
        "tts_model": settings.tts_model,
        "stt_model": settings.stt_model,
    }


@app.get("/api/passages")
def passages():
    return PASSAGES


@app.get("/api/phonemes")
def phonemes():
    return [{"id": p.id, "label": p.label, "ipa": p.ipa, "tip": p.tip} for p in CATALOG]


@app.post("/api/students")
def create_student(body: StudentIn, graph=Depends(get_graph)):
    name = body.name.strip()
    return graph_call(graph.ensure_student, student_id_for(name), name)


@app.get("/api/students/{student_id}/profile")
def profile(student_id: str, graph=Depends(get_graph)):
    if not graph_call(graph.student_exists, student_id):
        raise HTTPException(404, "Unknown student")
    return {
        "threshold": settings.struggle_threshold,
        "edges": graph_call(graph.profile, student_id),
        "sessions": graph_call(graph.recent_sessions, student_id),
    }


@app.post("/api/students/{student_id}/seed")
def seed(student_id: str, body: SeedIn, graph=Depends(get_graph)):
    """Manually set a starting error_rate (e.g. from a dialect-based prior)."""
    if body.phoneme_id not in BY_ID:
        raise HTTPException(400, f"Unknown phoneme '{body.phoneme_id}'")
    return graph_call(graph.seed, student_id, body.phoneme_id, body.error_rate)


@app.post("/api/lesson")
def create_lesson(body: LessonIn, graph=Depends(get_graph), sarvam=Depends(get_sarvam)):
    if body.text and body.text.strip():
        text, title = body.text, body.text.strip()[:40]
    else:
        match = next((p for p in PASSAGES if p["id"] == body.passage_id), None)
        if not match:
            raise HTTPException(400, "Choose a passage or paste your own text.")
        text, title = match["text"], match["title"]

    if not graph_call(graph.student_exists, body.student_id):
        raise HTTPException(404, "Unknown student")
    # Step 1: (Student)-[STRUGGLES_WITH {error_rate > threshold}]->(Phoneme)
    struggles = graph_call(graph.struggles, body.student_id, settings.struggle_threshold)

    try:
        lesson = build_lesson(text, struggles, sarvam)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except (SarvamError, AudioError) as e:
        raise HTTPException(502, str(e))

    LESSONS[lesson["id"]] = {
        "student_id": body.student_id,
        "title": title,
        "words": lesson["words"],
    }
    while len(LESSONS) > MAX_LESSONS:
        LESSONS.popitem(last=False)
    return lesson


@app.post("/api/lessons/{lesson_id}/submit")
def submit(
    lesson_id: str,
    audio_file: UploadFile = File(..., alias="audio"),
    client_duration: float = Form(0.0),
    graph=Depends(get_graph),
    sarvam=Depends(get_sarvam),
):
    record = LESSONS.get(lesson_id)
    if not record:
        raise HTTPException(404, "This lesson has expired. Prepare it again.")
    raw = audio_file.file.read()
    if len(raw) < 2000:
        raise HTTPException(400, "The recording is empty. Check microphone permission and try again.")

    suffix = Path(audio_file.filename or "rec.webm").suffix or ".webm"
    ctype = audio_file.content_type or "audio/webm"
    try:
        wav = audio.transcode_to_wav16k(raw, suffix)
    except AudioError as e:
        raise HTTPException(400, str(e))
    send, name, mime = (wav, "shadow.wav", "audio/wav") if wav else (raw, f"shadow{suffix}", ctype)
    duration = audio.wav_duration(wav) if wav else client_duration
    if duration > 30.5:
        raise HTTPException(400, f"Recording is {duration:.0f}s; Saaras' REST endpoint accepts up to 30 s. Use a shorter passage.")

    try:
        stt = sarvam.stt(send, name, mime)
    except SarvamError as e:
        raise HTTPException(502, str(e))

    result = scoring.score(record["words"], stt["transcript"], stt["timed_words"], duration)

    # Step 4: delta calculation + graph evolution (only if we actually heard the student read)
    changes: dict[str, dict] = {}
    updated = result["coverage"] >= MIN_COVERAGE and bool(stt["transcript"])
    if updated:
        for pid, ok in result["observations"]:
            r = graph_call(graph.record_observation, record["student_id"], pid, ok)
            ch = changes.setdefault(
                pid,
                {"id": pid, "label": BY_ID[pid].label, "before": r["before"], "hits": 0, "misses": 0},
            )
            ch["after"] = r["after"]
            ch["hits" if ok else "misses"] += 1
        graph_call(graph.log_session, record["student_id"], record["title"], result["wcpm"], result["accuracy"])

    result.pop("observations")
    result["graph_updated"] = updated
    result["changes"] = list(changes.values())
    result["threshold"] = settings.struggle_threshold
    return result
