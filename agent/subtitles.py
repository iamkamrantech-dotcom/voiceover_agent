"""Subtitles built from the final word timings on the output timeline.

Rules, in priority order:
  * one line, never wider than SUBTITLE_MAX_CHARS (42)
  * ALWAYS break at a sentence end - a cue that runs past a full stop reads
    badly even when it fits
  * at least SUBTITLE_MIN_SECONDS (0.8s) on screen, so nothing flashes
  * never overlapping - a cue extended to reach the minimum is clamped
    against the next cue's start

The single implementation: the preview burns these same cues, so what is
checked by eye in the draft is exactly what ships.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.align import Timeline  # noqa: E402
from agent.config import Config  # noqa: E402

SENTENCE_ENDS = ".!?"
GAP = 0.04  # smallest hole left between neighbouring cues


@dataclass
class Cue:
    index: int
    start: float
    end: float
    text: str

    @property
    def duration(self) -> float:
        return self.end - self.start


def _ends_sentence(word: str) -> bool:
    stripped = word.strip().rstrip("\"')]")
    return bool(stripped) and stripped[-1] in SENTENCE_ENDS


def build_cues(timeline: Timeline, cfg: Config) -> list[Cue]:
    raw: list[tuple[float, float, str]] = []

    for block in timeline.blocks:
        if block.silent or not block.words:
            continue
        line: list = []
        for word in block.words:
            candidate = " ".join(w.word for w in line + [word])
            if line and len(candidate) > cfg.subtitle_max_chars:
                raw.append((line[0].start, line[-1].end,
                            " ".join(w.word for w in line)))
                line = [word]
            else:
                line.append(word)
            # A sentence ending closes the cue regardless of length.
            if _ends_sentence(word.word):
                raw.append((line[0].start, line[-1].end,
                            " ".join(w.word for w in line)))
                line = []
        if line:
            raw.append((line[0].start, line[-1].end,
                        " ".join(w.word for w in line)))

    raw.sort(key=lambda item: item[0])

    cues: list[Cue] = []
    for i, (start, end, text) in enumerate(raw):
        # Never run into the cue after this one. Overlapping subtitles are
        # simply broken, so this rule outranks the minimum duration.
        ceiling = raw[i + 1][0] - GAP if i + 1 < len(raw) else float("inf")
        floor = cues[-1].end + GAP if cues else 0.0

        if start < floor:
            start = floor
        end = min(max(end, start + cfg.subtitle_min_seconds), ceiling)

        # Still too short? Pull the START earlier into the gap before it
        # rather than give up on the minimum. A subtitle that leads the
        # voice slightly reads better than one that flashes past.
        if end - start < cfg.subtitle_min_seconds:
            wanted = end - cfg.subtitle_min_seconds
            start = max(floor, min(start, wanted))

        end = max(end, start + 0.2)  # never collapse to nothing
        cues.append(Cue(len(cues) + 1, start, end, text))

    return cues


def srt_time(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(timeline: Timeline, cfg: Config, out_path: Path,
              log=None) -> Path:
    cues = build_cues(timeline, cfg)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        for cue in cues:
            handle.write(f"{cue.index}\n"
                         f"{srt_time(cue.start)} --> {srt_time(cue.end)}\n"
                         f"{cue.text}\n\n")
    if log:
        log.debug("subtitles: %d cues -> %s", len(cues), out_path)
    return out_path


def parse_srt(path: Path) -> list[Cue]:
    """Read back an SRT, so tests check the real file rather than memory."""
    def to_seconds(stamp: str) -> float:
        stamp = stamp.strip().replace(",", ".")
        h, m, s = stamp.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)

    cues: list[Cue] = []
    blocks = path.read_text(encoding="utf-8").strip().split("\n\n")
    for raw in blocks:
        lines = [line for line in raw.splitlines() if line.strip()]
        if len(lines) < 3:
            continue
        start, end = lines[1].split("-->")
        cues.append(Cue(int(lines[0]), to_seconds(start), to_seconds(end),
                        " ".join(lines[2:])))
    return cues
