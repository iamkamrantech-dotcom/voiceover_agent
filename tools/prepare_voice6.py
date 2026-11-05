"""Build reference clips tuned to how the STANDARD Chatterbox model listens.

The standard model reads only:
  * the first  6 s  -> speaker prompt tokens (ENC_COND_LEN = 6 * 16 kHz)
  * the first 10 s  -> s3gen decoder reference (DEC_COND_LEN = 10 * 24 kHz)

So a 14 s clip is mostly wasted, and whatever sits in the FIRST 6 SECONDS is
what actually defines the cloned voice. This scorer therefore optimises the
first 6 s hard and the remaining 4 s only lightly.

A window is good when its first 6 s are:
  - continuous speech (high speech ratio, no long pause)
  - starting exactly on a speech onset (not on a breath or silence)
  - steady in volume, never clipped
  - stable in pitch (laughs and squeaks have erratic f0)
  - TYPICAL - pitch and level close to the recording's own median

    venv\\Scripts\\python.exe tools\\prepare_voice6.py
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import librosa
import numpy as np
import pyloudnorm as ln
import soundfile as sf
from scipy.signal import butter, sosfilt

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INPUT_DIR = PROJECT_ROOT / "input"
SOURCE = INPUT_DIR / "voice_sample.mp3"

SR = 24_000
PROMPT_SECONDS = 6.0        # what the speaker encoder actually reads
CLIP_SECONDS = 10.0         # what the decoder actually reads
N_CLIPS = 4
STEP_SECONDS = 0.25
HOP = 256
FRAME = 1024
HIGHPASS_HZ = 70.0
TARGET_LUFS = -23.0
CLIP_CEILING = 0.99
ONSET_SECONDS = 0.30        # the clip must start speaking this fast


def log(msg: str) -> None:
    print(f"[agent] {msg}", flush=True)


@dataclass
class Window:
    start_s: float
    score: float
    speech6: float
    longest_gap6: float
    steady6: float
    f0_std: float
    f0_med: float
    clipped: int

    @property
    def end_s(self) -> float:
        return self.start_s + CLIP_SECONDS

    def describe(self) -> str:
        return (
            f"{self.start_s:5.1f}s -> {self.end_s:5.1f}s | "
            f"first6s: speech {self.speech6 * 100:5.1f}%  "
            f"gap {self.longest_gap6:4.2f}s  "
            f"steady +/-{self.steady6:4.1f}dB  "
            f"f0 {self.f0_med:5.1f}Hz +/-{self.f0_std:4.1f} | "
            f"clip {self.clipped}"
        )


def highpass(x: np.ndarray, sr: int, cutoff: float) -> np.ndarray:
    sos = butter(2, cutoff, btype="highpass", fs=sr, output="sos")
    return sosfilt(sos, x).astype(np.float32)


def longest_run(mask: np.ndarray) -> int:
    """Longest run of False values (i.e. the longest pause)."""
    best = run = 0
    for v in mask:
        run = 0 if v else run + 1
        best = max(best, run)
    return best


def analyse(y: np.ndarray, sr: int) -> list[Window]:
    rms = librosa.feature.rms(y=y, frame_length=FRAME, hop_length=HOP)[0]
    rms_db = librosa.amplitude_to_db(rms, ref=1.0)
    speech_floor = np.percentile(rms_db, 90) - 25.0
    voiced_all = rms_db > speech_floor

    log("Pitch (f0) nikali ja rahi hai, thora waqt lagega...")
    f0 = librosa.yin(y, fmin=65, fmax=400, sr=sr,
                     frame_length=FRAME, hop_length=HOP)

    # The recording's own "typical" voice, used as the target to stay near.
    g_f0_med = float(np.median(f0[voiced_all[: len(f0)]]))
    g_level = float(np.median(rms_db[voiced_all]))
    log(f"Typical awaaz: f0 {g_f0_med:.1f} Hz, level {g_level:.1f} dB")

    win = int(CLIP_SECONDS * sr)
    step = int(STEP_SECONDS * sr)
    f_per_s = sr / HOP
    n6 = int(PROMPT_SECONDS * f_per_s)
    n_onset = max(1, int(ONSET_SECONDS * f_per_s))

    out: list[Window] = []
    for start in range(0, len(y) - win + 1, step):
        seg = y[start : start + win]
        f0i = start // HOP
        v6 = voiced_all[f0i : f0i + n6]
        db6 = rms_db[f0i : f0i + n6]
        p6 = f0[f0i : f0i + n6] if f0i + n6 <= len(f0) else f0[f0i:]
        if v6.size < n6 or p6.size < 4 or v6.sum() < 8:
            continue

        speech6 = float(v6.mean())
        gap6 = longest_run(v6) / f_per_s
        voiced_db = db6[v6]
        steady6 = float(np.percentile(voiced_db, 90) - np.percentile(voiced_db, 10))
        level6 = float(np.mean(voiced_db))

        pv = p6[: v6.size][v6]
        if pv.size < 4:
            continue
        f0_med = float(np.median(pv))
        f0_std = float(np.std(pv))

        clipped = int(np.sum(np.abs(seg) >= CLIP_CEILING))
        onset_ok = bool(v6[:n_onset].mean() >= 0.6)

        # Whole-clip speech ratio matters a little (decoder reads 10 s).
        v10 = voiced_all[f0i : f0i + int(CLIP_SECONDS * f_per_s)]
        speech10 = float(v10.mean()) if v10.size else 0.0

        score = (
            speech6 * 100.0          # continuous speech is the main thing
            + speech10 * 20.0
            - gap6 * 40.0            # a long pause inside the prompt is bad
            - steady6 * 1.5          # wobbly level
            - min(f0_std, 120.0) * 0.35   # erratic pitch = laugh / squeak
            - abs(f0_med - g_f0_med) / max(g_f0_med, 1.0) * 60.0   # typicality
            - abs(level6 - g_level) * 1.0
            - min(clipped, 500) * 0.5
            - (0.0 if onset_ok else 60.0)  # must not open on a breath/silence
        )
        out.append(Window(start / sr, score, speech6, gap6, steady6,
                          f0_std, f0_med, clipped))
    return out


def pick_spread(wins: list[Window], n: int) -> list[Window]:
    chosen: list[Window] = []
    for w in sorted(wins, key=lambda w: w.score, reverse=True):
        if all(w.end_s <= o.start_s or w.start_s >= o.end_s for o in chosen):
            chosen.append(w)
            if len(chosen) == n:
                break
    return sorted(chosen, key=lambda w: w.start_s)


def process(seg: np.ndarray, sr: int) -> np.ndarray:
    """Light touch only. NOTE: no silence trimming here on purpose - the
    window already starts on a speech onset, and trimming would shift the
    6 s boundary that this whole script is built around."""
    filtered = highpass(seg, sr, HIGHPASS_HZ)
    try:
        meter = ln.Meter(sr)
        loud = meter.integrated_loudness(filtered)
        if np.isfinite(loud):
            filtered = ln.normalize.loudness(filtered, loud, TARGET_LUFS)
    except Exception as exc:
        log(f"Warning: loudness normalize skip ({exc})")
    peak = float(np.max(np.abs(filtered))) if filtered.size else 0.0
    if peak > 0.98:
        filtered = filtered * (0.98 / peak)
    return filtered.astype(np.float32)


def main() -> int:
    if not SOURCE.is_file():
        log(f"Source nahi mila: {SOURCE}")
        return 1

    log(f"Client recording load ho rahi hai: {SOURCE.name}")
    y, sr = librosa.load(str(SOURCE), sr=SR, mono=True)
    log(f"Lambai {len(y) / sr:.1f}s. Har 10s window ka pehla 6s check hoga.")

    wins = analyse(y, sr)
    if len(wins) < N_CLIPS:
        log("Kaafi saaf windows nahi mile.")
        return 1

    picked = pick_spread(wins, N_CLIPS)

    print()
    print("-" * 100)
    print("  CHOSEN 10s WINDOWS (scored on their FIRST 6 SECONDS)")
    print("-" * 100)
    for i, w in enumerate(picked, start=1):
        seg = y[int(w.start_s * sr) : int(w.start_s * sr) + int(CLIP_SECONDS * sr)]
        clip = process(seg, sr)
        out = INPUT_DIR / f"ref6_{i}.wav"
        sf.write(str(out), clip, sr, subtype="PCM_16")
        print(f"  ref6_{i}.wav  score {w.score:6.1f} | {w.describe()}")

    print()
    log(f"{len(picked)} naye reference clips tayyar hain.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
