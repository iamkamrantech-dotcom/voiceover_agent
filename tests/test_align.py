"""Alignment + preview tests on the generated test videos.

Checks, on all three videos, with the mock synthesizer:
  * no voice overlaps the next block, BLOCK_GAP respected
  * every voice ends inside its own segment
  * frames during a freeze are identical to the hold_end frame, and are
    NOT a blended fade frame (extracted from the rendered preview)
  * output duration = source duration + total extension, within 1 frame
  * word timings still line up with the audio after stretching

    venv\\Scripts\\python.exe tests\\test_align.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agent import log as agent_log                      # noqa: E402
from agent.align import check_timeline, plan_timeline   # noqa: E402
from agent.config import Config                         # noqa: E402
from agent.preview import render_preview                # noqa: E402
from agent.scenes import detect_scenes                  # noqa: E402
from agent.script_parser import parse_script            # noqa: E402
from agent.voice_stage import run_voice_stage           # noqa: E402

MEDIA = PROJECT_ROOT / "tests" / "media"

# Deliberately long, so blocks overflow and the video must be extended.
LONG_SCRIPT = (
    "Welcome back to the channel and thank you so much for joining me here "
    "again today for another one of these long rambling introductions. "
    "This first location is a place that almost nobody in the entire world "
    "has ever actually managed to visit in person before now. "
    "The second spot on our list today is even stranger than the first one "
    "and it is considerably harder to reach by any normal means. "
    "Our third and final stop sits high above the clouds all year round and "
    "it is genuinely one of the most beautiful places anywhere on earth."
)

SHORT_SCRIPT = (
    "A short line here. Another brief one. And a third. Finally this one."
)


class Results:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str, str, bool]] = []

    def add(self, video: str, name: str, expected: str, got: str, ok: bool):
        self.rows.append((video, name, expected, got, ok))

    def report(self) -> bool:
        w = max(len(r[1]) for r in self.rows)
        print(f"  {'video':<8}{'check':<{w + 2}}{'expected':<24}{'got':<26}result")
        print("  " + "-" * (8 + w + 2 + 24 + 26 + 6))
        for video, name, expected, got, ok in self.rows:
            print(f"  {video:<8}{name:<{w + 2}}{expected:<24}{got:<26}"
                  f"{'PASS' if ok else 'FAIL'}")
        failed = sum(1 for r in self.rows if not r[4])
        print(f"\n  {len(self.rows) - failed}/{len(self.rows)} checks passed")
        return failed == 0


def frame_at(video: Path, time_s: float, fps: float) -> np.ndarray | None:
    """Seek by FRAME INDEX, not milliseconds. Millisecond seeking is
    approximate and returns nothing at all near the end of a file."""
    cap = cv2.VideoCapture(str(video))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    index = int(round(max(0.0, time_s) * fps))
    if total > 0:
        index = min(index, total - 1)
    cap.set(cv2.CAP_PROP_POS_FRAMES, index)
    ok, frame = cap.read()
    cap.release()
    return frame if ok else None


def mad(a: np.ndarray, b: np.ndarray) -> float:
    if a is None or b is None:
        return 1e9
    if a.shape != b.shape:
        b = cv2.resize(b, (a.shape[1], a.shape[0]))
    return float(np.mean(np.abs(a.astype(np.float32) - b.astype(np.float32))))


def video_duration_of(cfg: Config, path: Path, fps: float) -> float:
    """Count VIDEO frames, not the container duration. The container is as
    long as its longest stream, so a short video stream would be hidden by
    the audio track and the check would pass when it should not."""
    import subprocess

    proc = subprocess.run(
        [cfg.ffprobe_bin, "-v", "error", "-select_streams", "v:0",
         "-count_packets", "-show_entries", "stream=nb_read_packets",
         "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    try:
        return int(proc.stdout.strip()) / fps
    except ValueError:
        return 0.0


def run_one(name: str, script: str, cfg: Config, log, r: Results) -> None:
    video = MEDIA / f"{name}.mp4"
    cache = cfg.output_dir / "_test_cache" / f"align_{name}"
    cfg.tts_cache_dir = cache
    # This suite checks the PLAIN freeze path: frames during a hold must be
    # identical to hold_end. The living-hold (moving) path is deliberately
    # a separate case, covered by tests/test_final.py.
    cfg.hold_motion = "none"

    detection = detect_scenes(video, cfg, log=log)
    split = parse_script(script, detection.scenes, cfg, log)
    audio = run_voice_stage(split.blocks, cfg, engine="mock", best_of=1, log=log)
    timeline = plan_timeline(detection, audio, cfg,
                             work_dir=cfg.work_dir / f"align_{name}", log=log)

    out = cfg.output_dir / "align_tests" / f"{name}_preview.mp4"
    render_preview(video, timeline, out, cfg,
                   work_dir=cfg.work_dir / f"preview_{name}", log=log)

    timeline.save(cfg.output_dir / "align_tests" / f"{name}_timeline.json")

    # ---- structural checks ----------------------------------------------
    problems = check_timeline(timeline, cfg)
    r.add(name, "no overlap / gap kept", "0 problems",
          f"{len(problems)} problems", not problems)
    if problems:
        for p in problems[:4]:
            log.warning(p)

    inside = all(b.silent or b.voice_end <= b.out_end + 1e-3
                 for b in timeline.blocks)
    r.add(name, "voice inside segment", "yes", "yes" if inside else "no", inside)

    # ---- duration --------------------------------------------------------
    expected = timeline.source_duration + timeline.total_extension
    actual = video_duration_of(cfg, out, timeline.fps)
    frame = 1.0 / timeline.fps
    close = abs(actual - expected) <= frame * 1.5
    r.add(name, "video duration", f"{expected:.2f}s +/-1f",
          f"{actual:.2f}s (d={actual - expected:+.3f})", close)

    # ---- word timings vs audio ------------------------------------------
    import soundfile as sf

    timing_ok = True
    detail = "ok"
    for b in timeline.blocks:
        if b.silent or not b.words:
            continue
        info = sf.info(b.wav)
        audio_len = info.frames / info.samplerate
        # voice_end must match the real audio length after every edit
        if abs(b.voice_duration - audio_len) > 0.05:
            timing_ok = False
            detail = f"block {b.block_index} len {b.voice_duration:.2f}!={audio_len:.2f}"
            break
        if b.words[-1].end > b.voice_end + 0.05:
            timing_ok = False
            detail = f"block {b.block_index} last word past end"
            break
        starts = [w.start for w in b.words]
        if starts != sorted(starts):
            timing_ok = False
            detail = f"block {b.block_index} words out of order"
            break
    r.add(name, "word timings match audio", "yes", detail, timing_ok)

    # ---- freeze frames ---------------------------------------------------
    frozen = [b for b in timeline.blocks if b.freeze_seconds > 0.05]
    if not frozen:
        r.add(name, "freeze frames clean", "n/a", "no freeze in this case", True)
        return

    fps = timeline.fps
    worst_hold = 0.0
    worst_margin = 1e9
    checked = 0
    blend_checked = 0

    for b in frozen:
        # Where the freeze sits on the OUTPUT timeline: the clean part is
        # trimmed to split_at and slowed, and the freeze follows it.
        clean_part = max(0.0, b.split_at - b.src_start)
        freeze_start = b.out_start + clean_part * b.video_slowdown
        freeze_end = freeze_start + b.freeze_seconds

        hold_frame = frame_at(video, b.hold_end, fps)
        if hold_frame is None:
            continue

        # Only a FADE has blended frames. For a hard cut there is nothing
        # to confuse the freeze with, so that comparison is meaningless.
        blend_frame = None
        if b.out_transition == "fade":
            mid = (b.out_transition_start + b.out_transition_end) / 2
            blend_frame = frame_at(video, mid, fps)

        for fraction in (0.2, 0.5, 0.8):
            t = freeze_start + b.freeze_seconds * fraction
            if t >= freeze_end - 1e-3:
                continue
            got = frame_at(out, t, fps)
            if got is None:
                continue
            d_hold = mad(got, hold_frame)
            worst_hold = max(worst_hold, d_hold)
            checked += 1
            if blend_frame is not None:
                worst_margin = min(worst_margin, mad(got, blend_frame) - d_hold)
                blend_checked += 1

    r.add(name, "freeze == hold_end frame", "<6 mean abs diff",
          f"{worst_hold:.2f} over {checked} frames",
          checked > 0 and worst_hold < 6.0)
    if blend_checked:
        r.add(name, "freeze is not a blend", "closer to hold_end",
              f"margin {worst_margin:+.1f} ({blend_checked}f)", worst_margin > 0)


def main() -> int:
    cfg = Config.load()
    log = agent_log.setup(cfg.log_dir, cfg.log_level, run_name="test_align")

    missing = [n for n in ("cuts", "fades", "zoom")
               if not (MEDIA / f"{n}.mp4").is_file()]
    if missing:
        log.user("Test videos nahi mile - pehle tests/make_test_videos.py chalayein.")
        return 1

    r = Results()
    run_one("cuts", LONG_SCRIPT, cfg, log, r)
    run_one("fades", LONG_SCRIPT, cfg, log, r)
    run_one("zoom", SHORT_SCRIPT, cfg, log, r)

    print()
    print("=" * 96)
    print("  ALIGNMENT + PREVIEW TEST")
    print("=" * 96)
    ok = r.report()
    print()
    log.user("Sab test pass ho gaye." if ok else "Kuch test fail hue.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
