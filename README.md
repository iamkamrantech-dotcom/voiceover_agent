# Voiceover Agent

Turn an image-based slideshow video plus an English script into a
YouTube-ready video narrated in a cloned voice. Everything runs locally and
free — no paid APIs, no cloud services.

The app checks and fixes the sync itself: the right sentence lands on the
right image, nothing is cut off mid-word, no long dead air, no rushing.

---

## What you need first

| Requirement | Why | How to get it |
|---|---|---|
| **Windows 10/11, 64-bit** | tested platform | — |
| **Python 3.11** | the app runs on it | [python.org](https://www.python.org/downloads/release/python-3119/) — tick "Add to PATH" |
| **Git** | to download the project | [git-scm.com](https://git-scm.com/download/win) |
| **FFmpeg + ffprobe on PATH** | video and audio processing | `winget install -e --id Gyan.FFmpeg` |
| **Visual C++ 2015–2022 x64 Redistributable** | `torch` won't load without it | `winget install -e --id Microsoft.VCRedist.2015+.x64` |
| **~16 GB RAM, ~10 GB free disk** | models load in memory; weights are ~7 GB | — |

> Runs on CPU — no graphics card needed. It is **slow on CPU**: budget a few
> times the video's length per run (a 10-minute narration can take ~40 min).
> That is expected, and the app shows progress and a time estimate.

---

## Setup — one time

Open **Command Prompt** and run these, line by line:

```cmd
git clone https://github.com/iamkamrantech-dotcom/voiceover_agent.git
cd voiceover_agent

py -3.11 -m venv venv

venv\Scripts\python.exe -m pip install --upgrade pip

REM torch MUST come from the CPU index first, or pip pulls a 2.5 GB CUDA build this can't use
venv\Scripts\python.exe -m pip install torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cpu

venv\Scripts\python.exe -m pip install -r requirements.txt
```

That's the whole install. (The first time you actually generate a video, it
downloads the voice/transcription models from Hugging Face, ~7 GB — one time,
then cached.)

---

## Run it

Double-click **`start.bat`** (or run it from Command Prompt).

The browser opens by itself at `http://127.0.0.1:8000`. To stop the app,
close the black window.

---

## Using it — A to Z

1. **Add the voice** (Voice page, do this once)
   - Upload a clean recording of the person to clone — a minute or more of
     normal speech, as clean as you have.
   - The app cuts candidate clips, scores them, and lists them. Play them,
     pick the best, click **Use this**. Every video from now uses that voice.

2. **Make a video** (New video page)
   - Upload your **slideshow video** (images with cuts/fades) and paste the
     **English script**.
   - The app splits the script across the scenes and shows a **fit check** —
     edit the text until each part fits its pictures. This step is free and
     takes seconds, so get it right here before generating.
   - Click **Generate**. It shows a time estimate first, then the stages:
     voice, scoring, alignment, mastering, render, quality checks.

3. **Review and fix** (Job page, when it finishes)
   - Watch the result with the player and the timeline.
   - Click any block to hear every take, switch to a better one, generate
     more takes, or edit that block's text — then **Re-render**. Only the
     changed block is redone; everything else is reused, so a fix costs about
     a minute instead of a full re-run. Every version is kept.

4. **Download** the MP4, subtitles (`.srt`), timeline and the quality report.

---

## Settings

The **Settings** page has every option in plain English with its default,
grouped into Voice, Timing, Audio, Output and Security. You rarely need to
touch it — the defaults are tuned. Settings lock while a job is running.

To require a password for the dashboard, set `APP_PASSWORD` in `.env` (it is
read on the next start).

---

## If something goes wrong

- **`import torch` fails / c10.dll error** → install the Visual C++
  Redistributable (table above), then reinstall `torch` from the CPU index.
- **"ffmpeg not found"** → install FFmpeg and reopen the terminal so it is on
  PATH.
- **`start.bat` says "No venv found"** → you skipped the setup; run the setup
  block above.
- **A job sits "Queued" behind another** → only one job runs at a time (the
  models use most of the RAM); it starts when the one ahead finishes.

---

## What's in here

`agent/` the pipeline · `server.py` the web app · `web/dist/` the built
dashboard (so running it needs only Python) · `web-ui/` the dashboard source ·
`tests/` the test suite · `CLAUDE.md` the full technical notes.
