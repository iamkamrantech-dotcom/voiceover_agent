# CLAUDE.md — Voiceover Agent

## 1. Project

A Python agent that takes:

- **an image-based video** (slideshow style: hard cuts or fades between images, sometimes a slow zoom/pan — Ken Burns effect), and
- **an English script** (plain text),

and produces a **YouTube-ready video narrated in a client's cloned voice**.

The narration must fit each image **perfectly**: the right sentence lands on the right image, nothing is cut off mid-word, no long dead air, no rushing. The target quality bar is a **professional video editor with 15+ years of experience** — if a human editor would notice a sync problem, the agent must notice it too and fix it.

### Hard constraint: 100% free and local

No paid APIs. No cloud services. No per-minute billing. Everything runs on the user's own machine with open-source / free tools.

| Job | Tool | Licence |
|---|---|---|
| Voice cloning + TTS | **Chatterbox TTS** (Resemble AI) — zero-shot cloning from a short reference audio | MIT |
| Word timings, subtitles, QC | **faster-whisper** | MIT |
| Scene detection, audio processing, rendering | **FFmpeg**, **OpenCV**, **numpy** | free |
| Web app (later phase) | **FastAPI** | MIT |

Script-to-image matching uses **no paid AI**:

1. If the script has `Scene 1:` / `Scene 2:` style markers → use them.
2. Else if the script has one paragraph per image (paragraph count ≈ image/scene count) → use paragraphs.
3. Else → **time-based split**: distribute the script across detected scenes proportional to each scene's duration.

### `reference/` folder

If a `reference/` folder exists, it holds an **earlier version of this project that used ElevenLabs**. Use it as a guide for structure, prompts and pipeline ideas — but **replace every ElevenLabs call with Chatterbox**. Never add a paid API back in, even if the reference code does it.

> As of project setup (2026-11-05) there is **no** `reference/` folder in this repo.

---

## 2. Working rules (these are not optional)

1. **Work in small phases.** One phase = one coherent piece of the pipeline.
2. **Show a plan before coding.** Every phase starts with a short plan the user approves. No surprise refactors.
3. **One focused module per job**, all inside `agent/`. A module does one thing. If a file starts doing two jobs, split it.
4. **All settings come from `.env`** and are loaded into a single `Config` dataclass (`agent/config.py`). No magic numbers scattered in the code, no hardcoded paths.
5. **Every phase has a test**, run on **generated test videos** (built by a helper that makes slideshows from coloured/sample images) — not on a file the user has to supply by hand.
6. **Everything user-facing is clear, concise English, sentence case.** The dashboard, log lines, the script check table, the QC summary, CLI output and error messages. Example: `log.user("Detecting scenes...")`. No emoji, no exclamation marks, no filler. Errors say what went wrong and what to do: `"No scenes found. Run scene detection first."`
   > **Changed in phase 8A.** User-facing text used to be Roman Urdu; 261 strings across 31 files were converted. Commits before `8A` read differently — that is history, not a style to copy.
7. **Never claim a phase works until its test has actually been run** and the output inspected.

---

## 3. Planned structure

```
Voiceover_Agent/
├── CLAUDE.md
├── .env                  # real settings (gitignored)
├── .env.example          # template, committed
├── requirements.txt
├── venv/                 # Python 3.11 (gitignored)
├── agent/
│   ├── __init__.py
│   ├── config.py         # [done] Config dataclass, loads .env
│   ├── log.py            # [done] plain user log + English debug log
│   ├── scenes.py         # [done] cuts & fades, frame-accurate
│   ├── script_parser.py  # [done] markers / paragraphs / time-split (DP)
│   ├── voice/
│   │   ├── base.py           # [done] Synthesizer/Converter/Verifier + factory
│   │   ├── chatterbox_synth.py # [done] chunked synthesis, natural pauses
│   │   ├── mock_synth.py     # [done] fake tones for fast pipeline tests
│   │   └── verify.py         # [done] ECAPA + faster-whisper gate
│   ├── voice_stage.py    # [done] 3-pass, memory-safe, cached, best-of-N
│   ├── align.py          # [done] fit voice to scenes, plan the timeline
│   ├── video_graph.py    # [done] THE filter graph; preview + render share it
│   ├── preview.py        # [done] fast 480p draft
│   ├── render.py         # [done] final quality, stream-copy when possible
│   ├── audio_master.py   # [done] voice chain, ducked music, 2-pass loudnorm
│   ├── subtitles.py      # [done] SRT, sentence breaks, no overlap
│   ├── qc.py             # [done] measures the finished file
│   ├── pipeline.py       # [done] the whole job + script check + finish_job
│   ├── revise.py         # [done] re-do ONE block, keep everything else
│   ├── versions.py       # [done] v1/v2/v3 history of one job
│   ├── waveform.py       # [done] peaks for the timeline, cached
│   ├── settings_schema.py # [done] every setting, its help and its default
│   ├── jobs.py           # [done] queue, progress, cancel, resume, per-job log
│   └── voice_profiles.py # [done] client voice candidates + history
├── server.py             # [done] FastAPI web app + SSE
├── web-ui/               # [done] React + TS + Vite + Tailwind source
├── web/dist/             # [done] built dashboard, COMMITTED on purpose
├── tools/node/           # portable Node, build tool only (gitignored)
├── voices/               # client voice profiles (gitignored)
├── start.bat             # [done] double-click to run everything
├── run.py                # [done] CLI: full job, or --check-script
├── run_preview.py        # [done] CLI: preview only
├── tests/
│   ├── make_test_videos.py  # [done] generates cuts/fades/zoom + truth JSON
│   ├── media/               # generated test videos (gitignored)
│   ├── test_voice.py        # [done]
│   ├── test_scenes.py       # [done] 21/21 checks
│   ├── test_script_parser.py # [done] 17/17 checks
│   ├── test_voice_stage.py  # [done] 21/21, mock + real run
│   ├── test_align.py        # [done] 16/16, plain-freeze path
│   ├── test_final.py        # [done] 56/57, mastering/subs/living hold/QC (see §4g)
│   ├── test_server.py       # [done] 45/45, queue/cancel/resume/settings
│   ├── test_revise.py       # [done] 39/39, one block redone, cache untouched
│   └── scripts/             # sample scripts for end-to-end runs
├── input/                # user's video + script + voice sample
└── outputs/              # rendered results (gitignored)
```

