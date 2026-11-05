// Voiceover Agent - front end. Plain JS on purpose: no build step, and the
// whole thing has to keep working on a machine that is busy making audio.

const $ = (id) => document.getElementById(id);
const state = {
  video: null, script: null, music: null,
  check: null, jobId: null, poll: null, timeline: null,
};

function fmtTime(seconds) {
  if (seconds === null || seconds === undefined || !isFinite(seconds)) return "";
  seconds = Math.max(0, Math.round(seconds));
  if (seconds < 60) return seconds + " second";
  const m = Math.floor(seconds / 60), s = seconds % 60;
  if (m < 60) return m + " minute" + (s ? " " + s + " second" : "");
  const h = Math.floor(m / 60);
  return h + " ghanta " + (m % 60) + " minute";
}

function clock(seconds) {
  if (!isFinite(seconds)) return "";
  seconds = Math.max(0, Math.round(seconds));
  const m = Math.floor(seconds / 60), s = seconds % 60;
  return m + ":" + String(s).padStart(2, "0");
}

async function post(url, form) {
  const res = await fetch(url, { method: "POST", body: form });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) {}
    throw new Error(detail);
  }
  return res.json();
}

async function get(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(res.statusText);
  return res.json();
}

// ---------------------------------------------------------------- tabs
const pages = { make: "page-make", voice: "page-voice", jobs: "page-jobs" };
function showTab(which) {
  Object.entries(pages).forEach(([key, id]) => {
    $(id).classList.toggle("hidden", key !== which);
    $("tab-" + key).setAttribute("aria-selected", String(key === which));
  });
  if (which === "voice") loadVoice();
  if (which === "jobs") loadJobs();
}
["make", "voice", "jobs"].forEach((k) =>
  $("tab-" + k).addEventListener("click", () => showTab(k)));

// ---------------------------------------------------------------- upload
$("f-script").addEventListener("change", async () => {
  const file = $("f-script").files[0];
  if (file) $("t-script").value = await file.text();
});

async function ensureUploads() {
  const form = new FormData();
  let needed = false;
  const video = $("f-video").files[0];
  const music = $("f-music").files[0];
  if (video && !state.video) { form.append("video", video); needed = true; }
  if (music && !state.music) { form.append("music", music); needed = true; }
  if (!needed) return;
  $("check-status").textContent = "Files upload ho rahi hain...";
  const out = await post("/api/upload", form);
  if (out.video) state.video = out.video;
  if (out.music) state.music = out.music;
}

// ---------------------------------------------------------------- step A
async function runCheck(scriptText) {
  await ensureUploads();
  if (!state.video) throw new Error("Pehle video chunein.");
  if (!scriptText.trim()) throw new Error("Script khali hai.");

  const form = new FormData();
  form.append("video", state.video);
  form.append("script_text", scriptText);
  form.append("draft", String($("s-quality").value === "draft"));
  const data = await post("/api/check", form);
  state.check = data;
  state.script = data.script_path;
  renderCheck(data);
}

function renderCheck(data) {
  const body = $("check-table").querySelector("tbody");
  body.innerHTML = "";
  data.rows.forEach((row) => {
    const tr = document.createElement("tr");
    if (row.cut_words) tr.className = "trouble";
    const advice = row.cut_words
      ? `~${row.cut_words} alfaaz kam karein`
      : (row.extension > 0.05 ? "theek hai (thora barhega)" : "theek hai");
    tr.innerHTML = `
      <td>${row.block + 1}</td>
      <td class="mono small">${row.start.toFixed(1)}&ndash;${row.end.toFixed(1)}s</td>
      <td class="num">${row.words}</td>
      <td class="num mono">${row.estimated.toFixed(1)}s</td>
      <td class="num mono">${row.available.toFixed(1)}s</td>
      <td class="num mono">${row.extension.toFixed(1)}s</td>
      <td class="small">${advice}</td>`;
    body.appendChild(tr);
  });

  const notes = $("check-notes");
  notes.innerHTML = "";
  const totalCut = data.rows.reduce((a, r) => a + r.cut_words, 0);
  const note = document.createElement("div");
  if (totalCut) {
    note.className = "note warn";
    note.textContent = `Kul lagbhag ${totalCut} alfaaz kam karein, warna video `
      + `kaafi kheenchni paregi.`;
  } else {
    note.className = "note ok";
    note.textContent = "Script video mein theek baith rahi hai.";
  }
  notes.appendChild(note);
  (data.warnings || []).forEach((w) => {
    const d = document.createElement("div");
    d.className = "note warn";
    d.textContent = w;
    notes.appendChild(d);
  });

  $("estimate").textContent =
    `Andaazan ${fmtTime(data.estimate_seconds)} lagega (${data.words} alfaaz, `
    + `${data.takes} take har block).`;
  $("card-check").classList.remove("hidden");
  $("check-status").textContent = "";
}

