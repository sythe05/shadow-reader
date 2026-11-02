"use strict";
const $ = (s) => document.querySelector(s);
const state = { student: null, lesson: null, audio: null, recorder: null, stream: null, chunks: [], raf: 0, startedAt: 0 };

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch (_) {}
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return res.json();
}
const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
const setText = (sel, msg, isErr = false) => { const n = $(sel); n.textContent = msg || ""; n.classList.toggle("error", isErr); };

/* ------------------------------------------------------------------ health */
async function loadHealth() {
  const box = $("#health"); box.replaceChildren();
  try {
    const h = await api("/api/health");
    const pill = (ok, t) => { const p = el("span", "pill " + (ok ? "ok" : "bad"), t); box.append(p); };
    pill(h.neo4j, h.neo4j ? "Neo4j connected" : "Neo4j unreachable");
    pill(h.sarvam_key_set, h.sarvam_key_set ? "Sarvam key set" : "Sarvam key missing");
    if (!h.ffmpeg) box.append(el("span", "pill", "ffmpeg not found (optional)"));
  } catch (e) { box.append(el("span", "pill bad", "Server error: " + e.message)); }
}

/* ------------------------------------------------------------------ student + graph */
async function setStudent(s) {
  state.student = s;
  localStorage.setItem("shadow.student", JSON.stringify(s));
  $("#studentLine").hidden = false;
  $("#studentLine").textContent = `Reading as ${s.name}.`;
  $("#setup").hidden = false;
  await refreshProfile();
}

async function refreshProfile() {
  const g = $("#graph"); g.replaceChildren();
  try {
    const p = await api(`/api/students/${encodeURIComponent(state.student.id)}/profile`);
    if (!p.edges.length) {
      g.append(el("p", "muted small", "No sounds tracked yet. Read a passage and this fills in."));
    }
    p.edges.forEach((e) => {
      const row = el("div", "grow");
      const top = el("div", "top2");
      top.append(el("span", "lbl", e.label), el("span", "muted small", `${e.error_rate.toFixed(2)} · ${e.attempts} tries`));
      const bar = el("div", "bar");
      const fill = el("i", e.error_rate > p.threshold ? "hot" : ""); fill.style.width = `${Math.round(e.error_rate * 100)}%`;
      const tick = el("u"); tick.style.left = `${p.threshold * 100}%`; tick.title = `Slow-down threshold ${p.threshold}`;
      bar.append(fill, tick);
      row.append(top, bar); g.append(row);
    });
    const s = $("#sessions"); s.replaceChildren();
    if (p.sessions.length) {
      s.append(el("h3", null, "Recent sessions"));
      p.sessions.slice(0, 5).forEach((x) => {
        const d = el("div"); d.append(el("span", null, x.passage), el("span", "muted", `${Math.round(x.wcpm)} WCPM`)); s.append(d);
      });
    }
  } catch (e) { g.append(el("p", "error", e.message)); }
}

$("#studentForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  try { await setStudent(await api("/api/students", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: $("#studentName").value }) })); }
  catch (e) { setText("#studentLine", e.message, true); $("#studentLine").hidden = false; }
});

$("#seedRate").addEventListener("input", (e) => { $("#seedOut").textContent = Number(e.target.value).toFixed(2); });
async function seed(pid, rate) {
  await api(`/api/students/${encodeURIComponent(state.student.id)}/seed`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ phoneme_id: pid, error_rate: rate }) });
}
$("#seedBtn").addEventListener("click", async () => { try { await seed($("#seedPhoneme").value, Number($("#seedRate").value)); await refreshProfile(); } catch (e) { alert(e.message); } });
$("#seedExampleBtn").addEventListener("click", async () => { try { await seed("ksha", 0.9); await seed("rha", 0.85); await refreshProfile(); } catch (e) { alert(e.message); } });

/* ------------------------------------------------------------------ lesson */
let customMode = false;
$("#customToggle").addEventListener("click", () => {
  customMode = !customMode;
  $("#customText").hidden = !customMode;
  $("#passageSelect").disabled = customMode;
  $("#customToggle").textContent = customMode ? "Use a built-in passage" : "Paste my own";
});