---

## 4. Machine this runs on (checked 2026-11-05)

- **OS:** Windows 11 Pro (10.0.26300), 64-bit
- **CPU:** Intel Core i5-8365U @ 1.60 GHz — 4 cores / 8 threads
- **RAM:** 15.8 GB total
- **GPU:** Intel UHD Graphics 620 (integrated) — **no NVIDIA GPU, no CUDA**
- **Disk:** C: ~176 GB free
- **Python:** 3.12.10 (kept) + **3.11.9** installed for this project
- **FFmpeg/ffprobe:** 9.0.2 (Gyan full build, winget)
- **Git:** 2.56.0

### Consequence: everything runs on CPU

- Install the **CPU build of PyTorch** (`--index-url https://download.pytorch.org/whl/cpu`). Do **not** install CUDA wheels — they would be a ~2.5 GB download that cannot be used here.
- `DEVICE=cpu` in `.env`. Chatterbox and faster-whisper must both be given `device="cpu"`.
- faster-whisper on CPU: use `compute_type="int8"` and a small model (`base` or `small`). `large-v3` is too slow on 4 cores. Measured: `small.en` transcribes at **~0.7× real time** (faster than real time) with accurate word timestamps — cheap enough to run on every scene as a QC pass.
- **torch requires the MSVC runtime.** A bare Windows install may not have it: `winget install -e --id Microsoft.VCRedist.2015+.x64` (needs admin). Without it `import torch` dies with `OSError: [WinError 126] ... c10.dll`.
- Model weights live in `~/.cache/huggingface` and total **~7.2 GB** for standard + Turbo + `small.en`. Keep them cached; re-downloading Turbo took ~47 minutes on this connection.
- **For the client's voice, use the STANDARD model, not Turbo** — see §4b. Turbo is 3.5× faster but sounds measurably less like the client. Speed figures below still apply.
- Speed measured on this machine (2026-11-05, 8.5 s of speech, 63 s voice reference):

  | Variant | Generate | Audio out | Speed |
  |---|---|---|---|
  | `ChatterboxTTS` (standard) | 93.1 s | 6.0 s | **15.5× slower than real time** |
  | `ChatterboxTurboTTS` | 21.3 s | 5.1 s | **4.2× slower than real time** |

  Turbo is ~3.7× faster for the same sentence and both transcribed back word-perfect. Import it as `from chatterbox.tts_turbo import ChatterboxTurboTTS` — it is **not** exported from `chatterbox/__init__.py`.
- **Turbo's API differs**: it ignores `exaggeration`, `cfg_weight` and `min_p` (logs a warning and drops them), and it **asserts the reference clip is longer than 5 s**. Config must not assume those knobs work on Turbo.
- Both variants output **24 kHz mono float32** — resample to 44.1 kHz at the render stage.
- **Chatterbox on CPU is still slow** — budget ~4× real time with Turbo (a 10-minute narration ≈ 40 minutes). So:
  - synthesise **per scene/chunk**, not the whole script at once;
  - **cache** generated audio on disk keyed by (text + voice sample + settings) so a re-run never re-synthesises unchanged lines;
  - always show progress in the log, e.g. `"Scene 3 of 12: generating the voice..."`;
  - keep a `FAST_TEST=true` mode in `.env` that uses tiny clips so tests don't take an hour.

---

## 4b. Voice matching — the winning setup (measured 2026-11-05)

Run `tools/voice_experiment.py` to reproduce. Similarity = cosine of SpeechBrain
ECAPA speaker embeddings vs the **full** client recording, as a percentage.

**Winner of this round: `standard` engine + `exaggeration=0.5`, `cfg_weight=0.5`.** (The reference was later changed from `ref_2` to `ref6_3` — see §4b-2.)

| # | Setup | Similarity | Words | Gen |
|---|---|---|---|---|
| 1 | VC on standard ref2 (0.5/0.5) | 78.0% | OK | 110.1 s |
| 2 | **standard, ref2, 0.5/0.5** | **77.9%** | OK | **75.0 s** |
| 3 | VC on standard ref2 (0.6/0.3) | 77.4% | OK | 123.9 s |
| 4 | standard, ref2, 0.6/0.3 | 74.9% | OK | 84.9 s |
| 5 | standard, ref2, 0.4/0.3 | 74.4% | OK | 83.6 s |
| 6 | turbo, ref2 | 73.1% | OK | 24.9 s |
| 7 | turbo, ref1 | 72.1% | OK | 30.6 s |
| 8 | turbo, ref3 | 67.3% | OK | 22.2 s |

