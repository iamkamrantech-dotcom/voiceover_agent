"""Final-quality render. Same timeline, same graph as the preview.

Source resolution and frame rate, x264 high profile at CRF 18 preset slow,
BT.709 tags, AAC 320k at 48 kHz, faststart so it begins playing before it
has finished downloading.

When nothing is re-timed (no slowdown, no freeze anywhere) and subtitles are
not burned in, the video stream is COPIED rather than re-encoded - there is
nothing to change, and re-encoding would only throw away quality.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.config import Config  # noqa: E402
from agent import log as agent_log  # noqa: E402
from agent.align import Timeline  # noqa: E402
from agent.preview import probe_size  # noqa: E402
from agent.video_graph import build_filter_graph, needs_retiming  # noqa: E402


@dataclass
class RenderResult:
    path: str
    stream_copied: bool
    burned_subtitles: bool
    width: int
    height: int
    fps: float


def render_final(video: Path, timeline: Timeline, audio: Path, out_path: Path,
                 cfg: Config | None = None, subtitle_path: Path | None = None,
                 burn_subs: bool = False, work_dir: Path | None = None,
                 log=None) -> RenderResult:
    cfg = cfg or Config.load()
    log = log or agent_log.get()
    work = Path(work_dir or (cfg.work_dir / "render"))
    work.mkdir(parents=True, exist_ok=True)

    size = probe_size(cfg, video)
    retimed = needs_retiming(timeline)
    burn = burn_subs and subtitle_path is not None
    copy_video = not retimed and not burn

    gop = max(1, int(round(timeline.fps / cfg.final_gop_divisor)))

    common_out = [
        # AUDIO_CODEC was declared in config and ignored here, so changing it
        # did nothing. Honour it; aac stays the default because it is what
        # YouTube wants.
        "-c:a", cfg.audio_codec or "aac", "-b:a", cfg.final_audio_bitrate,
        "-ar", str(cfg.final_audio_rate), "-ac", "2",
        "-movflags", "+faststart",
        "-t", f"{timeline.output_duration:.6f}",
    ]

    out_path.parent.mkdir(parents=True, exist_ok=True)

    if copy_video:
        log.user("Nothing is re-timed, copying the video stream "
                 "(no quality loss).")
        cmd = [
            cfg.ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(video), "-i", str(audio),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", *common_out, str(out_path),
        ]
    else:
        graph = build_filter_graph(
            timeline, cfg, size,
            subtitle_path=subtitle_path if burn else None,
            scale_height=None,  # keep source resolution
        )
        (work / "final_graph.txt").write_text(graph, encoding="utf-8")
        log.user(f"Rendering final video ({size[0]}x{size[1]}, "
                 f"CRF {cfg.video_crf}, preset {cfg.video_preset})...")
        cmd = [
            cfg.ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(video), "-i", str(audio),
            "-filter_complex", graph,
            "-map", "[vout]", "-map", "1:a:0",
            "-c:v", cfg.video_codec,
            "-profile:v", cfg.final_profile,
            "-crf", str(cfg.video_crf),
            "-preset", cfg.video_preset,
            "-pix_fmt", "yuv420p",
            "-g", str(gop), "-keyint_min", str(gop),
            "-colorspace", "bt709", "-color_primaries", "bt709",
            "-color_trc", "bt709",
            *common_out, str(out_path),
        ]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        log.error(f"Final render fail: {proc.stderr.strip()[-600:]}")
        raise RuntimeError("FFmpeg final render failed")

    log.user(f"Final video ready: {out_path}")
    return RenderResult(str(out_path), copy_video, burn, size[0], size[1],
                        timeline.fps)