$("#prepareBtn").addEventListener("click", async () => {
  stopEverything();
  $("#prepareBtn").disabled = true; setText("#prepareStatus", "Checking the graph and synthesising audio…");
  $("#results").hidden = true;
  try {
    const body = { student_id: state.student.id };
    if (customMode) body.text = $("#customText").value; else body.passage_id = $("#passageSelect").value;
    const lesson = await api("/api/lesson", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    state.lesson = lesson; renderLesson(lesson); setText("#prepareStatus", "");
  } catch (e) { setText("#prepareStatus", e.message, true); }
  finally { $("#prepareBtn").disabled = false; }
});

function b64ToBlobUrl(b64, type) {
  const bin = atob(b64); const arr = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
  return URL.createObjectURL(new Blob([arr], { type }));
}

function renderLesson(lesson) {
  $("#stage").hidden = false;
  const box = $("#passage"); box.replaceChildren();
  lesson.words.forEach((w) => {
    const s = el("span", "w" + (w.slowed ? " target" : ""), w.text); s.dataset.i = w.index;
    if (w.slowed) {
      s.append(el("small", null, `×${w.pace.toFixed(2)}`));
      s.title = w.struggles.map((x) => `${x.label}: ${x.error_rate}`).join(", ");
    }
    box.append(s);
  });
  const n = lesson.words.filter((w) => w.slowed).length;
  $("#stageNote").textContent = n
    ? `${n} word${n > 1 ? "s" : ""} slowed for your trouble sounds.`
    : "No sound is above the slow-down line, so this plays at natural pace.";
  if (state.audio) URL.revokeObjectURL(state.audio.src);
  state.audio = new Audio(b64ToBlobUrl(lesson.audio_b64, "audio/wav"));
  setText("#stageStatus", ""); $("#shadowBtn").hidden = false; $("#stopBtn").hidden = true;
}

function highlightLoop() {
  const t = state.audio.currentTime; let active = -1;
  state.lesson.words.forEach((w) => { if (t >= w.start - 0.02) active = w.index; });
  document.querySelectorAll(".w").forEach((n) => n.classList.toggle("active", Number(n.dataset.i) === active && !state.audio.paused));
  if (!state.audio.paused) state.raf = requestAnimationFrame(highlightLoop);
}

function playAudio(onEnd) {
  state.audio.currentTime = 0;
  state.audio.onended = () => { document.querySelectorAll(".w.active").forEach((n) => n.classList.remove("active")); onEnd && onEnd(); };
  return state.audio.play().then(() => highlightLoop());
}

$("#listenBtn").addEventListener("click", () => { stopEverything(); playAudio(); });

function pickMime() {
  const opts = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];
  return opts.find((m) => window.MediaRecorder && MediaRecorder.isTypeSupported(m)) || "";
}

$("#shadowBtn").addEventListener("click", async () => {
  try {
    stopEverything();
    state.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 } });
    const mime = pickMime();
    state.recorder = new MediaRecorder(state.stream, mime ? { mimeType: mime } : undefined);
    state.chunks = [];
    state.recorder.ondataavailable = (e) => { if (e.data.size) state.chunks.push(e.data); };
    state.recorder.onstop = () => submitRecording(state.recorder.mimeType || mime || "audio/webm");
    state.recorder.start(); state.startedAt = performance.now();
    $("#shadowBtn").hidden = true; $("#stopBtn").hidden = false; $("#results").hidden = true;
    setText("#stageStatus", "Recording. Read along a beat behind the voice.");
    await playAudio(() => { setText("#stageStatus", "Finish the last words…"); setTimeout(stopRecording, 2000); });
  } catch (e) { setText("#stageStatus", e.name === "NotAllowedError" ? "Microphone permission was denied." : e.message, true); stopEverything(); }
});
$("#stopBtn").addEventListener("click", stopRecording);