How to read the numbers:

- **~90%** = two clips of the *same speaker* from different parts of one recording (`ref_1` vs `ref_2`/`ref_3` scored 89.2% / 92.9%). **This is the realistic ceiling**, not 96%.
- **96%** = a clip scored against the recording it was cut from. Upper bound only; ignore it as a target.
- **78%** = our best clone. Still roughly **12 points short** of a real same-speaker match.

Conclusions:

1. **Standard beats Turbo by ~5 points** on voice likeness. Since the client's voice is the whole point, take the 3.5× speed hit.
2. **`ChatterboxVC` is not worth it** — +0.1 points for +35 s per clip, i.e. noise. Keep `TTS_USE_VC=false`.
3. **Reference choice matters** (`ref_2` 73.1% vs `ref_3` 67.3% on Turbo) — so pick the reference deliberately, do not grab the first 15 seconds.
4. `exaggeration/cfg_weight` of 0.5/0.5 beat both 0.4/0.3 and 0.6/0.3 by ~3 points.

**Caveat:** each setup was generated **once**, and Chatterbox sampling is stochastic (`temperature=0.8`). The ~5-point Standard-vs-Turbo gap is almost certainly real; the 0.1-point gap at the top is definitely not. Repeat runs are needed before trusting any sub-2-point difference.

---

## 4b-2. Experiment 2 — reference window and best-of-N (2026-11-05)

Reproduce with `tools/prepare_voice6.py` then `tools/voice_experiment2.py`.

**Step 1 — reference window.** New candidates (`ref6_1..4`) are 10 s clips chosen so their *first 6 seconds* are continuous, clean, on-onset, pitch-stable speech, because that is the only part the standard speaker encoder reads. All five generated at the **same seed (1234)** for a fair comparison:

| Reference | Similarity | From |
|---|---|---|
| **ref6_3** | **85.1%** | 29.8–39.8 s |
| ref6_4 | 80.7% | 44.2–54.2 s |
| ref6_1 | 80.0% | 0.5–10.5 s |
| ref_2 (old winner) | 79.3% | 14.5–28.5 s |
| ref6_2 | 79.2% | 13.5–23.5 s |

**Step 2 — best-of-N on `ref6_3`.** Same text, same reference, four different seeds:

| Seed | Similarity |
|---|---|
| 404 | 82.2% |
| 202 | 77.2% |
| 303 | 76.4% |
| 101 | 73.8% |

**min 73.8% · max 82.2% · mean 77.4% · spread 8.4 points**

| Strategy | Expected | Gain | Extra time/line |
|---|---|---|---|
| best-of-1 | 77.4% | — | — |
| best-of-2 | 79.6% | +2.2 | +70 s |
| **best-of-3** | **81.0%** | **+3.6** | **+141 s** |
| best-of-4 | 82.2% | +4.8 | +211 s |

### Read these two steps together — step 1 is NOT conclusive

Run-to-run spread on one fixed reference is **8.4 points**, which is *larger* than the 5.9-point spread between the five different references at a fixed seed. Those step-1 numbers are one sample each, so **the reference ranking is confounded by seed luck and is not established**. `ref6_3`'s own five samples (85.1, 82.2, 77.2, 76.4, 73.8) average **78.9%** — statistically indistinguishable from `ref_2`'s 79.3%.

Honest conclusions:

1. **Best-of-N is the only proven lever here.** It is measured within a single reference, so sampling noise cannot explain it. Use `TTS_BEST_OF_N=3`.
2. **Choosing the reference by its first 6 seconds is well-motivated but unproven.** `ref6_3` is the default because it produced the best single clip and its selection criteria match how the model actually reads the prompt — not because it beat `ref_2` by a trustworthy margin.
3. **85.1% is a best-of-5 result, not a typical one.** A single take from `ref6_3` averages ~79%. Do not quote 85% as the expected quality.
4. To settle the reference question properly, run 4–5 seeds × the top 2 references (~12 min on this CPU) and compare the means.

### Levers still untried

- **RVC** trained on a longer recording — see §4c. Most likely route past ~85%.
- A longer, cleaner source recording than the current 62.7 s.
- Re-tuning `exaggeration`/`cfg_weight` on the new reference; the 0.5/0.5 win came from experiment 1 on `ref_2` at a single seed each, so it carries the same confound.

---

## 4c. The voice stage must be swappable (two slots, not one)

**An RVC voice-conversion stage, trained on a longer client recording, is planned.** RVC learns the client's timbre from minutes of audio instead of zero-shot cloning from 6 seconds, so it is the most likely route to close the remaining gap to a real same-speaker match. Nothing in the pipeline may assume Chatterbox is the only way audio gets made.

So the voice stage is **two independent slots**, not one:

```
text ──▶ [ SYNTHESIZER ]──▶ speech ──▶ [ CONVERTER (optional) ]──▶ final audio
          Chatterbox std                 none (default)
          Chatterbox turbo               ChatterboxVC
          (any future TTS)               RVC  <-- planned
```