$("btn-check").addEventListener("click", async () => {
  try {
    $("btn-check").disabled = true;
    await runCheck($("t-script").value);
    $("t-script2").value = $("t-script").value;
  } catch (e) {
    $("check-status").textContent = e.message;
  } finally {
    $("btn-check").disabled = false;
  }
});

$("btn-recheck").addEventListener("click", async () => {
  try {
    $("btn-recheck").disabled = true;
    $("t-script").value = $("t-script2").value;
    await runCheck($("t-script2").value);
  } catch (e) {
    $("check-status").textContent = e.message;
  } finally {
    $("btn-recheck").disabled = false;
  }
});

// ---------------------------------------------------------------- step B
$("btn-start").addEventListener("click", async () => {
  if (!state.check) return;
  $("btn-start").disabled = true;
  try {
    const form = new FormData();
    form.append("video", state.video);
    form.append("script", state.script);
    if (state.music) form.append("music", state.music);
    form.append("draft", String($("s-quality").value === "draft"));
    form.append("keep_original_audio", String($("c-keep-audio").checked));
    form.append("burn_subs", String($("c-burn-subs").checked));
    form.append("words", String(state.check.words));
    form.append("total_takes", String(state.check.total_takes));
    form.append("estimate_seconds", String(state.check.estimate_seconds));
    const out = await post("/api/jobs", form);
    watch(out.id);
  } catch (e) {
    $("check-status").textContent = e.message;
  } finally {
    $("btn-start").disabled = false;
  }
});

// ---------------------------------------------------------------- progress
function watch(jobId) {
  state.jobId = jobId;
  $("card-progress").classList.remove("hidden");
  $("card-result").classList.add("hidden");
  if (state.poll) clearInterval(state.poll);
  state.poll = setInterval(tick, 1000);
  tick();
}

async function tick() {
  if (!state.jobId) return;
  let s;
  try { s = await get("/api/jobs/" + state.jobId); } catch (e) { return; }

  // textContent, not innerHTML: these strings come from the server and
  // must never be parsed as markup. Real dash characters, not entities -
  // textContent shows "&mdash;" literally.
  const queued = s.state === "queued";
  $("p-stage").textContent = queued
    ? `Qatar mein hai — number ${s.position}`
    : (s.detail || s.stage || s.state);

  let pct = 0;
  if (s.total_takes && s.done_takes) pct = 55 * (s.done_takes / s.total_takes);
  if (["align", "subtitles", "audio"].includes(s.stage)) pct = 70;
  if (s.stage === "render") pct = 85;
  if (s.stage === "qc") pct = 95;
  if (s.state === "done") pct = 100;
  $("p-bar").style.width = pct.toFixed(0) + "%";

  $("p-elapsed").textContent = s.elapsed ? clock(s.elapsed) : "";
  $("p-remaining").textContent = (s.state === "running" && s.remaining)
    ? "~" + fmtTime(s.remaining) + " baqi" : "";
  $("p-detail").textContent = (s.block && s.blocks)
    ? `Block ${s.block}/${s.blocks}` + (s.takes ? `, take ${s.take}/${s.takes}` : "")
    : "";

  const over = ["done", "failed", "cancelled", "interrupted"].includes(s.state);
  $("btn-cancel").classList.toggle("hidden", over);
  $("btn-resume").classList.toggle("hidden",
    !["failed", "cancelled", "interrupted"].includes(s.state));

  if (over) {
    clearInterval(state.poll);
    state.poll = null;
    if (s.state === "done") showResult(s);
    else {
      $("p-stage").textContent = s.state === "cancelled"
        ? "Job cancel ho gayi."
        : (s.error || s.detail || "Job mukammal nahi hui.");
    }
  }
}

$("btn-cancel").addEventListener("click", async () => {
  if (!state.jobId) return;
  await post("/api/jobs/" + state.jobId + "/cancel", new FormData());
  tick();
});

$("btn-resume").addEventListener("click", async () => {
  if (!state.jobId) return;
  await post("/api/jobs/" + state.jobId + "/resume", new FormData());
  watch(state.jobId);
});