function stopRecording() {
  if (state.recorder && state.recorder.state === "recording") { state.audio.pause(); state.recorder.stop(); }
}
function stopEverything() {
  cancelAnimationFrame(state.raf);
  if (state.audio) { state.audio.pause(); state.audio.onended = null; }
  if (state.recorder && state.recorder.state === "recording") { state.recorder.onstop = null; state.recorder.stop(); }
  if (state.stream) { state.stream.getTracks().forEach((t) => t.stop()); state.stream = null; }
  document.querySelectorAll(".w.active").forEach((n) => n.classList.remove("active"));
  $("#shadowBtn").hidden = false; $("#stopBtn").hidden = true;
}

/* ------------------------------------------------------------------ scoring */
async function submitRecording(mime) {
  const duration = (performance.now() - state.startedAt) / 1000;
  state.stream && state.stream.getTracks().forEach((t) => t.stop()); state.stream = null;
  $("#stopBtn").hidden = true; $("#shadowBtn").hidden = false;
  setText("#stageStatus", "Scoring with Saaras…");
  const ext = mime.includes("mp4") ? "mp4" : mime.includes("ogg") ? "ogg" : "webm";
  const fd = new FormData();
  fd.append("audio", new Blob(state.chunks, { type: mime }), `shadow.${ext}`);
  fd.append("client_duration", duration.toFixed(2));
  try {
    const r = await api(`/api/lessons/${state.lesson.id}/submit`, { method: "POST", body: fd });
    setText("#stageStatus", ""); renderResults(r); await refreshProfile();
  } catch (e) { setText("#stageStatus", e.message, true); }
}

function renderResults(r) {
  $("#results").hidden = false;
  r.words.forEach((w) => {
    const n = document.querySelector(`.w[data-i="${w.index}"]`);
    if (n) { n.classList.remove("ok", "miss"); n.classList.add(w.correct ? "ok" : "miss"); n.title = w.heard ? `Heard: ${w.heard}` : "Not heard"; }
  });
  const stats = $("#stats"); stats.replaceChildren();
  [[`${Math.round(r.accuracy * 100)}%`, `${r.correct} of ${r.total} words correct`], [Math.round(r.wcpm), "words correct per minute"]].forEach(([v, l]) => {
    const s = el("div", "stat"); s.append(el("b", null, v), el("span", null, l)); stats.append(s);
  });
  $("#heard").textContent = r.transcript ? `Saaras heard: ${r.transcript}` : "Saaras heard nothing.";
  const box = $("#changes"); box.replaceChildren();
  if (!r.graph_updated) { box.append(el("p", "error", "Not enough of the passage was heard, so your graph was left unchanged. Try again closer to the microphone.")); return; }
  box.append(el("h2", null, "What changed in your graph"));
  r.changes.forEach((c) => {
    const row = el("div", "change");
    const bar = el("div", "bar"); const fill = el("i", c.after > r.threshold ? "hot" : ""); fill.style.width = `${Math.round(c.after * 100)}%`;
    const tick = el("u"); tick.style.left = `${r.threshold * 100}%`; bar.append(fill, tick);
    const d = c.after - c.before;
    const delta = el("span", "delta " + (d <= 0 ? "up" : "down"), `${c.before.toFixed(2)} → ${c.after.toFixed(2)}`);
    row.append(el("span", "lbl", c.label), bar, delta); box.append(row);
  });
}

/* ------------------------------------------------------------------ boot */
(async function init() {
  loadHealth();
  const [passages, phonemes] = await Promise.all([api("/api/passages"), api("/api/phonemes")]);
  passages.forEach((p) => { const o = el("option", null, p.title); o.value = p.id; $("#passageSelect").append(o); });
  phonemes.forEach((p) => { const o = el("option", null, p.label); o.value = p.id; $("#seedPhoneme").append(o); });
  try {
    const saved = JSON.parse(localStorage.getItem("shadow.student") || "null");
    if (saved) { $("#studentName").value = saved.name; await setStudent(saved); }
  } catch (_) {}
})();
