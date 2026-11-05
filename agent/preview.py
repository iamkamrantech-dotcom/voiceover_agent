"""Fast preview render - for checking sync by eye, not for delivery.

Low resolution, fast preset, burned-in subtitles. The point is to see
whether the narration lands on the right image, so it must be quick to
produce and obvious to read.

The video is re-timed in one FFmpeg filter graph. Per block:

    [ src_start .. hold_end ]  slowed by video_slowdown
    [ freeze on the hold_end frame ]        <- tpad clones the LAST frame,
    [ hold_end .. src_end ]    normal speed    which is exactly hold_end

Freezing on hold_end is the whole trick: it is the last clean frame before
the outgoing transition, so a frozen frame is never a half-dissolved blend.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.config import Config  # noqa: E402
from agent import log as agent_log  # noqa: E402
from agent.align import Timeline, BlockPlan  # noqa: E402


# --------------------------------------------------------------------------
# audio track
# --------------------------------------------------------------------------

def build_voice_track(timeline: Timeline, cfg: Config, out_path: Path,
                      log) -> Path:
    """One wav holding every block's voice at its planned position."""
    import soundfile as sf

    sample_rate = 0
    for block in timeline.blocks:
        if block.wav:
            sample_rate = sf.info(block.wav).samplerate
            break
    sample_rate = sample_rate or cfg.audio_sample_rate

    total = int(np.ceil(timeline.output_duration * sample_rate)) + sample_rate
    track = np.zeros(total, dtype=np.float32)

    for block in timeline.blocks:
        if block.silent or not block.wav:
            continue
        audio, sr = sf.read(block.wav, dtype="float32", always_2d=False)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        if sr != sample_rate:
            import librosa

            audio = librosa.resample(audio, orig_sr=sr, target_sr=sample_rate)
        start = int(round(block.voice_start * sample_rate))
        end = min(start + len(audio), total)
        if end > start:
            track[start:end] += audio[: end - start]

    peak = float(np.max(np.abs(track))) if track.size else 0.0
    if peak > 0.99:
        track *= 0.99 / peak
        log.debug("voice track normalised from peak %.3f", peak)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out_path), track, sample_rate, subtype="PCM_16")
    log.debug("voice track: %s (%.2fs)", out_path, len(track) / sample_rate)
    return out_path


# --------------------------------------------------------------------------
# subtitles
# --------------------------------------------------------------------------

