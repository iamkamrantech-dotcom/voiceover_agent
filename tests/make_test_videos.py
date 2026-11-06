"""Generate slideshow test videos with FFmpeg, plus exact ground truth JSON.

Three videos land in tests/media/:

  cuts.mp4   4 images, hard cuts, 5s / 4s / 6s / 3s
  fades.mp4  3 images with motion INSIDE them, 0.8s crossfades
  zoom.mp4   4 images, slow Ken Burns zoom on each, hard cuts

Each gets a <name>.json holding the true cut times, fade windows and scene
boundaries, so tests check against real numbers instead of eyeballing.

    venv\\Scripts\\python.exe tests\\make_test_videos.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agent.config import Config  # noqa: E402

MEDIA = PROJECT_ROOT / "tests" / "media"
IMAGES = MEDIA / "images"

FPS = 25
W, H = 640, 360
SRC_W, SRC_H = 1280, 720

CUT_DURATIONS = [5.0, 4.0, 6.0, 3.0]
FADE_DURATIONS = [6.0, 6.0, 6.0]
FADE_LENGTH = 0.8
ZOOM_DURATIONS = [5.0, 5.0, 5.0, 5.0]

# Distinct, strongly different base colours so a missed cut is unambiguous.
PALETTE = [
    (196, 64, 48), (46, 110, 196), (72, 168, 86),
    (208, 170, 40), (138, 68, 180), (40, 170, 176), (210, 110, 50),
]


def log(msg: str) -> None:
    print(f"[agent] {msg}", flush=True)


def make_image(index: int, path: Path) -> None:
    """A distinct, detailed picture. Detail matters: a flat colour would make
    the zoom video trivially easy, and we want zoom to produce real pixel
    change that a naive detector could mistake for a cut."""
    rng = np.random.default_rng(1000 + index)
    base = np.array(PALETTE[index % len(PALETTE)], dtype=np.float32)

    # Diagonal gradient background
    yy, xx = np.mgrid[0:SRC_H, 0:SRC_W].astype(np.float32)
    ramp = (xx / SRC_W * 0.6 + yy / SRC_H * 0.4)[..., None]
    canvas = base[None, None, :] * (0.55 + 0.75 * ramp)

    # Fine texture so zooming actually changes pixels
    canvas += rng.normal(0, 7.0, size=(SRC_H, SRC_W, 3))
    canvas = np.clip(canvas, 0, 255).astype(np.uint8)

    img = Image.fromarray(canvas)
    draw = ImageDraw.Draw(img)

    # Shapes, seeded per image so every picture is structurally different
    for _ in range(9):
        x0 = int(rng.integers(40, SRC_W - 260))
        y0 = int(rng.integers(40, SRC_H - 260))
        w = int(rng.integers(90, 250))
        h = int(rng.integers(90, 250))
        colour = tuple(int(c) for c in rng.integers(30, 230, size=3))
        if rng.random() < 0.5:
            draw.ellipse([x0, y0, x0 + w, y0 + h], fill=colour)
        else:
            draw.rectangle([x0, y0, x0 + w, y0 + h], fill=colour)

    # Big index block, easy to read when checking keyframes by eye
    draw.rectangle([40, 40, 200, 200], fill=(255, 255, 255))
    draw.rectangle([70, 70, 70 + 20 * (index + 1), 170], fill=(0, 0, 0))

    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


def run_ffmpeg(cfg: Config, args: list[str], out: Path) -> None:
    cmd = [cfg.ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error", *args, str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stderr)
        raise SystemExit(f"FFmpeg failed for {out.name}")


def probe_duration(cfg: Config, path: Path) -> float:
    proc = subprocess.run(
        [cfg.ffprobe_bin, "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return 0.0


# --------------------------------------------------------------------------

def build_cuts(cfg: Config) -> dict:
    """4 images, hard cuts, no motion at all."""
    images = []
    for i in range(len(CUT_DURATIONS)):
        p = IMAGES / f"cut_{i}.png"
        make_image(i, p)
        images.append(p)

    args: list[str] = []
    for dur, img in zip(CUT_DURATIONS, images):
        args += ["-loop", "1", "-t", f"{dur}", "-i", str(img)]

    chains = [
        f"[{i}:v]fps={FPS},scale={W}:{H},setsar=1,format=yuv420p[v{i}]"
        for i in range(len(images))
    ]
    concat_in = "".join(f"[v{i}]" for i in range(len(images)))
    filt = ";".join(chains) + f";{concat_in}concat=n={len(images)}:v=1:a=0[out]"

    out = MEDIA / "cuts.mp4"
    run_ffmpeg(cfg, args + [
        "-filter_complex", filt, "-map", "[out]",
        "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
        "-pix_fmt", "yuv420p", "-r", str(FPS),
    ], out)

    cuts, scenes, t = [], [], 0.0
    for i, dur in enumerate(CUT_DURATIONS):
        scenes.append({"index": i, "start": round(t, 3), "end": round(t + dur, 3)})
        t += dur
        if i < len(CUT_DURATIONS) - 1:
            cuts.append({"time": round(t, 3), "frame": int(round(t * FPS))})

    return {
        "video": out.name, "type": "hard_cuts", "fps": FPS,
        "duration": round(t, 3), "scenes": scenes,
        "hard_cuts": cuts, "fades": [],
    }


def build_fades(cfg: Config) -> dict:
    """3 images with a box moving INSIDE each one, joined by 0.8s crossfades.
    The internal motion must not be mistaken for a transition."""
    images = []
    for i in range(len(FADE_DURATIONS)):
        p = IMAGES / f"fade_{i}.png"
        make_image(i + 10, p)
        images.append(p)

    args: list[str] = []
    for dur, img in zip(FADE_DURATIONS, images):
        args += ["-loop", "1", "-t", f"{dur}", "-i", str(img)]

    chains = []
    for i, dur in enumerate(FADE_DURATIONS):
        # A box sweeping across the frame: real motion, same picture.
        x_expr = f"40+{(W - 140)}*t/{dur}"
        y_expr = f"{60 + 70 * i}+40*sin(2*PI*t/{dur})"
        chains.append(
            f"[{i}:v]fps={FPS},scale={W}:{H},setsar=1,"
            f"drawbox=x='{x_expr}':y='{y_expr}':w=90:h=90:"
            f"color=white@0.95:t=fill,format=yuv420p[v{i}]"
        )

    # xfade offsets accumulate on the already-faded timeline.
    fades, filt_parts, prev_label, timeline = [], list(chains), "[v0]", FADE_DURATIONS[0]
    for i in range(1, len(FADE_DURATIONS)):
        offset = timeline - FADE_LENGTH
        label = f"[x{i}]" if i < len(FADE_DURATIONS) - 1 else "[out]"
        filt_parts.append(
            f"{prev_label}[v{i}]xfade=transition=fade:"
            f"duration={FADE_LENGTH}:offset={offset:.3f}{label}"
        )
        fades.append({
            "start": round(offset, 3),
            "end": round(offset + FADE_LENGTH, 3),
            "mid": round(offset + FADE_LENGTH / 2, 3),
            "mid_frame": int(round((offset + FADE_LENGTH / 2) * FPS)),
        })
        timeline = timeline + FADE_DURATIONS[i] - FADE_LENGTH
        prev_label = label

    out = MEDIA / "fades.mp4"
    run_ffmpeg(cfg, args + [
        "-filter_complex", ";".join(filt_parts), "-map", "[out]",
        "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
        "-pix_fmt", "yuv420p", "-r", str(FPS),
    ], out)

    # Scene boundaries use the fade MIDPOINT, so scenes stay contiguous.
    scenes, start = [], 0.0
    for i, f in enumerate(fades):
        scenes.append({"index": i, "start": round(start, 3), "end": f["mid"]})
        start = f["mid"]
    scenes.append({"index": len(fades), "start": round(start, 3),
                   "end": round(timeline, 3)})

    return {
        "video": out.name, "type": "crossfades", "fps": FPS,
        "duration": round(timeline, 3), "scenes": scenes,
        "hard_cuts": [], "fades": fades,
    }


def build_zoom(cfg: Config) -> dict:
    """4 images, each slowly zooming, hard cuts between them.
    The zoom must NOT be reported as a transition."""
    images = []
    for i in range(len(ZOOM_DURATIONS)):
        p = IMAGES / f"zoom_{i}.png"
        make_image(i + 20, p)
        images.append(p)

    args: list[str] = []
    for dur, img in zip(ZOOM_DURATIONS, images):
        args += ["-loop", "1", "-t", f"{dur}", "-i", str(img)]

    chains = []
    for i in range(len(images)):
        # zoompan keeps the output size fixed while magnifying the source,
        # which is what a Ken Burns move looks like.
        chains.append(
            f"[{i}:v]fps={FPS},"
            f"zoompan=z='min(1+0.0025*on,1.35)':d=1:"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"s={W}x{H}:fps={FPS},setsar=1,format=yuv420p[v{i}]"
        )
    concat_in = "".join(f"[v{i}]" for i in range(len(images)))
    filt = ";".join(chains) + f";{concat_in}concat=n={len(images)}:v=1:a=0[out]"

    out = MEDIA / "zoom.mp4"
    run_ffmpeg(cfg, args + [
        "-filter_complex", filt, "-map", "[out]",
        "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
        "-pix_fmt", "yuv420p", "-r", str(FPS),
    ], out)

    cuts, scenes, t = [], [], 0.0
    for i, dur in enumerate(ZOOM_DURATIONS):
        scenes.append({"index": i, "start": round(t, 3), "end": round(t + dur, 3)})
        t += dur
        if i < len(ZOOM_DURATIONS) - 1:
            cuts.append({"time": round(t, 3), "frame": int(round(t * FPS))})

    return {
        "video": out.name, "type": "zoom_hard_cuts", "fps": FPS,
        "duration": round(t, 3), "scenes": scenes,
        "hard_cuts": cuts, "fades": [],
        "note": "slow zoom inside every scene; none of it is a transition",
    }


def build_cuts_with_audio(cfg: Config) -> Path:
    """cuts.mp4 plus a quiet tone bed.

    The other three videos have no audio at all, which leaves
    --keep-original-audio with nothing to test. This variant exists purely
    for that, so the original fixtures and their ground truth stay untouched.
    """
    source = MEDIA / "cuts.mp4"
    out = MEDIA / "cuts_with_audio.mp4"
    cmd = [
        cfg.ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(source),
        "-f", "lavfi", "-i", "sine=frequency=180:sample_rate=48000",
        "-f", "lavfi", "-i", "sine=frequency=270:sample_rate=48000",
        "-filter_complex",
        "[1:a][2:a]amix=inputs=2:normalize=1,volume=-26dB[a]",
        "-map", "0:v", "-map", "[a]", "-c:v", "copy",
        "-c:a", "aac", "-b:a", "128k", "-shortest", str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stderr)
        raise SystemExit("FFmpeg failed for cuts_with_audio.mp4")
    return out


def build_music_bed(cfg: Config, seconds: float = 30.0) -> Path:
    """A simple pad so the music path can be tested without shipping any
    copyrighted audio."""
    out = MEDIA / "music_bed.wav"
    fade = 2.0
    cmd = [
        cfg.ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"sine=frequency=196:duration={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=262:duration={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}",
        "-filter_complex",
        f"[0][1][2]amix=inputs=3:normalize=1,"
        f"afade=t=in:st=0:d={fade},"
        f"afade=t=out:st={seconds - fade}:d={fade},volume=-6dB[a]",
        "-map", "[a]", "-ar", "48000", str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stderr)
        raise SystemExit("FFmpeg failed for music_bed.wav")
    return out


def main() -> int:
    cfg = Config.load()
    MEDIA.mkdir(parents=True, exist_ok=True)
    IMAGES.mkdir(parents=True, exist_ok=True)

    log(f"FFmpeg: {cfg.ffmpeg_bin}")
    log("Building test videos...")

    for builder in (build_cuts, build_fades, build_zoom):
        truth = builder(cfg)
        path = MEDIA / truth["video"]
        truth["measured_duration"] = round(probe_duration(cfg, path), 3)
        (MEDIA / f"{Path(truth['video']).stem}.json").write_text(
            json.dumps(truth, indent=2), encoding="utf-8"
        )
        log(f"{truth['video']}: {truth['duration']}s "
            f"(measured {truth['measured_duration']}s), "
            f"{len(truth['scenes'])} scenes, "
            f"{len(truth['hard_cuts'])} cuts, {len(truth['fades'])} fades")

    extra = build_cuts_with_audio(cfg)
    log(f"{extra.name}: cuts.mp4 + halki si audio (--keep-original-audio ke liye)")

    bed = build_music_bed(cfg)
    log(f"{bed.name}: test music bed")

    log(f"All test videos are in {MEDIA}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