// ---------------------------------------------------------------- result
async function showResult(s) {
  const base = "/api/jobs/" + s.id + "/file/";
  $("player").src = base + "video";
  $("dl-video").href = base + "video";
  $("dl-srt").href = base + "subtitles";
  $("dl-report").href = base + "report";

  const note = $("qc-note");
  note.innerHTML = "";
  const div = document.createElement("div");
  div.className = s.result.qc_passed ? "note ok" : "note warn";
  div.textContent = s.result.qc_passed
    ? "QC pass — file theek lag rahi hai."
    : "QC ne kuch masle bataye hain — report dekh lein.";
  note.appendChild(div);

  const listen = s.result.listen_to || [];
  $("listen-box").classList.toggle("hidden", listen.length === 0);
  const list = $("listen-list");
  list.innerHTML = "";
  listen.forEach((item) => {
    const btn = document.createElement("button");
    btn.className = "listen-item";
    const at = item.at === null || item.at === undefined ? null : item.at;
    btn.innerHTML = `<strong>Block ${item.block + 1}</strong>`
      + `<span>${item.why}</span>`
      + (at !== null ? `<span class="mono" style="margin-left:auto">${clock(at)}</span>` : "");
    btn.addEventListener("click", () => {
      if (at !== null) { $("player").currentTime = at; $("player").play(); }
    });
    list.appendChild(btn);
  });

  try {
    state.timeline = await get("/api/jobs/" + s.id + "/timeline");
    drawStrip(state.timeline);
  } catch (e) { /* timeline is a nice-to-have */ }

  $("card-result").classList.remove("hidden");
  $("card-result").scrollIntoView({ behavior: "smooth", block: "start" });
}

function drawStrip(timeline) {
  const total = timeline.output_duration || 1;
  const strip = $("strip");
  strip.innerHTML = "";

  const row = document.createElement("div");
  row.className = "strip-row";
  row.innerHTML = '<span class="label">Tasveer</span>';
  const track = document.createElement("div");
  track.className = "strip-track";

  timeline.blocks.forEach((b, i) => {
    const seg = document.createElement("div");
    seg.className = "seg scene";
    seg.style.left = (b.out_start / total * 100) + "%";
    seg.style.width = ((b.out_end - b.out_start) / total * 100) + "%";
    seg.title = `Block ${i + 1}: ${b.out_start.toFixed(1)}-${b.out_end.toFixed(1)}s`;
    const lbl = document.createElement("span");
    lbl.className = "seg-label";
    lbl.textContent = i + 1;
    seg.appendChild(lbl);
    track.appendChild(seg);

    if (b.extension > 0.01) {
      const ext = document.createElement("div");
      ext.className = "seg extended";
      const extStart = b.out_end - b.extension;
      ext.style.left = (extStart / total * 100) + "%";
      ext.style.width = (b.extension / total * 100) + "%";
      ext.title = `+${b.extension.toFixed(1)}s barhaya gaya`;
      track.appendChild(ext);
    }
    if (!b.silent && b.voice_end > b.voice_start) {
      const v = document.createElement("div");
      v.className = "seg voice";
      v.style.left = (b.voice_start / total * 100) + "%";
      v.style.width = ((b.voice_end - b.voice_start) / total * 100) + "%";
      v.title = `Awaaz ${b.voice_start.toFixed(1)}-${b.voice_end.toFixed(1)}s`;
      track.appendChild(v);
    }
  });

  row.appendChild(track);
  strip.appendChild(row);

  const legend = document.createElement("div");
  legend.className = "small muted";
  legend.style.marginTop = "6px";
  legend.textContent = `Kul lambai ${total.toFixed(1)}s`
    + (timeline.total_extension > 0.01
       ? ` (${timeline.total_extension.toFixed(1)}s barhayi gayi)` : "");
  strip.appendChild(legend);
}

