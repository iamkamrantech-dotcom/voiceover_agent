"""Amplitude peaks for the job page's timeline.

The browser must not decode a 48 kHz wav to draw a waveform - for a ten
minute narration that is 28 million samples it would have to fetch and
decode before the first pixel appears. Instead the peaks are measured once,
here, and stored as one byte per bucket.

50 buckets per second is the point where a drawn waveform stops looking
blocky at normal zoom, and ten minutes still costs about 30 KB over the
wire. One byte per bucket is plenty: a waveform is read as a shape, not as
a measurement, and nothing in the UI quotes a number from it.

Speech is near enough symmetric around zero, so a single peak per bucket is
stored and drawn mirrored. That halves the size for no visible difference.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

PER_SECOND = 50
VERSION = 1


def build_peaks(wav: Path, out_path: Path | None = None,
                per_second: int = PER_SECOND) -> dict:
    """Measure `wav` and return (and optionally save) its peak data."""
    import numpy as np
    import soundfile as sf

    wav = Path(wav)
    info = sf.info(str(wav))
    rate = info.samplerate
    bucket = max(1, int(round(rate / per_second)))

    peaks: list[int] = []
    with sf.SoundFile(str(wav)) as handle:
        # Read in whole seconds so a bucket is never split across reads.
        chunk = bucket * per_second
        while True:
            block = handle.read(frames=chunk, dtype="float32", always_2d=True)
            if block.shape[0] == 0:
                break
            mono = np.abs(block).max(axis=1)
            full = (mono.shape[0] // bucket) * bucket
            if full:
                windows = mono[:full].reshape(-1, bucket).max(axis=1)
                peaks.extend((np.clip(windows, 0.0, 1.0) * 255)
                             .astype(np.uint8).tolist())
            tail = mono[full:]
            if tail.size and block.shape[0] < chunk:
                peaks.append(int(min(1.0, float(tail.max())) * 255))

    data = {
        "version": VERSION,
        "per_second": per_second,
        "duration": round(info.frames / rate, 3),
        "peaks": base64.b64encode(bytes(peaks)).decode("ascii"),
    }
    if out_path is not None:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(data), encoding="utf-8")
    return data


def peaks_for(wav: Path, cache: Path, per_second: int = PER_SECOND) -> dict:
    """Cached peaks, measured on first use.

    Jobs made before this existed have no peak file, so they get one the
    first time their timeline is opened rather than no waveform at all.
    """
    cache = Path(cache)
    if cache.is_file():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if data.get("version") == VERSION and data.get("peaks"):
                return data
        except Exception:
            pass  # unreadable or from an older format - measure it again
    return build_peaks(wav, cache, per_second)