Rules that keep the slots swappable:

1. **Two protocols in `agent/`**, each one small:
   - `Synthesizer`: `synthesize(text: str, out_path: Path) -> SynthResult` — anything that turns text into a wav file.
   - `VoiceConverter`: `convert(in_path: Path, out_path: Path) -> Path` — anything that changes the voice of an existing wav. **May be `None`.**
2. **Both are chosen from `.env`** (`TTS_ENGINE`, `VOICE_CONVERTER`) through the `Config` dataclass and built by a factory. No module imports a concrete engine directly; the pipeline only ever sees the protocol.
3. **The rest of the pipeline only knows about wav files**, never about Chatterbox types, tensors or model objects. Scene fitting, timing, subtitles and render must work unchanged if the synthesizer is replaced.
4. **The converter is optional and order-fixed**: synthesize first, convert second. A converter must not change the word timing, or alignment done before it becomes invalid — re-run the faster-whisper timing pass *after* conversion.
5. **Quality gates are engine-agnostic.** `tools/voice_similarity.py` (ECAPA) and the faster-whisper word check score a wav file, so they work on any engine's output and give a directly comparable number. Every new engine gets scored the same way before it is adopted.
6. **One model resident at a time.** Load, use, free with `gc.collect()`. RVC must not be held in memory alongside Chatterbox.

Practical note for RVC: it is a converter, not a synthesizer — it still needs speech as input. Chatterbox stays in the pipeline as the thing that produces words and prosody; RVC would only restyle the timbre.

---

## 4d. Scene detection (`agent/scenes.py`) — how it works and why

Two passes:

1. **Coarse, ~10 fps.** Each sampled frame is compared with one **~0.5 s earlier**, never with its neighbour. Frame-to-frame difference cannot see a crossfade — across 40 ms an 0.8 s fade moves ~5% — but across 0.5 s it is unmistakable. The distance mixes a **colour-histogram** term (blind to zoom and pan, so Ken Burns does not fire) with a **coarse pixel** term on a 32×18 grayscale (catches cuts between images sharing a palette).
2. **Refine, full frame rate.** For each candidate region, take a clean frame just before (**A**) and just after (**B**), then for every frame measure how far it has travelled from A toward B:

   `alpha = dot(frame − A, B − A) / |B − A|²`

   `alpha` runs 0 → 1 across the transition. **A hard cut jumps 0 → 1 in one frame; a crossfade climbs gradually** — so the *shape* of alpha identifies the kind, and the frame where it crosses 0.5 is a frame-accurate midpoint.

Scene boundary convention: a **cut** ends the scene on the cut frame; a **fade** ends it at the **midpoint**, so scenes stay contiguous with no gaps. `hold_end` is the **last clean frame before the outgoing transition** — for a fade that is the frame before the blend starts. **Freeze on `hold_end` when stretching a scene, never on a blended frame.**

Measured on the generated test videos (`tests/test_scenes.py`, **21/21 pass**): hard cuts exact to the frame on both the plain and the Ken-Burns video, fade midpoints within 1 frame, zero false transitions from zoom.

---

## 4e. Script splitting and the voice stage

### `agent/script_parser.py`

Three methods, chosen in order: **markers** (`Scene 1:`, `[Scene 2]`, `Image 3 -`) → **paragraphs** (only when paragraph count equals scene count) → **time-based DP**.

The DP splits the script into sentences (long ones broken at commas, then hard-wrapped), then fills `dp[k][i]` = cheapest way to cover the first `i` sentences with `k` scenes, where a group's cost is `(speaking_time − screen_time)²` and speaking time is `words / NARRATION_WPS`. Consecutive-only assignment means **order can never go backwards**. Each scene gets at least one sentence whenever there are enough to go round.

A scene with no text of its own **joins the previous block** so narration continues over it; leading scenes with no text **stay silent**, since there is nothing before them to continue. Each block carries `text`, `scene_ids`, `start`, `end`, `prev_text`, `next_text`.

Warns when the script needs more than `SCRIPT_OVERFLOW_RATIO` × the video's length at 2.5 words/second.

### `agent/voice_stage.py` — three passes, one model at a time

| Pass | Loads | Does |
|---|---|---|
| 1 | synthesizer | N takes per block, `seed = TTS_SEED + take`, each cached, then **freed** |
| 2 | ECAPA + whisper | score every take, keep the best take **whose words are correct**, keep its word timings, then **freed** |
| 3 | synthesizer, then verifier | blocks with no correct take get fresh seeds (`+1000 × round`), up to `TTS_RETRY_ROUNDS` |

Cache key = sha1 of text + reference + engine + exaggeration + cfg_weight + temperature + chunking + seed, so **nothing unchanged is ever re-synthesised**.

Chatterbox degrades on long text, so `chatterbox_synth.py` never hands over a whole block: it splits into ≤`TTS_MAX_CHUNK_CHARS` pieces, generates each separately, and joins them with **0.30 s after a full stop, 0.15 s after a comma**, then trims silence **at the block edges only** — pauses inside the block are deliberate.

`mock_synth.py` emits one tone per word plus a sidecar JSON of exact word timings, and `MockVerifier` reads that back. Whisper cannot transcribe tones, so without this the mock path could not exercise pass 2 — with it, the full orchestration (caching, best-of-N, retries, timings) is testable in **0.2 s** instead of hours.

