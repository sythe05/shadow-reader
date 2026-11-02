# छाया पाठ — Shadow Reader

Adaptive shadow reading for Hindi fluency. A Neo4j graph remembers which sounds a student
struggles with. Sarvam **Bulbul** reads each passage at natural pace but slows down only on
the words containing those sounds. Sarvam **Saaras** listens to the student shadowing the voice,
and the graph is updated so the slowdowns fade as the student improves.

```
            ┌────────────────────────── Neo4j ──────────────────────────┐
            │ (Student)-[STRUGGLES_WITH {error_rate, attempts}]->(Phoneme) │
            └───────▲──────────────────────────────────────┬─────────────┘
   1. query >0.7    │                                      │ 4. update error_rate
                    │                                      │
 browser ──► FastAPI ──► lesson.py ──► Bulbul (TTS) ──► stitched WAV + word timings ──► browser plays it
   ▲                                                                                      │ student shadows,
   └──── result + graph delta ◄── scoring.py ◄── Saaras (STT) ◄── recording ◄─────────────┘ mic records (3)
```

## Quick start

You need Python 3.10+, Docker (for Neo4j) and a Sarvam AI API key. `ffmpeg` is optional but recommended.

```bash
# 1. Start Neo4j (browser UI at http://localhost:7474)
docker compose up -d

# 2. Install dependencies
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 3. Add your key
#    .env is already in this folder; open it and fill in SARVAM_API_KEY.
#    (If it is missing: cp .env.example .env)

# 4. Run
uvicorn app.main:app --reload
# open http://127.0.0.1:8000
```

No Docker? Use a free Neo4j Aura instance and set `NEO4J_URI` (e.g. `neo4j+s://xxxx.databases.neo4j.io`),
`NEO4J_USER` and `NEO4J_PASSWORD` in `.env`.

The page header shows three status pills (Neo4j, Sarvam key, ffmpeg) so you can see what is missing.

### Trying the exact example from the spec

1. Enter a name and click **Continue**.
2. Open **Set a starting point** in the right panel and click **Load क्ष and ड़ example**
   (sets `ksha = 0.90`, `rha = 0.85`).
3. Pick *पेड़ पर पक्षी* and click **Prepare lesson**. पक्षी and पेड़ are underlined with their pace
   (for example ×0.62); every other word is at natural pace.
4. Put on **headphones**, click **Start shadow reading**, and read along a beat behind the voice.
5. Read the result: per-word correctness, WCPM, and what changed in the graph.

## How each workflow step is implemented

| Step | What happens | Code |
|---|---|---|
| 1. Context retrieval | Cypher `MATCH (s)-[r:STRUGGLES_WITH]->(p) WHERE r.error_rate > $threshold` | `Graph.struggles` in `app/graph.py` |
| 2. Dynamic synthesis | Words containing a struggled phoneme are synthesised alone at a slower pace, the rest at base pace, then stitched | `app/lesson.py` |
| 3. Shadow reading | Browser plays the lesson while `MediaRecorder` records; audio goes to Saaras | `static/app.js`, `Sarvam.stt` |
| 4. Delta + graph evolution | Heard words are aligned to expected words; each phoneme occurrence is a hit or miss that moves `error_rate` | `app/scoring.py`, `Graph.record_observation` |

### Pace and error rate

- Slowed pace = `BASE_PACE − (BASE_PACE − SLOW_MIN_PACE) × error_rate`, so 0.7 → about ×0.69 and 1.0 → ×0.55.
- After each occurrence: `error_rate ← error_rate × (1 − α) + α × (0 if hit else 1)`,
  with `α = max(ERROR_RATE_ALPHA, 1 / (attempts + 2))` (a running average for the first attempts, then a slow moving average).
- A phoneme seen for the first time starts at `PHONEME_PRIOR` (0.5). One miss takes it to 0.75, which is over the
  0.7 line, so the next lesson slows that word.
- Once `error_rate` falls under `STRUGGLE_THRESHOLD`, Bulbul stops slowing those words. Because the threshold is a hard
  cut, the pace jumps back to natural speed at that point. Lower `STRUGGLE_THRESHOLD` (for example 0.5) for a longer tail of mild slowdown.
- If under 30 % of the passage is heard, the graph is not updated, so silence or a dead mic never counts as a failure.

## Things you should know (read before trusting the scores)

1. **Bulbul has no per-word SSML.** Sarvam's TTS API exposes a global `pace` (0.5 to 2.0 on v3) and I found no
   SSML support in its docs. So instead of injecting tags, the app synthesises the target words as separate
   segments at a slower pace and joins them with short pauses. This gives the same effect, and as a bonus
   exact timings for every word (used for the live highlight). The cost is slightly less seamless prosody at the joins.
2. **Saaras does not return per-word confidence.** The spec's "confidence scores for target words" are not available
   from Sarvam's speech-to-text. A word counts as correct if it aligns to the expected word with
   spelling similarity ≥ `WORD_MATCH_THRESHOLD` **and** every tracked phoneme's grapheme sequence (for example `ड़`,
   `क्ष`) is present in what Saaras transcribed.