// ---------------------------------------------------------------- jobs tab
async function loadJobs() {
  const data = await get("/api/jobs");
  const list = $("jobs-list");
  list.innerHTML = "";
  if (!data.jobs.length) {
    list.innerHTML = '<li class="muted">Abhi koi job nahi hai.</li>';
    return;
  }
  data.jobs.forEach((j) => {
    const li = document.createElement("li");
    li.innerHTML = `
      <span class="pill ${j.state}">${j.state}</span>
      <span class="grow"><strong>${j.name}</strong>
        <span class="muted small">${j.created}</span></span>
      <span class="small muted">${j.detail || ""}</span>`;

    if (j.state === "done") {
      const open = document.createElement("button");
      open.className = "ghost small";
      open.textContent = "Kholein";
      open.addEventListener("click", () => { showTab("make"); watch(j.id); });
      li.appendChild(open);
    }
    if (["failed", "cancelled", "interrupted"].includes(j.state)) {
      const again = document.createElement("button");
      again.className = "ghost small";
      again.textContent = "Dobara shuru";
      again.addEventListener("click", async () => {
        await post("/api/jobs/" + j.id + "/resume", new FormData());
        showTab("make"); watch(j.id);
      });
      li.appendChild(again);
    }
    if (["queued", "running"].includes(j.state)) {
      const stop = document.createElement("button");
      stop.className = "danger small";
      stop.textContent = "Cancel";
      stop.addEventListener("click", async () => {
        await post("/api/jobs/" + j.id + "/cancel", new FormData());
        loadJobs();
      });
      li.appendChild(stop);
    }
    list.appendChild(li);
  });
}

// ---------------------------------------------------------------- voice
async function loadVoice() {
  const data = await get("/api/voice");
  const active = data.active;
  if (active) {
    $("voice-active-text").textContent =
      `${active.profile} / ${active.candidate} (${active.set_at} se)`;
    $("voice-active-audio").src =
      "/api/voice/audio?path=" + encodeURIComponent(active.reference);
  } else {
    $("voice-active-text").textContent =
      ".env wali awaaz use ho rahi hai: " + data.fallback;
    $("voice-active-audio").src =
      "/api/voice/audio?path=" + encodeURIComponent(data.fallback);
  }

  const box = $("profile-list");
  box.innerHTML = "";
  if (!data.profiles.length) { box.textContent = "Abhi koi profile nahi hai."; return; }
  data.profiles.forEach((p) => {
    const div = document.createElement("div");
    div.className = "cand";
    div.innerHTML = `<strong>${p.name}</strong>
      <span class="muted small"> &mdash; ${p.created}</span>`;
    p.candidates.forEach((c) => {
      const b = document.createElement("button");
      b.className = "ghost small";
      b.style.margin = "6px 6px 0 0";
      b.textContent = c.name + (c.similarity ? ` (${c.similarity}%)` : "");
      b.addEventListener("click", () => activate(p.name, c.name));
      div.appendChild(b);
    });
    box.appendChild(div);
  });
}

async function activate(profile, candidate) {
  const form = new FormData();
  form.append("profile", profile);
  form.append("candidate", candidate);
  await post("/api/voice/activate", form);
  $("voice-status").textContent = "Active awaaz badal di gayi.";
  loadVoice();
}

$("btn-voice-prepare").addEventListener("click", async () => {
  const file = $("f-voice").files[0];
  if (!file) { $("voice-status").textContent = "Pehle recording chunein."; return; }
  $("btn-voice-prepare").disabled = true;
  $("voice-status").textContent = "Candidates ban rahe hain, thora waqt lagega...";
  try {
    const form = new FormData();
    form.append("recording", file);
    form.append("name", $("t-voice-name").value);
    const profile = await post("/api/voice/prepare", form);
    renderCandidates(profile);
    $("voice-status").textContent = "Ho gaya.";
    loadVoice();
  } catch (e) {
    $("voice-status").textContent = e.message;
  } finally {
    $("btn-voice-prepare").disabled = false;
  }
});

function renderCandidates(profile) {
  const box = $("candidate-list");
  box.innerHTML = "";
  profile.candidates.forEach((c) => {
    const div = document.createElement("div");
    div.className = "cand" + (c.name === profile.best ? " best" : "");
    div.innerHTML = `<strong>${c.name}</strong>
      <span class="muted small"> &mdash; ${c.start}s se ${c.end}s tak</span>
      ${c.similarity ? `<span class="pill done" style="margin-left:8px">${c.similarity}% milti hai</span>` : ""}
      ${c.name === profile.best ? '<span class="pill running" style="margin-left:8px">behtareen</span>' : ""}`;
    if (c.sample) {
      const audio = document.createElement("audio");
      audio.controls = true;
      audio.src = "/api/voice/audio?path=" + encodeURIComponent(c.sample);
      div.appendChild(audio);
    }
    const use = document.createElement("button");
    use.className = "primary small";
    use.style.marginTop = "8px";
    use.textContent = "Yehi use karein";
    use.addEventListener("click", () => activate(profile.name, c.name));
    div.appendChild(use);
    box.appendChild(div);
  });
  $("card-candidates").classList.remove("hidden");
}

showTab("make");