If no take ever passes the word check, the closest-sounding take is kept and a loud warning tells the user to listen to it. Dropping the block silently would be worse.

### ECAPA similarity is duration-sensitive — do not gate short blocks

Measured by truncating **one fixed clip** (`BEST_NEW.wav`, approved by the client) to different lengths and re-scoring it against the full recording:

| Clip length | Similarity |
|---|---|
| 1.0 s | 50.0% |
| 1.8 s | 70.7% |
| 2.5 s | 68.5% |
| 3.5 s | 78.8% |
| 5.0 s | 85.4% |

**Same audio, same voice.** The score tracks duration, not quality — ECAPA simply has too little to work with on a short clip. Consequences baked into the code:

- `MIN_SIMILARITY` is applied **only** to blocks of at least `MIN_SIMILARITY_SECONDS` (3.5 s). Shorter blocks still get a score, flagged `similarity_reliable=False`, but never fail on it.
- **Never compare similarity across blocks of different lengths** — a short block will always look worse.
- Best-of-N ranking *within* a block stays valid: all takes of one block are about the same length, so the comparison is fair.
- Any future quality report must show block duration next to similarity, or the number misleads.

---

## 4f. Alignment (`agent/align.py`) and preview (`agent/preview.py`)

Each block's voice gets a window: its scenes, minus `LEAD_IN` (breath) at the start and `TAIL_OUT` at the end (`TAIL_OUT_FINAL` on the last block), with at least `BLOCK_GAP` of silence before the next block speaks.

**A short voice is never slowed to fill the window.** Silence is fine; stretched speech is not.

When the voice is too long, three fixes in order — cheapest and least audible first:

| | Fix | Limit |
|---|---|---|
| a | **Tighten internal pauses** using word timings | never below 0.18 s after a sentence, 0.10 s after a comma |
| b | **Speed the voice up**, pitch preserved | `MAX_SPEEDUP` = 1.08 (rubberband if available, else atempo) |
| c | **Extend the video**: slow up to `hold_end`, then freeze on it | `MAX_VIDEO_SLOWDOWN` = 1.15, freeze rounded to whole frames |

Word timings are rebuilt after every change, so subtitles stay exact. Every later block shifts by the extension accumulated before it.

### Two bugs this phase, both worth remembering

**1. The freeze was silently dropped on every hard cut.** For a fade, `split_at` (= `hold_end` + 1 frame) sits inside the block, so the renderer splits it into a slowed/frozen part plus a normal-speed transition tail. For a **hard cut**, `hold_end` is the block's last frame, so `split_at == src_end` and there is no tail — and the code had tied *retiming* to the existence of that tail. Result: hard-cut blocks got no slowdown and no freeze at all. **Retiming always applies to the clean part; the split is only about preserving a transition tail.**

**2. A test that passed for the wrong reason.** The duration check used the *container* duration, which is as long as its longest stream — so a too-short video stream was masked by the full-length audio track, and the check passed while the video was visibly wrong. It now counts **video packets** (`nb_read_packets`) instead. When a timing test passes, confirm it is measuring the stream you think it is.

`split_at` and the outgoing transition kind are stored **on the plan**, not re-derived in the renderer, so the two cannot drift apart.

Tests: `tests/test_align.py`, **16/16** across all three videos — no overlap, `BLOCK_GAP` respected, voice inside its segment, video duration within 1 frame, word timings consistent with the audio, and frozen frames extracted from the rendered preview matching the `hold_end` frame (MAD < 3.4) while being measurably *further* from the blended fade midpoint (margin +30.4).

---

## 4g. Final output, script check and QC

### One graph, two renders

`agent/video_graph.py` is the **only** place a Timeline becomes a filter graph. `preview.py` (480p draft) and `render.py` (final) both call it, differing only in scale and encode settings. A draft that proves sync is worthless if the final render re-times differently — and duplicated graph logic already caused two bugs in §4f, so it is not duplicated again.

`render.py` **stream-copies** the video when no block is re-timed and subtitles are not burned in: nothing needs changing, so re-encoding would only throw quality away.

### Living hold

A freeze held perfectly still reads as a stall. Once a hold exceeds `HOLD_MOTION_MIN_SECONDS` (0.5 s) the frozen frame gets a smoothstep push-in of at most `HOLD_ZOOM_MAX` (4%), built as `trim` one frame → `loop` → `zoompan`.

Where the move ends depends on what follows: into a **hard cut or the end of the video** it keeps pushing in; into a **fade** it is triangular and eases back to exactly 1.0, because the dissolve must begin from the same framing or it visibly jumps. Measured: 4.6 mean-abs-diff across the hold→fade boundary, versus 12.6–14.7 of total travel during the hold itself.

### Script check — run it first, always

