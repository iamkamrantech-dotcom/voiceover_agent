"""Fast preview render - for checking sync by eye, not for delivery.

Low resolution, fast preset, burned-in subtitles. The point is to see
whether the narration lands on the right image, so it must be quick to
produce and obvious to read.

The re-timing itself lives in agent/video_graph.py and the subtitles in
agent/subtitles.py, both shared with the final render. A draft that proves
sync is worthless if the final render re-times differently, so neither is
reimplemented here - this module only assembles the voice track and picks
draft-quality encode settings.
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
from agent.align import Timeline  # noqa: E402
from agent.subtitles import write_srt  # noqa: E402
from agent.video_graph import build_filter_graph  # noqa: E402


def probe_size(cfg: Config, video: Path) -> tuple[int, int]:
    proc = subprocess.run(
        [cfg.ffprobe_bin, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(video)],
        capture_output=True, text=True,
    )
    try:
        width, height = proc.stdout.strip().split(",")[:2]
        return int(width), int(height)
    except ValueError:
        return 1280, 720


def build_voice_track(timeline: Timeline, cfg: Config, out_path: Path,
                      log, sample_rate: int | None = None) -> Path:
    """One wav holding every block's voice at its planned position."""
    import soundfile as sf

    if sample_rate is None:
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
        subtitle_path = write_srt(timeline, cfg, work / "preview.srt", log)

    size = probe_size(cfg, video)

    def run(with_subs: bool) -> subprocess.CompletedProcess:
        graph = build_filter_graph(
            timeline, cfg, size,
            subtitle_path=subtitle_path if with_subs else None,
            scale_height=cfg.preview_height,
        )
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
