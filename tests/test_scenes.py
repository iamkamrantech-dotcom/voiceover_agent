"""Check scene detection against the exact truth in tests/media/*.json.

Pass conditions:
  * hard cuts      - exact to the frame
  * fade midpoints - within 2 frames
  * zoom video     - no false transitions at all
  * scene count    - matches

If input/video.mp4 exists, detection also runs on it and prints the scene
list for eyeball checking (no pass/fail - there is no ground truth for it).

    venv\\Scripts\\python.exe tests\\test_scenes.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agent import log as agent_log          # noqa: E402
from agent.config import Config             # noqa: E402
from agent.scenes import detect_scenes      # noqa: E402

MEDIA = PROJECT_ROOT / "tests" / "media"
CUT_TOLERANCE_FRAMES = 0
FADE_TOLERANCE_FRAMES = 2


class Check:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str, str, bool]] = []

    def add(self, video: str, what: str, expected: str, got: str, ok: bool) -> None:
        self.rows.append((video, what, expected, got, ok))

    @property
    def passed(self) -> bool:
        return all(r[4] for r in self.rows)

    def table(self) -> str:
        width = max(len(r[1]) for r in self.rows) if self.rows else 10
        lines = [
            f"  {'video':<11}{'check':<{width + 2}}{'expected':<22}{'got':<22}result",
            "  " + "-" * (11 + width + 2 + 22 + 22 + 6),
        ]
        for video, what, expected, got, ok in self.rows:
            lines.append(
                f"  {video:<11}{what:<{width + 2}}{expected:<22}{got:<22}"
                f"{'PASS' if ok else 'FAIL'}"
            )
        return "\n".join(lines)


def check_video(name: str, cfg: Config, log, check: Check) -> None:
    truth = json.loads((MEDIA / f"{name}.json").read_text(encoding="utf-8"))
    video = MEDIA / truth["video"]
    fps = truth["fps"]

    result = detect_scenes(
        video, cfg,
        keyframe_dir=cfg.output_dir / "scenes" / name,
        log=log,
    )

    got_cuts = [t for t in result.transitions if t.kind == "cut"]
    got_fades = [t for t in result.transitions if t.kind == "fade"]
    want_cuts = truth["hard_cuts"]
    want_fades = truth["fades"]

    # --- counts ---
    check.add(name, "scene count", str(len(truth["scenes"])),
              str(len(result.scenes)), len(result.scenes) == len(truth["scenes"]))
    check.add(name, "hard cut count", str(len(want_cuts)), str(len(got_cuts)),
              len(got_cuts) == len(want_cuts))
    check.add(name, "fade count", str(len(want_fades)), str(len(got_fades)),
              len(got_fades) == len(want_fades))

    # --- hard cuts, frame exact ---
    for i, want in enumerate(want_cuts):
        if i < len(got_cuts):
            got_frame = got_cuts[i].mid_frame
            delta = abs(got_frame - want["frame"])
            ok = delta <= CUT_TOLERANCE_FRAMES
            got_text = f"f{got_frame} ({got_frame / fps:.2f}s)"
        else:
            ok, got_text = False, "missing"
        check.add(name, f"cut {i + 1} frame",
                  f"f{want['frame']} ({want['time']:.2f}s)", got_text, ok)

    # --- fade midpoints, within tolerance ---
    for i, want in enumerate(want_fades):
        if i < len(got_fades):
            got_frame = got_fades[i].mid_frame
            delta = abs(got_frame - want["mid_frame"])
            ok = delta <= FADE_TOLERANCE_FRAMES
            got_text = f"f{got_frame} ({got_frame / fps:.2f}s) d={delta}"
        else:
            ok, got_text = False, "missing"
        check.add(name, f"fade {i + 1} mid",
                  f"f{want['mid_frame']} (+/-{FADE_TOLERANCE_FRAMES})",
                  got_text, ok)

    # --- zoom video must not invent transitions ---
    if truth["type"] == "zoom_hard_cuts":
        extra = len(result.transitions) - len(want_cuts)
        check.add(name, "no false cuts (zoom)", "0 extra", f"{extra} extra",
                  extra == 0)

    # --- hold_end must never land inside a fade ---
    bad = 0
    for scene in result.scenes:
        t = scene.transition_out
        if t and t.kind == "fade" and scene.hold_end_frame >= t.start_frame:
            bad += 1
    check.add(name, "hold_end is clean", "0 inside fade", f"{bad} inside fade",
              bad == 0)


def show_client_video(cfg: Config, log) -> None:
    video = cfg.input_video
    print()
    print("=" * 78)
    print("  CLIENT VIDEO")
    print("=" * 78)
    if not video.is_file():
        print(f"  {video} mojood nahi hai - skip.")
        print("  Apni video yahan rakhein to agli baar scene list print hogi.")
        return

    result = detect_scenes(video, cfg,
                           keyframe_dir=cfg.output_dir / "scenes" / video.stem,
                           log=log)
    print()
    print(f"  {'#':<4}{'start':>9}{'end':>9}{'hold_end':>10}{'dur':>8}  in -> out")
    print("  " + "-" * 68)
    for s in result.scenes:
        t_in = s.transition_in.kind if s.transition_in else "start"
        t_out = s.transition_out.kind if s.transition_out else "end"
        print(f"  {s.index:<4}{s.start:9.2f}{s.end:9.2f}{s.hold_end:10.2f}"
              f"{s.duration:8.2f}  {t_in} -> {t_out}")
    print(f"\n  Keyframes: {cfg.output_dir / 'scenes' / video.stem}")


def main() -> int:
    cfg = Config.load()
    log = agent_log.setup(cfg.log_dir, cfg.log_level, run_name="test_scenes")

    missing = [n for n in ("cuts", "fades", "zoom")
               if not (MEDIA / f"{n}.json").is_file()]
    if missing:
        log.user("Test videos nahi mile - pehle tests/make_test_videos.py chalayein.")
        return 1

    check = Check()
    for name in ("cuts", "fades", "zoom"):
        check_video(name, cfg, log, check)

    print()
    print("=" * 78)
    print("  SCENE DETECTION TEST")
    print("=" * 78)
    print(check.table())
    print()
    total = len(check.rows)
    failed = sum(1 for r in check.rows if not r[4])
    print(f"  {total - failed}/{total} checks passed")

    show_client_video(cfg, log)

    print()
    if check.passed:
        log.user("Sab test pass ho gaye.")
    else:
        log.user(f"{failed} test fail hue.")
    return 0 if check.passed else 1


if __name__ == "__main__":
    sys.exit(main())