`run.py --check-script` loads **no models** and finishes in seconds. It reports per block: words, estimated speech at `NARRATION_WPS`, available time, predicted extension, and how many words to cut. It assumes only the 8% speed-up can help (pause tightening can't be known without audio), so it is deliberately slightly pessimistic.

**This exists because voice generation costs hours on this CPU.** Finding out a script is too long afterwards is the most expensive mistake available. Exit code 3 means some block needs cutting.

### Mastering and QC

Voice chain is light on purpose — high-pass 75 Hz, −2.5 dB at 250 Hz, +2 dB at 3 kHz, de-esser, 2.5:1 compressor. **Two-pass** `loudnorm` to −14 LUFS / −1.5 dBTP; one pass guesses from a running window and misses. Music is looped, faded, set by `MUSIC_DB` and ducked with `sidechaincompress` keyed off the voice.

QC measures the finished mp4, never the plan: loudness, true peak, clipping, duration by **video packet count**, and word drift.

**Word drift gates on the median.** faster-whisper's own timestamps jitter 50–150 ms, so a per-word 0.1 s gate would report Whisper's precision as a defect. Systematic drift — a wrong stretch factor, a bad offset — moves *every* word, which the median catches; outliers are listed as "listen and check" instead of failing. Mock runs emit tones, not speech, so the check reports "not applicable" rather than failing.

**Silence is only a problem inside a block's voice span.** Gaps *between* blocks are by design (§4f never stretches a short voice to fill its scene). An early version flagged those designed gaps because the silence merely touched a block's edge; QC now measures how much of the silence falls *inside* expected speech.

### Subtitles: non-overlap outranks minimum duration

Cues break at sentence ends, cap at `SUBTITLE_MAX_CHARS`, and aim for `SUBTITLE_MIN_SECONDS` on screen. When neighbours sit closer together than the minimum, the cue is pulled earlier into the gap before it; if that is still not enough it takes every frame available and stays short. **Overlapping subtitles are broken; a 0.76 s cue is not.** The test asserts that precise invariant — at least the minimum, *or* provably maximal — rather than a threshold that cannot always hold.

Tests: `tests/test_final.py`, **56/57**. Every mock case passes. The one miss is the **real run's integrated loudness** — −15.06 LUFS against the −14 ±0.5 target, i.e. about 1 dB quiet. It is a known, pre-existing tolerance edge, not an 8B regression: `audio_master.py` is byte-identical to 8A and `render.py` changed only the audio codec string. Two things make it unsurprising on this particular check: the test clip is ~19 s, and EBU R128 integrated loudness with gating is unstable on a short, dynamic signal with ducked music, where two-pass `loudnorm`'s pass-1 gain prediction can fall back and land low; real client videos are minutes long and far steadier. Worth tightening in the audio stage separately; it does not affect the 8B feature set.

---

## 4h. The web app (`server.py` + `web/`)

Double-click `start.bat`: it checks the venv, starts the server and opens the browser.

### The two-step flow exists for one reason

**Step A** (scene detection + script fit check) loads **no models** and takes seconds. The script can be edited on the page and re-checked as often as needed. **Step B** starts the slow job, and shows a time estimate first.

This split is the whole point of the UI. Voice generation costs hours on this CPU; discovering the script was too long afterwards is the most expensive mistake available, so the fit check comes first and is free.

### Jobs

One job runs at a time — a single Chatterbox load already takes most of the RAM — and the rest queue with their position shown. **Every job owns a `status.json`**, which is the source of truth rather than memory, so finished jobs survive a restart and an unfinished one can be resumed. Resume is nearly free: the TTS cache is keyed on text + reference + settings + seed, so voice already made is found, not remade.

**Interrupted is not cancelled.** A job stopped because the server went down is unfinished work that can be resumed; a job the user cancelled was deliberately abandoned. An early version marked both `cancelled`, which hid the fact that the first kind was resumable. `shutdown()` sets `_stop` before `_cancel` precisely so the worker can tell the two apart.

Progress reporting threads a `progress`/`cancel` pair through `run_job` → `run_voice_stage`, so the UI shows stage, block *x* of *y*, take *x* of *n*, and a remaining-time estimate computed from **this run's measured pace** once there are two takes to measure. `outputs/speed_profile.json` keeps a smoothed seconds-per-word per engine so the *first* estimate is based on this machine, not a guess.

### `status.json` is written by two threads — a job once vanished into the queue

Found by the 8B end-to-end run (2026-11-06). The HTTP thread writes a job's status when it is submitted and renumbered; the worker writes the same file as it reports progress. Both wrote through one shared `status.tmp`, so the second `os.replace` hit **`PermissionError: [WinError 5]`**. Measured on this machine: four threads × 60 writes to one file produced **186 failures out of 240**.

The failure mode was the bad part, not the exception. The worker raised while *starting* a job — which had already been popped off the queue — so there was nothing left to run it, and `status.json` still read `queued`. On screen that is **"Waiting in the queue, position 1" forever**, indistinguishable from a busy machine, and the one remedy a person naturally reaches for is the one that cannot work: waiting.

**Readers are half the problem.** Unique temp names fixed writer-versus-writer and the next run failed the same way: Python opens files on Windows without `FILE_SHARE_DELETE`, so one thread merely *reading* `status.json` — an open SSE stream, the jobs table — makes the writer's `os.replace` fail. Measured: three writers against five readers, with unique temp names and no reader lock, **267 failures**. So `read()` takes the same lock as `write()`. `agent/stats.py` still reads the file directly from the HTTP thread, which the retry below covers.

Four changes, and `tests/test_server.py` §12–13 hold each one:

1. **Every write uses a unique temp name** (`status.<hex>.tmp`), removed in a `finally`, and both the write and the read are serialised on the manager lock so the file and the event published from it stay in the same order for every reader. The rename retries a few times with a short backoff, because a virus scanner or the search indexer can hold the file open from outside this process and that is transient, not a reason to fail a job.
2. **A crash in `_run` marks the job `failed`** with a reason. A job that cannot run must say so; `failed` is also resumable, so nothing is lost.
3. **The loop's `finally` can no longer kill the worker.** `_renumber()` and the snapshot publish sat outside any `try`, so one exception there would end the only worker thread for the life of the process — and every later job would queue behind a thread that no longer existed, with `start()` declining to help because it had already handed out a thread.

### Voice page

Upload a client recording → candidates are cut with the §4b-2 first-6-seconds rule → each clones a test sentence → ECAPA scores them against the full recording → pick one. Profiles live in `voices/<name>/`. The active choice is `voices/active.json`, which **overrides `VOICE_SAMPLE`** in `Config.load()`.

> **Changed in phase 8B.** Profiles used to be undeletable, so switching back was always possible. The dashboard now offers rename and delete, because a page full of abandoned test recordings is its own problem. **The active profile cannot be deleted** — `delete_profile` refuses it by name with a reason, so the guarantee that still matters (the voice every future job clones cannot vanish under it) is kept by a rule rather than by never deleting anything.
>
> **Preparing a voice now goes through the job queue** (`kind="voice"`). It loads Chatterbox and then ECAPA, and running it inline in the HTTP thread could put a second model in memory beside a running job — the one thing §4c forbids. Queued, it reports its three stages, can be cancelled, and cannot collide with a render.

Tests: `tests/test_server.py`, **45/45**, including a mid-job kill and resume that verifies all 8 cached takes are untouched.

---

## 4i. The dashboard (phase 8A)

React + TypeScript (strict) + Vite + Tailwind in `web-ui/`, built to `web/dist/`, which **is committed** so running the app still needs only Python. Node lives in `tools/node/` (gitignored) and is a build tool only — never a runtime dependency.

```
tools\node\npm.cmd --prefix web-ui install     # needs tools/node on PATH
tools\node\npm.cmd --prefix web-ui run build   # -> web/dist
```

Anthropic's **`frontend-design`** plugin is installed and was followed.

**Design tokens** live in `web-ui/src/index.css` as CSS variables, mapped into Tailwind with `@theme inline`. One accent (signal blue), a cool-neutral grey ramp, semantic colours that only ever mean something, a 4px spacing scale, three radii, Inter with `tabular-nums` on every number a person compares down a column. Dark and light follow the system with a manual override persisted in `localStorage` and applied before first paint so the page never flashes.

**Live updates are SSE, not polling.** `JobManager` publishes on every `status.json` write, so the stream can never report something that did not reach disk; `useLive()` opens with a snapshot so a dropped connection resynchronises.

**The storyboard is the point of the whole UI.** Each scene shows its keyframe next to its own editable narration, with a fit bar underneath that re-checks as you type (debounced 400 ms). On generate, the blocks are written as a `Scene N:` marker script, so `MARKER_RE` reproduces exactly the mapping shown on screen — what you saw is what gets made.

**Fit maths has one implementation**, `fit_row()` in `agent/pipeline.py`, shared by the CLI check and the dashboard, so the two cannot disagree.

### What the design review caught

Screenshots at 1440 and 390 in both themes, reviewed and fixed:

- The fit bar said **"Fits"** beside "3.6s speech / 3.4s available" and drew a red overhang. Both were true (the 8% speed-up absorbs it) but together they read as broken. Now: *"Fits with a slight speed-up"* with an accent overhang, and red only when words actually have to be cut.
- The whole Overview sat in skeletons because four calls were bundled in one `Promise.all` and `/api/machine` walked a 7 GB model cache on every request. Panels now load independently and the cache scan is held for two minutes.
- Lazy-loaded keyframes left blank boxes while scrolling the storyboard. These are small JPEGs from localhost; lazy loading bought nothing and cost the thumbnails their point.
- Bare em dashes for unmeasured stats read as a bug, so they now say *"no measurements yet"*.

Tests: `web-ui/e2e/`, **10/10** Playwright specs against the real server with `TTS_ENGINE=mock`.

---

## 4j. Revisions and versions (phase 8B)

### The problem this exists to solve

A job was all-or-nothing. If block 4 of 40 came out wrong, the only remedy was running the whole video again — hours on this CPU. Yet the pipeline already made the fix cheap: the TTS cache is keyed on text + reference + settings + seed, so re-running touches only what changed. What was missing was an entry point that regenerates **one** block, and any way to pick between takes that were already generated and scored.

### Every take is kept, not just the winner

`voice_stage.py` scored N takes per block and threw N−1 of them away from the record. The wavs were on disk in `outputs/_tts_cache/` the whole time and simply never enumerated. `BlockAudio.takes` now persists each one — seed, path, duration, similarity, words_ok, transcript and word timings — so "choose a different take" costs **no model load and no synthesis at all**, just a re-render.

### Three actions, one engine (`agent/revise.py`)

| Action | Models loaded | Cost |
|---|---|---|
| `choose_take` | none | render only |
| `more_takes` | synthesizer, then verifier, for **one** block | N × one block |
| `edit_text` | same, new cache keys for that block only | N × one block |

All three then call **`finish_job`** — align → subtitles → master → render → QC, extracted verbatim out of `run_job`. There is no second rendering path: §4f already cost two bugs to duplicated graph logic, so a revision rebuilds the whole video through the same filter graph rather than patching one segment into the old file.

New takes requested from the review panel use their own seed band (`REVIEW_SEED_BASE = 10_000`) so they cannot collide with the retry bands (`+1000 × round`).

### A revision re-opens its own job id

Same id, same folder, same SSE channel, state back to `running` — so the queue indicator, cancel, the stepper and the block grid all work with no second code path, and the single worker still guarantees one model resident at a time. `params["kind"]` picks `run_revision` over `run_job`.

> **`status.json` is read into `JobStatus(**data)` inside a bare `except: return None`.** Any key in the file that is not a declared field made that job **vanish from the dashboard silently**. `from_dict` now drops unknown keys instead, so a status written by a newer build degrades to missing information rather than a missing job.

### Versions

v1 stays exactly where it already was, at the top of the job folder; v2+ live in `versions/vN/`. A job with no `versions.json` gets a v1 entry synthesised from its existing files on first read, so **every job already on disk gained a history for free and nothing moved**. Paths are stored relative to the job dir so the folder can be copied. `delete()` refuses the last remaining version and refuses v1.

Notes are written from the change — *"Block 3: take 2 of 4 chosen"*, *"Block 5: text edited"* — because a list of v1/v2/v3 with no reason attached is not a history.

### Three latent bugs this phase surfaced

1. **The cache key ignored `TTS_TRIM_TOP_DB` and `VOICE_CONVERTER`**, both of which change the audio. Harmless while nothing could edit them — and reachable the moment the Settings page could write them, with the worst possible symptom: silently reusing stale audio. Both are in the key now.
2. **`timeline.json` pointed at the aligned wavs in `_work/align/`, which the cleanup step deletes.** A finished job's timeline referenced files that no longer existed. The aligned wavs are copied to `<version>/blocks/blockNNN.wav` and the timeline points there — which also gives the review panel the block audio *as it is in the video*, next to the raw take.
3. **`done_takes` was incremented on cache hits**, so a resumed job reported more takes done than it had (a real job on disk read `done_takes: 8, total_takes: 6`). Cached takes are counted separately and shown as "found in cache".

### How the timeline stays smooth

Three tracks over a ten-minute video, so the obvious implementation stutters:

- the **waveform** is one `<canvas>` sized to the viewport, not the scroll width, redrawn only on zoom, scroll or theme change; peaks are precomputed once (`agent/waveform.py`, 50 buckets/second, ~30 KB for ten minutes) rather than decoded in the browser;
- the **playhead** is a single absolutely-positioned element written straight from a `requestAnimationFrame` loop via `style.transform` — never through React state, which would re-render three tracks 60 times a second;
- **thumbnails outside the viewport are not rendered**, and the scroll width is capped at `MAX_WIDTH = 32_000` px so zoom cannot build an element the browser will not composite;
- colours are read from the CSS tokens at draw time, so both themes work without a second code path.

### Settings

`agent/settings_schema.py` is one table of 64 `SettingDef`s across Voice · Timing and fit · Audio · Output · Security, each with a label, a one-line plain-English explanation, type, default, bounds and whether it is writable. The page renders that table, so **a setting cannot be described on screen in a way that disagrees with what the code does with it**. Defaults being a real map is what makes "reset this setting" possible: reset deletes the line from `.env` rather than writing a literal that would then be wrong if the default ever changes.

Everything safe is editable when idle and **locked with a stated reason while a job runs** — a job reads settings as it goes, so changing one underneath it would build a video half one way and half another. Paths, device and the password value are read-only always. The lock is decided on the server, so the page watches the live queue and re-fetches the moment it empties or fills; otherwise a page opened during a job would stay locked after the job ended, with a reload — that nobody is told to do — the only way out.

### Tests

`tests/test_revise.py`, **39/39**. The ones that matter prove the cost: choosing a take creates and touches **zero** cache files; editing one block's text adds **exactly `best_of`** new wavs and leaves every other take byte-identical (same mtime trick `test_server.py` uses for resume); three versions are kept, each with its own video and QC report.

---

## 5. Quality bar — what "fits the image perfectly" means

The agent must check and fix, not just generate:

- **Scene boundary respect** — a sentence must not straddle a cut. If it does, either shift the cut (if allowed) or adjust pacing.
- **No overflow** — narration for a scene must not run longer than the scene. Fix order: (1) small speed-up within a natural range, (2) tighten silences, (3) report to the user if it still does not fit.
- **No dead air** — if a scene is much longer than its narration, pad with natural pause distribution, not a silent block at the end.
- **Verify with faster-whisper**: transcribe the generated narration, compare word timings against the intended script, and flag drift above the threshold in `.env`.
- **Clean audio**: loudness-normalise to broadcast level (EBU R128, around −14 LUFS for YouTube), no clipping.

---

## 6. Conventions

- Python 3.11, type hints everywhere, `dataclass` for config/data objects.
- All paths via `pathlib.Path`, never string concatenation.
- FFmpeg is called through a single wrapper (`agent/ffmpeg_utils.py` or inside `render.py`) — never scattered `subprocess.run` calls.
- Temporary files go to a configurable work dir, cleaned up unless `KEEP_TEMP=true`.
- Fail loudly with a clear plain-English message + the technical detail.
