"""Build 3 candidate voice-reference clips from the client's recording.

Picks three non-overlapping 12-15s windows that have the most continuous clean
speech: high speech ratio, low silence, no clipping, steady volume.

Processing is deliberately light so the voice itself is not altered:
  mono -> trim edge silence -> gentle 70 Hz high-pass -> loudness normalize.

    venv\\Scripts\\python.exe tools\\prepare_voice.py
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

SR = 24_000                 # Chatterbox works at 24 kHz
CLIP_SECONDS = 14.0         # inside the 12-15s window the task asks for
N_CLIPS = 3
HIGHPASS_HZ = 70.0
TARGET_LUFS = -23.0         # reference level; Chatterbox re-normalises anyway
FRAME = 1024
HOP = 256
CLIP_CEILING = 0.99         # samples at/above this count as clipped


def log(msg: str) -> None:
    print(f"[agent] {msg}", flush=True)


@dataclass
class Candidate:
    start_s: float
    end_s: float
    score: float
    speech_ratio: float
    rms_db: float
    steadiness_db: float
    clipped: int

    def describe(self) -> str:
        return (
            f"{self.start_s:6.1f}s -> {self.end_s:6.1f}s | "
            f"speech {self.speech_ratio * 100:5.1f}% | "
            f"level {self.rms_db:6.1f} dB | "
            f"steadiness +/-{self.steadiness_db:4.1f} dB | "
            f"clipped {self.clipped}"
        )


def highpass(x: np.ndarray, sr: int, cutoff: float) -> np.ndarray:
    sos = butter(2, cutoff, btype="highpass", fs=sr, output="sos")
    return sosfilt(sos, x).astype(np.float32)


def score_windows(y: np.ndarray, sr: int) -> list[Candidate]:
    """Slide a CLIP_SECONDS window and score each position."""
    win = int(CLIP_SECONDS * sr)
    step = int(0.5 * sr)
    if len(y) < win:
        raise SystemExit(f"Recording is shorter than {CLIP_SECONDS}s.")

    # Frame energy in dB, used for both speech detection and steadiness.
    rms = librosa.feature.rms(y=y, frame_length=FRAME, hop_length=HOP)[0]
    rms_db = librosa.amplitude_to_db(rms, ref=1.0)
    # Speech threshold relative to this recording's own loud parts.
    speech_floor = np.percentile(rms_db, 90) - 25.0

    out: list[Candidate] = []
    for start in range(0, len(y) - win + 1, step):
        seg = y[start : start + win]
        f0 = start // HOP
        f1 = (start + win) // HOP
        seg_db = rms_db[f0:f1]
        if seg_db.size == 0:
            continue

        voiced = seg_db > speech_floor
        speech_ratio = float(voiced.mean())
        if voiced.sum() < 4:
            continue

        # Steadiness: spread of level across the *speech* frames only.
        voiced_db = seg_db[voiced]
        steadiness = float(np.percentile(voiced_db, 90) - np.percentile(voiced_db, 10))
        level = float(np.mean(voiced_db))
        clipped = int(np.sum(np.abs(seg) >= CLIP_CEILING))

        # More speech is good; wobbly level and clipping are bad.
        score = (
            speech_ratio * 100.0
            - steadiness * 1.5
            - min(clipped, 500) * 0.5
        )
        out.append(
            Candidate(start / sr, (start + win) / sr, score, speech_ratio,
                      level, steadiness, clipped)
        )
    return out


def pick_non_overlapping(cands: list[Candidate], n: int) -> list[Candidate]:
    """Best-first, skipping anything that overlaps an already chosen clip."""
    chosen: list[Candidate] = []
    for c in sorted(cands, key=lambda c: c.score, reverse=True):
        if all(c.end_s <= o.start_s or c.start_s >= o.end_s for o in chosen):
            chosen.append(c)
            if len(chosen) == n:
                break
    return sorted(chosen, key=lambda c: c.start_s)


def process_clip(seg: np.ndarray, sr: int) -> np.ndarray:
    # 1. trim silence at the edges only
    trimmed, _ = librosa.effects.trim(seg, top_db=30)
    if trimmed.size < sr * 5:  # never fall under Turbo's 5s minimum
        trimmed = seg
    # 2. gentle high-pass to kill rumble, leaves the voice alone
    filtered = highpass(trimmed, sr, HIGHPASS_HZ)
    # 3. loudness normalise (gain only - no compression, no EQ)
    try:
        meter = ln.Meter(sr)
        loudness = meter.integrated_loudness(filtered)
        if np.isfinite(loudness):
            filtered = ln.normalize.loudness(filtered, loudness, TARGET_LUFS)
    except Exception as exc:
        log(f"Warning: loudness normalize skip ho gaya ({exc})")
    # 4. safety: back off if normalisation pushed anything into clipping
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
    log(f"Lambai {len(y) / sr:.1f} second, {sr} Hz, mono.")

    cands = score_windows(y, sr)
    if len(cands) < N_CLIPS:
        log("Itne saaf hisse nahi mile ke 3 alag clip ban sakein.")
        return 1

    picked = pick_non_overlapping(cands, N_CLIPS)
    log(f"{len(picked)} sab se saaf hisse chun liye gaye.")

    print()
    print("-" * 78)
    print("  CHOSEN REFERENCE CLIPS (from the original recording)")
    print("-" * 78)
    for i, c in enumerate(picked, start=1):
        seg = y[int(c.start_s * sr) : int(c.end_s * sr)]
        clip = process_clip(seg, sr)
        out = INPUT_DIR / f"ref_{i}.wav"
        sf.write(str(out), clip, sr, subtype="PCM_16")
        print(f"  ref_{i}.wav  {c.describe()}")
        print(f"              saved {len(clip) / sr:.1f}s -> {out}")

    print()
    log("Teeno reference clip tayyar hain.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
