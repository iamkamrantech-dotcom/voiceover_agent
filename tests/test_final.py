"""Final-output tests: mastering, subtitles, living hold, QC.

Mock runs on all three videos check the finished mp4, not the plan:
  * loudness within 0.5 LU of -14, true peak <= -1.5 dB
  * video duration exact to the frame (video packets, not container)
  * SRT parses, monotonic, non-overlapping, >= 0.8s, <= 42 chars
  * the living hold MOVES (consecutive frames differ) while staying close
    to the hold_end frame
  * no jump into a fade: the last hold frame matches the first frame of
    the transition, proving the zoom eased back to 1.0
  * --keep-original-audio works on cuts_with_audio.mp4

    venv\\Scripts\\python.exe tests\\test_final.py
    venv\\Scripts\\python.exe tests\\test_final.py --mock-only
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agent import log as agent_log                  # noqa: E402
from agent.config import Config                     # noqa: E402
from agent.pipeline import run_job                  # noqa: E402
from agent.subtitles import parse_srt               # noqa: E402

MEDIA = PROJECT_ROOT / "tests" / "media"
SCRIPTS = PROJECT_ROOT / "tests" / "scripts"

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
SHORT_SCRIPT = "A short line here. Another brief one. And a third. Finally this one."


class Results:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str, str, bool]] = []

    def add(self, case: str, name: str, expected: str, got: str, ok: bool):
        self.rows.append((case, name, expected, got, ok))

    def report(self) -> bool:
        c = max(len(r[0]) for r in self.rows)
        w = max(len(r[1]) for r in self.rows)
        print(f"  {'case':<{c + 2}}{'check':<{w + 2}}{'expected':<22}"
              f"{'got':<24}result")
        print("  " + "-" * (c + w + 54))
        for case, name, expected, got, ok in self.rows:
            print(f"  {case:<{c + 2}}{name:<{w + 2}}{expected:<22}{got:<24}"
                  f"{'PASS' if ok else 'FAIL'}")
        failed = sum(1 for r in self.rows if not r[4])
        print(f"\n  {len(self.rows) - failed}/{len(self.rows)} checks passed")
        return failed == 0


def frame_at(video: Path, time_s: float, fps: float):
    cap = cv2.VideoCapture(str(video))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    index = int(round(max(0.0, time_s) * fps))
    if total > 0:
        index = min(index, total - 1)
    cap.set(cv2.CAP_PROP_POS_FRAMES, index)
    ok, frame = cap.read()
    cap.release()
    return frame if ok else None


def mad(a, b) -> float:
    if a is None or b is None:
        return float("nan")
    if a.shape != b.shape:
        b = cv2.resize(b, (a.shape[1], a.shape[0]))
    return float(np.mean(np.abs(a.astype(np.float32) - b.astype(np.float32))))


def write_script(name: str, text: str) -> Path:
    SCRIPTS.mkdir(parents=True, exist_ok=True)
    path = SCRIPTS / name
    path.write_text(text, encoding="utf-8")
    return path


def check_subtitles(case: str, srt: Path, cfg: Config, r: Results) -> None:
    cues = parse_srt(srt)
    r.add(case, "srt has cues", ">0", str(len(cues)), len(cues) > 0)
    if not cues:
        return

    too_long = [c for c in cues if len(c.text) > cfg.subtitle_max_chars]
    r.add(case, "srt line length", f"<={cfg.subtitle_max_chars} chars",
          f"max {max(len(c.text) for c in cues)}", not too_long)

    # A cue must reach the minimum UNLESS its neighbours leave less room
    # than that - in which case it must occupy every frame available.
    # Overlapping subtitles are broken; a slightly short one is not.
    eps = 0.02
    short_and_shrinkable = []
    squeezed = 0
    for i, cue in enumerate(cues):
        if cue.duration >= cfg.subtitle_min_seconds - eps:
            continue
        floor = cues[i - 1].end if i else 0.0
        ceiling = cues[i + 1].start if i + 1 < len(cues) else float("inf")
        maximal = (cue.start - floor <= 0.06 + eps
                   and ceiling - cue.end <= 0.06 + eps)
        if maximal:
            squeezed += 1
        else:
            short_and_shrinkable.append(cue)

    note = f"min {min(c.duration for c in cues):.2f}s"
    if squeezed:
        note += f", {squeezed} squeezed"
    r.add(case, "srt min on screen", f">={cfg.subtitle_min_seconds}s or maximal",
          note, not short_and_shrinkable)

    overlaps = sum(1 for a, b in zip(cues, cues[1:]) if b.start < a.end - 1e-3)
    r.add(case, "srt no overlap", "0", str(overlaps), overlaps == 0)

    monotonic = all(b.start >= a.start for a, b in zip(cues, cues[1:]))
    r.add(case, "srt monotonic", "yes", "yes" if monotonic else "no", monotonic)


def check_living_hold(case: str, video_src: Path, final: Path, timeline,
                      cfg: Config, r: Results) -> None:
    fps = timeline.fps
    frozen = [b for b in timeline.blocks
              if b.freeze_seconds > cfg.hold_motion_min_seconds]
    if not frozen:
        r.add(case, "living hold", "n/a", "no long freeze", True)
        return

    moved_min = 1e9        # smallest movement between hold frames
    drift_max = 0.0        # furthest any hold frame got from hold_end
    jump_max = 0.0         # biggest step into a fade
    fades = 0

    for b in frozen:
        clean = max(0.0, b.split_at - b.src_start)
        start = b.out_start + clean * b.video_slowdown
        end = start + b.freeze_seconds
        hold_frame = frame_at(video_src, b.hold_end, fps)

        samples = []
        for fraction in (0.15, 0.4, 0.65, 0.9):
            t = start + b.freeze_seconds * fraction
            if t < end - 1e-3:
                got = frame_at(final, t, fps)
                if got is not None:
                    samples.append(got)
                    drift_max = max(drift_max, mad(got, hold_frame))

        for first, second in zip(samples, samples[1:]):
            moved_min = min(moved_min, mad(first, second))

        # Into the transition: the zoom must have returned to 1.0, so the
        # last held frame and the first transition frame should line up.
        if b.out_transition == "fade":
            fades += 1
            last_hold = frame_at(final, end - 1.5 / fps, fps)
            first_tail = frame_at(final, end + 0.5 / fps, fps)
            jump_max = max(jump_max, mad(last_hold, first_tail))

    r.add(case, "hold is moving", ">0.05 between frames",
          f"{moved_min:.3f}", moved_min > 0.05)
    r.add(case, "hold stays on image", "<25 vs hold_end",
          f"{drift_max:.1f}", drift_max < 25.0)
    if fades:
        r.add(case, "no jump into fade", "<12 mean abs diff",
              f"{jump_max:.1f}", jump_max < 12.0)


def run_case(case: str, video_name: str, script_text: str, cfg: Config,
             log, r: Results, keep_original_audio: bool = False) -> None:
    video = MEDIA / video_name
    script = write_script(f"{case}_auto.txt", script_text)
    cfg.tts_cache_dir = cfg.output_dir / "_test_cache" / f"final_{case}"

    result = run_job(video, script, cfg, log, engine="mock", draft=True,
                     keep_original_audio=keep_original_audio)

    import json

    timeline_data = json.loads(result.timeline_json.read_text(encoding="utf-8"))

    class _W:
        def __init__(self, d): self.__dict__.update(d)

    class _B:
        def __init__(self, d):
            self.__dict__.update(d)
            self.words = [_W(w) for w in d.get("words", [])]

    class _T:
        def __init__(self, d):
            self.__dict__.update(d)
            self.blocks = [_B(b) for b in d["blocks"]]

    timeline = _T(timeline_data)
    report = result.report

    # ---- loudness --------------------------------------------------------
    if report.loudness_i is not None:
        delta = abs(report.loudness_i - cfg.target_lufs)
        r.add(case, "loudness", f"{cfg.target_lufs:.0f} +/-0.5 LU",
              f"{report.loudness_i:.2f} LUFS", delta <= 0.5)
    else:
        r.add(case, "loudness", "measured", "missing", False)

    if report.loudness_tp is not None:
        r.add(case, "true peak", f"<={cfg.target_true_peak:.1f} dB",
              f"{report.loudness_tp:.2f} dB",
              report.loudness_tp <= cfg.target_true_peak + 0.05)

    r.add(case, "no clipping", "no", "yes" if report.clipping else "no",
          not report.clipping)

    # ---- duration --------------------------------------------------------
    r.add(case, "video duration", f"{report.duration_expected:.2f}s +/-1f",
          f"{report.duration_actual:.2f}s "
          f"({report.duration_actual - report.duration_expected:+.3f})",
          report.duration_ok)

    # ---- subtitles -------------------------------------------------------
    check_subtitles(case, result.subtitles, cfg, r)

    # ---- living hold -----------------------------------------------------
    check_living_hold(case, video, result.video, timeline, cfg, r)

    # ---- audio present ---------------------------------------------------
    from agent.audio_master import has_audio_stream

    r.add(case, "final has audio", "yes",
          "yes" if has_audio_stream(cfg, result.video) else "no",
          has_audio_stream(cfg, result.video))


def real_run(cfg: Config, log, r: Results) -> None:
    print()
    print("=" * 92)
    print("  REAL RUN - Chatterbox best-of-1, fades.mp4, with music")
    print("=" * 92)

    cfg.tts_cache_dir = cfg.output_dir / "_test_cache" / "final_real"
    out = cfg.output_dir / "final_test.mp4"
    result = run_job(
        MEDIA / "fades.mp4", SCRIPTS / "fades_demo.txt", cfg, log,
        music=MEDIA / "music_bed.wav", engine=cfg.tts_engine, draft=True,
        out_path=out,
    )

    report = result.report
    r.add("real", "final file written", "yes",
          "yes" if out.is_file() else "no", out.is_file())
    if report.loudness_i is not None:
        r.add("real", "loudness", f"{cfg.target_lufs:.0f} +/-0.5 LU",
              f"{report.loudness_i:.2f} LUFS",
              abs(report.loudness_i - cfg.target_lufs) <= 0.5)
    r.add("real", "video duration", f"{report.duration_expected:.2f}s +/-1f",
          f"{report.duration_actual:.2f}s", report.duration_ok)
    if report.drift_applicable and report.drift_median is not None:
        r.add("real", "word drift median", f"<={cfg.max_word_drift:.2f}s",
              f"{report.drift_median:.3f}s "
              f"(p95 {report.drift_p95:.2f}, max {report.drift_max:.2f})",
              report.drift_ok)
    check_subtitles("real", result.subtitles, cfg, r)


def main() -> int:
    cfg = Config.load()
    log = agent_log.setup(cfg.log_dir, cfg.log_level, run_name="test_final")

    needed = ["cuts.mp4", "fades.mp4", "zoom.mp4", "cuts_with_audio.mp4",
              "music_bed.wav"]
    missing = [n for n in needed if not (MEDIA / n).is_file()]
    if missing:
        log.user(f"These files are missing: {missing}. Run "
                 f"tests/make_test_videos.py first.")
        return 1

    r = Results()
    run_case("cuts", "cuts.mp4", LONG_SCRIPT, cfg, log, r)
    run_case("fades", "fades.mp4", LONG_SCRIPT, cfg, log, r)
    run_case("zoom", "zoom.mp4", SHORT_SCRIPT, cfg, log, r)
    run_case("orig-audio", "cuts_with_audio.mp4", LONG_SCRIPT, cfg, log, r,
             keep_original_audio=True)

    if "--mock-only" not in sys.argv:
        real_run(cfg, log, r)
    else:
        print("\n  (real run skipped: --mock-only)")

    print()
    print("=" * 92)
    print("  FINAL OUTPUT TEST")
    print("=" * 92)
    ok = r.report()
    print()
    log.user("All tests passed." if ok else "Some tests failed.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
