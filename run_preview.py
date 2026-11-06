"""End-to-end preview: video + script -> narrated preview video.

    venv\\Scripts\\python.exe run_preview.py --video input/video.mp4 --script input/script.txt
    venv\\Scripts\\python.exe run_preview.py --video x.mp4 --script s.txt --draft

Steps: scene detection -> script splitting -> voice generation -> timeline
alignment -> fast preview render. Everything is driven by .env; the flags
below only override the handful of things worth changing per run.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from agent import log as agent_log                      # noqa: E402
from agent.align import check_timeline, plan_timeline   # noqa: E402
from agent.config import Config                         # noqa: E402
from agent.preview import render_preview                # noqa: E402
from agent.scenes import detect_scenes                  # noqa: E402
from agent.script_parser import parse_script_file       # noqa: E402
from agent.voice_stage import run_voice_stage, save_report  # noqa: E402


def timeline_table(timeline, split_method: str) -> str:
    lines = [
        "",
        "=" * 112,
        f"  TIMELINE   ({split_method} split, "
        f"{timeline.source_duration:.2f}s -> {timeline.output_duration:.2f}s, "
        f"+{timeline.total_extension:.2f}s)",
        "=" * 112,
        f"  {'#':<3}{'scenes':<9}{'src':>14}{'out':>14}{'voice':>14}"
        f"{'over':>7}{'pause':>7}{'speed':>7}{'slow':>7}{'freeze':>8}  text",
        "  " + "-" * 108,
    ]
    for b in timeline.blocks:
        scenes = ",".join(str(i) for i in b.scene_ids)
        src = f"{b.src_start:.2f}-{b.src_end:.2f}"
        out = f"{b.out_start:.2f}-{b.out_end:.2f}"
        if b.silent:
            voice = "(silent)"
        else:
            voice = f"{b.voice_start:.2f}-{b.voice_end:.2f}"
        text = "" if b.silent else " ".join(w.word for w in b.words)[:30]
        lines.append(
            f"  {b.block_index:<3}{scenes:<9}{src:>14}{out:>14}{voice:>14}"
            f"{b.overflow_before:7.2f}{b.pause_saved:7.2f}"
            f"{b.stretch:7.3f}{b.video_slowdown:7.3f}{b.freeze_seconds:8.2f}"
            f"  {text}"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--video", required=True, help="slideshow video")
    parser.add_argument("--script", required=True, help="English script (.txt)")
    parser.add_argument("--out", default=None, help="output preview mp4")
    parser.add_argument("--draft", action="store_true",
                        help="best-of-1 instead of TTS_BEST_OF_N (much faster)")
    parser.add_argument("--engine", default=None,
                        help="standard | turbo | mock (default: .env)")
    parser.add_argument("--no-subtitles", action="store_true",
                        help="skip burned-in subtitles")
    args = parser.parse_args()

    cfg = Config.load()
    cfg.ensure_dirs()
    if args.no_subtitles:
        cfg.preview_subtitles = False

    log = agent_log.setup(cfg.log_dir, cfg.log_level, run_name="preview")

    video = Path(args.video)
    script = Path(args.script)
    if not video.is_file():
        log.error(f"Video not found: {video}")
        return 1
    if not script.is_file():
        log.error(f"Script not found: {script}")
        return 1

    engine = (args.engine or cfg.tts_engine).lower()
    best_of = cfg.best_of_n(draft=args.draft)
    out_path = Path(args.out) if args.out else (
        cfg.output_dir / f"{video.stem}_preview.mp4")

    started = time.perf_counter()
    log.user(f"Starting: {video.name} + {script.name}")
    log.user(f"Engine: {engine}, best-of-{best_of}"
             + (" (draft)" if args.draft else ""))

    # 1. scenes
    detection = detect_scenes(
        video, cfg, keyframe_dir=cfg.output_dir / "scenes" / video.stem, log=log
    )

    # 2. script
    split = parse_script_file(script, detection.scenes, cfg, log)
    for warning in split.warnings:
        log.user(f"NOTE: {warning}")

    # 3. voice
    audio = run_voice_stage(split.blocks, cfg, engine=engine,
                            best_of=best_of, log=log)
    save_report(audio, cfg.output_dir / "voice_stage" / f"{video.stem}.json")

    # 4. timeline
    timeline = plan_timeline(detection, audio, cfg,
                             work_dir=cfg.work_dir / f"align_{video.stem}",
                             log=log)
    timeline.save(cfg.output_dir / "timeline" / f"{video.stem}.json")

    problems = check_timeline(timeline, cfg)
    for problem in problems:
        log.warning(problem)

    # 5. preview
    render_preview(video, timeline, out_path, cfg,
                   work_dir=cfg.work_dir / f"preview_{video.stem}", log=log)

    print(timeline_table(timeline, split.method))

    print()
    print("  " + "-" * 60)
    spoken = [a for a in audio if not a.silent]
    weak = [a for a in spoken if a.similarity is not None
            and a.similarity_reliable and a.similarity < cfg.min_similarity]
    bad_words = [a for a in spoken if not a.words_ok]
    print(f"  blocks            : {len(timeline.blocks)} "
          f"({len(spoken)} spoken)")
    print(f"  words correct     : {len(spoken) - len(bad_words)}/{len(spoken)}")
    print(f"  below {cfg.min_similarity:.0f}% voice  : {len(weak)} "
          f"(only blocks >= {cfg.min_similarity_seconds:.1f}s are judged)")
    print(f"  timeline problems : {len(problems)}")
    print(f"  preview           : {out_path}")
    print(f"  took              : {time.perf_counter() - started:.1f}s")

    log.user("Done. Watch the preview to check the sync.")
    return 0 if not problems else 2


if __name__ == "__main__":
    sys.exit(main())
