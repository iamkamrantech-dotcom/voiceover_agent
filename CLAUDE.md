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
6. **User-facing log messages in simple Roman Urdu.** Example: `"Scene detect ho rahi hain..."`, `"Voice clone ho gaya, ab timing check kar raha hoon..."`. Internal/debug logs and all code, comments, docstrings and variable names stay in **English**.
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
│   ├── config.py         # Config dataclass, loads .env
│   ├── logger.py         # Roman Urdu user log + English debug log
│   ├── video_probe.py    # ffprobe: duration, fps, resolution
│   ├── scene_detect.py   # OpenCV/FFmpeg: find cuts & fades
│   ├── script_parser.py  # Scene markers / paragraphs / time-split
│   ├── tts_chatterbox.py # Voice cloning + speech generation
│   ├── align.py          # faster-whisper word timings, fit audio to scenes
│   ├── subtitles.py      # SRT / VTT output
│   ├── render.py         # FFmpeg mux: video + narration + subs
│   ├── qc.py             # quality check: drift, clipping, dead air
│   └── pipeline.py       # orchestrates the phases
├── tests/
│   ├── make_test_video.py
│   └── test_*.py
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
- faster-whisper on CPU: use `compute_type="int8"` and a small model (`base` or `small`). `large-v3` is too slow on 4 cores.
- **Chatterbox on CPU is slow** — expect roughly **5–15× slower than real time** (a 1-minute narration can take 5–15 minutes). So:
  - synthesise **per scene/chunk**, not the whole script at once;
  - **cache** generated audio on disk keyed by (text + voice sample + settings) so a re-run never re-synthesises unchanged lines;
  - always show progress in the Roman Urdu log, e.g. `"Scene 3/12 ki awaaz ban rahi hai..."`;
  - keep a `FAST_TEST=true` mode in `.env` that uses tiny clips so tests don't take an hour.

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
- Fail loudly with a clear Roman Urdu message + the English technical detail.