def _srt_time(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_subtitles(timeline: Timeline, cfg: Config, out_path: Path,
                    log) -> Path:
    """Group the word timings into short readable lines."""
    entries: list[tuple[float, float, str]] = []

    for block in timeline.blocks:
        if block.silent or not block.words:
            continue
        line: list = []
        for word in block.words:
            candidate = " ".join(w.word for w in line + [word])
            if line and len(candidate) > cfg.subtitle_max_chars:
                entries.append((line[0].start, line[-1].end,
                                " ".join(w.word for w in line)))
                line = [word]
            else:
                line.append(word)
        if line:
            entries.append((line[0].start, line[-1].end,
                            " ".join(w.word for w in line)))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        for i, (start, end, text) in enumerate(entries, start=1):
            handle.write(f"{i}\n{_srt_time(start)} --> "
                         f"{_srt_time(max(end, start + 0.3))}\n{text}\n\n")
    log.debug("subtitles: %d lines -> %s", len(entries), out_path)
    return out_path


def _escape_for_filter(path: Path) -> str:
    """FFmpeg filter arguments need the Windows drive colon escaped."""
    text = str(path.resolve()).replace("\\", "/")
    return text.replace(":", r"\:")


# --------------------------------------------------------------------------
# video graph
# --------------------------------------------------------------------------

def split_point(timeline: Timeline, block: BlockPlan) -> tuple[float, bool]:
    """Where the clean part of a block ends, and whether a separate tail
    chain is needed for the outgoing transition.

    `split_at` comes from the aligner (hold_end + 1 frame), so the renderer
    and the plan can never drift apart.

    A tail only exists when something follows hold_end inside this block -
    true for a FADE, false for a hard CUT, where hold_end is the last frame
    of the block. Retiming is applied to the clean part either way: tying
    it to the tail would silently drop the freeze on every hard cut.
    """
    frame = 1.0 / timeline.fps if timeline.fps else 0.04
    at = block.split_at or min(block.src_end, block.hold_end + frame)
    has_tail = at < block.src_end - 1e-6
    return at, has_tail


def build_filter_graph(timeline: Timeline, cfg: Config,
                       subtitle_path: Path | None) -> str:
    blocks = timeline.blocks
    chains: list[str] = []

    # One copy of the source per chain that reads it.
    parts = [2 if split_point(timeline, b)[1] else 1 for b in blocks]
    copies = sum(parts)

    split_labels = [f"s{i}" for i in range(copies)]
    chains.append("[0:v]split=" + str(copies) + "".join(
        f"[{label}]" for label in split_labels))

    all_labels: list[str] = []
    cursor = 0
    for i, block in enumerate(blocks):
        at, has_tail = split_point(timeline, block)

        # part 1: everything up to and including hold_end, slowed then frozen
        first = f"b{i}a"
        chain = (f"[{split_labels[cursor]}]trim=start={block.src_start:.6f}:"
                 f"end={at:.6f},setpts=PTS-STARTPTS")
        if block.video_slowdown > 1.0:
            chain += f",setpts={block.video_slowdown:.6f}*PTS"
        if block.freeze_seconds > 0:
            # tpad clones the LAST frame of this part = the hold_end frame.
            chain += (f",tpad=stop_mode=clone:"
                      f"stop_duration={block.freeze_seconds:.6f}")
        chains.append(chain + f"[{first}]")
        all_labels.append(first)
        cursor += 1

        # part 2: the outgoing transition, always at normal speed
        if has_tail:
            second = f"b{i}b"
            chains.append(
                f"[{split_labels[cursor]}]trim=start={at:.6f}:"
                f"end={block.src_end:.6f},setpts=PTS-STARTPTS[{second}]"
            )
            all_labels.append(second)
            cursor += 1

    concat_in = "".join(f"[{label}]" for label in all_labels)
    chains.append(f"{concat_in}concat=n={len(all_labels)}:v=1:a=0[joined]")

    tail = f"[joined]scale=-2:{cfg.preview_height},fps={timeline.fps}"
    if subtitle_path is not None:
        tail += (f",subtitles='{_escape_for_filter(subtitle_path)}'"
                 f":force_style='FontSize=16,Outline=1,MarginV=18'")
    tail += "[vout]"
    chains.append(tail)

    return ";".join(chains)


# --------------------------------------------------------------------------

def render_preview(video: Path, timeline: Timeline, out_path: Path,
                   cfg: Config | None = None, work_dir: Path | None = None,
                   log=None) -> Path:
    cfg = cfg or Config.load()
    log = log or agent_log.get()
    work = Path(work_dir or (cfg.work_dir / "preview"))
    work.mkdir(parents=True, exist_ok=True)

    log.user("Preview ke liye awaaz ka track bana rahe hain...")
    voice = build_voice_track(timeline, cfg, work / "voice_track.wav", log)

    subtitle_path = None
    if cfg.preview_subtitles:
        subtitle_path = build_subtitles(timeline, cfg, work / "preview.srt", log)

    def run(with_subs: bool) -> subprocess.CompletedProcess:
        graph = build_filter_graph(timeline, cfg,
                                   subtitle_path if with_subs else None)
        (work / "filter_graph.txt").write_text(graph, encoding="utf-8")
        cmd = [
            cfg.ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(video), "-i", str(voice),
            "-filter_complex", graph,
            "-map", "[vout]", "-map", "1:a",
            "-c:v", "libx264", "-preset", cfg.preview_preset,
            "-crf", str(cfg.preview_crf), "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            "-t", f"{timeline.output_duration:.6f}",
            str(out_path),
        ]
        return subprocess.run(cmd, capture_output=True, text=True)

    log.user("Preview render ho raha hai (fast, low quality)...")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    proc = run(with_subs=subtitle_path is not None)

    if proc.returncode != 0 and subtitle_path is not None:
        log.warning("Subtitles ke sath render fail hua, bina subtitles try kar rahe hain.")
        log.debug("ffmpeg error: %s", proc.stderr.strip()[:800])
        proc = run(with_subs=False)

    if proc.returncode != 0:
        log.error(f"Preview render fail: {proc.stderr.strip()[:500]}")
        raise RuntimeError("FFmpeg preview render failed")

    log.user(f"Preview tayyar: {out_path}")
    return out_path
