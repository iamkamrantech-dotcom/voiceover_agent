"""Voiceover Agent - one command for the whole job.

    # Check the script FIRST. Seconds, no models, no wasted hours.
    venv\\Scripts\\python.exe run.py --video X.mp4 --script Y.txt --check-script

    # Full job
    venv\\Scripts\\python.exe run.py --video X.mp4 --script Y.txt
    venv\\Scripts\\python.exe run.py --video X.mp4 --script Y.txt --draft --music bed.mp3
    venv\\Scripts\\python.exe run.py --video X.mp4 --script Y.txt --keep-original-audio --burn-subs

Everything else comes from .env.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from agent import log as agent_log  # noqa: E402
from agent.config import Config  # noqa: E402
from agent.pipeline import (  # noqa: E402
    check_script, run_job, script_check_table,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--video", required=True, help="slideshow video")
    parser.add_argument("--script", required=True, help="English script (.txt)")
    parser.add_argument("--music", default=None,
                        help="background music file, ducked under the voice")
    parser.add_argument("--keep-original-audio", action="store_true",
                        help="use the video's own audio as the bed instead")
    parser.add_argument("--burn-subs", action="store_true",
                        help="burn subtitles into the picture")
    parser.add_argument("--draft", action="store_true",
                        help="best-of-1 instead of TTS_BEST_OF_N (much faster)")
    parser.add_argument("--check-script", action="store_true",
                        help="script fit check only - no voice generation")
    parser.add_argument("--preview", action="store_true",
                        help="also write the fast 480p preview")
    parser.add_argument("--engine", default=None,
                        help="standard | turbo | mock (default: .env)")
    parser.add_argument("--out", default=None, help="final mp4 path")
    return parser


def main() -> int:
    args = build_parser().parse_args()

    cfg = Config.load()
    cfg.ensure_dirs()
    log = agent_log.setup(cfg.log_dir, cfg.log_level, run_name="run")

    video = Path(args.video)
    script = Path(args.script)
    if not video.is_file():
        log.error(f"Video nahi mila: {video}")
        return 1
    if not script.is_file():
        log.error(f"Script nahi mili: {script}")
        return 1

    # ---- script check only ----------------------------------------------
    if args.check_script:
        split, rows = check_script(video, script, cfg, log)
        print(script_check_table(split, rows, cfg))
        print()
        needs_cut = sum(1 for r in rows if r.cut_words)
        log.user("Script check mukammal - koi awaaz nahi banayi gayi."
                 if not needs_cut else
                 f"{needs_cut} block lambe hain - script theek kar ke "
                 f"dobara check karein, phir voice banayein.")
        return 0 if not needs_cut else 3

    music = Path(args.music) if args.music else None
    if music is not None and not music.is_file():
        log.error(f"Music file nahi mili: {music}")
        return 1

    result = run_job(
        video, script, cfg, log,
        music=music,
        keep_original_audio=args.keep_original_audio,
        burn_subs=args.burn_subs,
        draft=args.draft,
        engine=args.engine,
        make_preview=args.preview,
        out_path=Path(args.out) if args.out else None,
    )

    print()
    print("  " + "-" * 62)
    print(f"  final video : {result.video}")
    print(f"  subtitles   : {result.subtitles}")
    print(f"  timeline    : {result.timeline_json}")
    print(f"  job folder  : {result.job_dir}")
    print(f"  took        : {result.seconds:.1f}s")
    if result.report:
        print(f"  QC          : {'PASS' if result.report.passed else 'FAIL'}")

    if result.report and not result.report.passed:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