3. **ASR is lenient.** Language models tend to "autocorrect" toward the right spelling, so a mispronounced word can still
   be transcribed correctly. Treat `error_rate` as a conservative signal. Setting `SARVAM_STT_MODE=verbatim`
   may help; try it on real student audio.
4. **Word timestamps are not guaranteed.** The app requests them and uses them for WCPM when Saaras returns
   word-level data. Otherwise WCPM uses the recording length. The Sarvam docs I found disagree on whether
   word-level timestamps exist for the synchronous endpoint, so the code handles both.
5. **Use headphones.** Otherwise the mic hears the model's voice and Saaras transcribes that too.
6. **30-second limit.** Saaras' REST endpoint accepts 30 s of audio, so passages are capped at `MAX_PASSAGE_WORDS` (40).
7. **WCPM includes slowed words**, so it dips when many words are slowed, then rises as the graph improves.
8. Phoneme detection is spelling-based (`app/phonemes.py`, 12 sounds). Add rows to `CATALOG` to track more.

## Configuration (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `SARVAM_API_KEY` | – | Your Sarvam key (sent as `api-subscription-key`) |
| `SARVAM_TTS_MODEL` / `SARVAM_TTS_SPEAKER` | `bulbul:v3` / `shubh` | Bulbul model and voice |
| `SARVAM_STT_MODEL` / `SARVAM_STT_MODE` | `saaras:v3` / `transcribe` | Saaras model and output mode |
| `LANGUAGE_CODE` | `hi-IN` | Used for both TTS and STT |
| `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` | `bolt://localhost:7687` / `neo4j` / `shadowreader123` | Database. The compose file uses the same password |
| `STRUGGLE_THRESHOLD` | `0.7` | Edges above this are slowed |
| `BASE_PACE` / `SLOW_MIN_PACE` | `1.0` / `0.55` | Natural pace and the slowest pace |
| `ERROR_RATE_ALPHA` | `0.12` | How quickly the graph reacts after the first few attempts |
| `PHONEME_PRIOR` | `0.5` | Starting error_rate for a newly seen phoneme |
| `WORD_MATCH_THRESHOLD` | `0.85` | Minimum similarity to call a word correct |
| `MAX_PASSAGE_WORDS` | `40` | Passage length cap |

Bulbul results are cached on disk in `.cache/tts`, so repeated normal-pace segments cost nothing after the first call.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Neo4j, key and ffmpeg status |
| GET | `/api/passages`, `/api/phonemes` | Built-in passages, tracked phonemes |
| POST | `/api/students` `{name}` | Create or fetch a student |
| GET | `/api/students/{id}/profile` | All `STRUGGLES_WITH` edges and recent sessions |
| POST | `/api/students/{id}/seed` `{phoneme_id, error_rate}` | Manually set a starting error rate |
| POST | `/api/lesson` `{student_id, passage_id \| text}` | Steps 1 and 2: returns WAV (base64) and word timings |
| POST | `/api/lessons/{id}/submit` (multipart `audio`) | Steps 3 and 4: scores and updates the graph |

Interactive docs: http://127.0.0.1:8000/docs

## Inspecting the graph

In the Neo4j browser (http://localhost:7474):

```cypher
MATCH (s:Student)-[r:STRUGGLES_WITH]->(p:Phoneme)
RETURN s, r, p;

// Trouble spots over the line
MATCH (s:Student {id: 'japika'})-[r:STRUGGLES_WITH]->(p:Phoneme)
WHERE r.error_rate > 0.7
RETURN p.label, r.error_rate ORDER BY r.error_rate DESC;
```

## Project layout

```
app/
  main.py        FastAPI routes
  graph.py       Neo4j queries (context retrieval, graph evolution)
  lesson.py      Segment planning, Bulbul synthesis, WAV stitching, word timings
  scoring.py     Word alignment, phoneme checks, WCPM
  sarvam.py      Bulbul and Saaras HTTP client (retries, disk cache)
  phonemes.py    Hindi phoneme catalogue and tokeniser
  audio.py       WAV helpers, optional ffmpeg transcoding
  passages.py    Built-in practice passages
static/          Single-page UI (index.html, app.js, style.css)
tests/           Offline tests (fake Sarvam + in-memory graph)
docker-compose.yml, .env.example, requirements.txt
```

## Tests

```bash
pytest -q
```

The tests use a fake Sarvam client and an in-memory graph, so they need no keys and no database. They cover
phoneme detection (including precomposed nukta letters), pace mapping, segment timing, scoring and alignment,
and the full loop where repeated clean reads push an error rate under the threshold so the next lesson stops slowing the word.
The Cypher queries and the live Sarvam calls are **not** exercised by the tests; they follow Sarvam's published
API reference, but check them against your account on first run.

## Troubleshooting

- **"Neo4j unreachable"**: run `docker compose ps`, and make sure `NEO4J_PASSWORD` in `.env` matches the one Neo4j was first started with
  (to reset, `docker compose down -v`).
- **Bulbul 4xx error**: the message from Sarvam is shown in the UI. Check `SARVAM_TTS_SPEAKER` is valid for your model.
- **Saaras rejects the recording**: install `ffmpeg` so recordings are converted to 16 kHz WAV before upload.
- **Microphone blocked**: browsers only allow the mic on `localhost` or HTTPS.
