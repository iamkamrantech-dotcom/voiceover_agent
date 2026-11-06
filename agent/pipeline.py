"""The whole job, start to finish, in one place.

    scenes -> script split -> voice -> timeline -> audio master
           -> subtitles -> final render -> QC

Every run gets its own job folder so nothing from one job overwrites
another. The script check is a separate, model-free entry point because
finding out a script is too long AFTER hours of voice generation is the
most expensive mistake this project can make.
"""

from __future__ import annotations

import math
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent import log as agent_log  # noqa: E402
from agent.align import check_timeline, plan_timeline  # noqa: E402
from agent.audio_master import master  # noqa: E402
from agent.config import Config  # noqa: E402
from agent.preview import build_voice_track, render_preview  # noqa: E402
from agent.qc import QCReport, run_qc, summarise  # noqa: E402
from agent.render import render_final  # noqa: E402
from agent.scenes import detect_scenes  # noqa: E402
from agent.script_parser import ScriptSplit, parse_script_file  # noqa: E402
from agent.subtitles import write_srt  # noqa: E402
from agent.voice_stage import (  # noqa: E402
    JobCancelled, _check, run_voice_stage, save_report,
)


@dataclass
class ScriptCheckRow:
    block: int
    scene_ids: list[int]
    start: float
    end: float
    words: int
    estimated: float
    available: float
    extension: float
    cut_words: int


@dataclass
class JobResult:
    job_dir: Path
    video: Path | None = None
    subtitles: Path | None = None
    timeline_json: Path | None = None
    report: QCReport | None = None
    split: ScriptSplit | None = None
    seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)


def make_job_dir(cfg: Config, stem: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = cfg.output_dir / "jobs" / f"{stem}_{stamp}"
    path.mkdir(parents=True, exist_ok=True)
    return path


# --------------------------------------------------------------------------
# script check - no models, runs in seconds
# --------------------------------------------------------------------------

def check_script(video: Path, script: Path, cfg: Config,
                 log) -> tuple[ScriptSplit, list[ScriptCheckRow]]:
    detection = detect_scenes(video, cfg, log=log)
    split = parse_script_file(script, detection.scenes, cfg, log)

    rows: list[ScriptCheckRow] = []
    for position, block in enumerate(split.blocks):
        is_final = position == len(split.blocks) - 1
        tail = cfg.tail_out_final if is_final else max(
            cfg.tail_out, cfg.block_gap - cfg.lead_in)
        available = (block.end - block.start) - cfg.lead_in - tail
        estimated = block.estimated_speech_seconds(cfg.narration_wps)

        # Pause tightening cannot be predicted without audio, so this
        # deliberately assumes only the speed-up ceiling can help.
        extension = max(0.0, estimated - available * cfg.max_speedup)
        cut_words = 0
        if extension > cfg.freeze_warn_seconds:
            cut_words = int(math.ceil(
                (extension - cfg.freeze_warn_seconds) * cfg.narration_wps))

        rows.append(ScriptCheckRow(
            block=block.index, scene_ids=list(block.scene_ids),
            start=block.start, end=block.end, words=block.word_count,
            estimated=estimated, available=available,
            extension=extension, cut_words=cut_words,
        ))
    return split, rows


def script_check_table(split: ScriptSplit, rows: list[ScriptCheckRow],
                       cfg: Config) -> str:
    lines = [
        "",
        "=" * 100,
        f"  SCRIPT CHECK   ({split.method} split, {len(rows)} block)",
        "=" * 100,
        f"  {'#':<4}{'scenes':<9}{'time':>14}{'words':>8}"
        f"{'speech':>12}{'available':>11}{'extend':>9}  action",
        "  " + "-" * 96,
    ]
    for row in rows:
        when = f"{row.start:.1f}-{row.end:.1f}s"
        scenes = ",".join(str(i) for i in row.scene_ids)
        if row.cut_words:
            advice = f"Cut about {row.cut_words} words"
        elif row.extension > 0.05:
            advice = "Fits, slight extension"
        else:
            advice = "Fits"
        lines.append(
            f"  {row.block:<4}{scenes:<9}{when:>14}{row.words:>8}"
            f"{row.estimated:>11.1f}s{row.available:>10.1f}s"
            f"{row.extension:>8.1f}s  {advice}"
        )

    total_cut = sum(r.cut_words for r in rows)
    lines.append("  " + "-" * 96)
    if total_cut:
        lines.append(f"  Cut about {total_cut} words in total so the video "
                     f"does not have to stretch.")
    else:
        lines.append("  The script fits the video.")
    for warning in split.warnings:
        lines.append(f"  NOTE: {warning}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# the full job
# --------------------------------------------------------------------------

def run_job(video: Path, script: Path, cfg: Config, log,
            music: Path | None = None, keep_original_audio: bool = False,
            burn_subs: bool = False, draft: bool = False,
            engine: str | None = None, make_preview: bool = False,
            out_path: Path | None = None, job_dir: Path | None = None,
            progress=None, cancel=None) -> JobResult:
    started = time.perf_counter()

    def step(stage: str, detail: str) -> None:
        if progress:
            progress(stage=stage, detail=detail)
        _check(cancel)

    engine = (engine or cfg.tts_engine).lower()
    best_of = cfg.best_of_n(draft=draft)
    job_dir = Path(job_dir) if job_dir else make_job_dir(cfg, video.stem)
    job_dir.mkdir(parents=True, exist_ok=True)
    work = job_dir / "_work"
    result = JobResult(job_dir=job_dir)

    log.user(f"Starting job: {video.name} + {script.name}")
    log.user(f"Engine: {engine}, best-of-{best_of}"
             + (" (draft)" if draft else ""))
    log.user(f"Job folder: {job_dir}")

    # 1. scenes
    step("scenes", "Detecting scenes...")
    detection = detect_scenes(video, cfg,
                              keyframe_dir=job_dir / "keyframes", log=log)

    # 2. script
    step("script", "Splitting the script across scenes...")
    split = parse_script_file(script, detection.scenes, cfg, log)
    result.split = split
    for warning in split.warnings:
        log.user(f"NOTE: {warning}")
        result.warnings.append(warning)

    # 3. voice
    step("voice", "Generating narration...")
    audio_blocks = run_voice_stage(split.blocks, cfg, engine=engine,
                                   best_of=best_of, log=log,
                                   progress=progress, cancel=cancel)
    save_report(audio_blocks, job_dir / "voice_stage.json")

    # 4. timeline
    step("align", "Planning timeline...")
    timeline = plan_timeline(detection, audio_blocks, cfg,
                             work_dir=work / "align", log=log)
    result.timeline_json = job_dir / "timeline.json"
    timeline.save(result.timeline_json)
    for problem in check_timeline(timeline, cfg):
        log.warning(problem)
        result.warnings.append(problem)

    # 5. subtitles
    step("subtitles", "Writing subtitles...")
    result.subtitles = write_srt(timeline, cfg,
                                 job_dir / f"{video.stem}.srt", log)

    # 6. audio master
    step("audio", "Mastering audio...")
    voice_track = build_voice_track(timeline, cfg, work / "voice_48k.wav",
                                    log, sample_rate=cfg.final_audio_rate)
    music_source = music
    if keep_original_audio:
        if music is not None:
            log.warning("Both --music and --keep-original-audio were given, "
                        "using the video's own audio.")
        music_source = video
    master_result = master(voice_track, work / "master.wav",
                           timeline.output_duration, cfg,
                           music=music_source, log=log)

    # 7. final render
    step("render", "Rendering final video...")
    final_path = out_path or (job_dir / f"{video.stem}_final.mp4")
    render_final(video, timeline, work / "master.wav", final_path, cfg,
                 subtitle_path=result.subtitles, burn_subs=burn_subs,
                 work_dir=work / "render", log=log)
    result.video = final_path

    if make_preview:
        render_preview(video, timeline, job_dir / f"{video.stem}_preview.mp4",
                       cfg, work_dir=work / "preview", log=log)

    # 8. QC
    step("qc", "Running final checks...")
    report = run_qc(final_path, timeline, cfg, engine=engine,
                    audio_blocks=audio_blocks, log=log)
    report.save(job_dir / f"{video.stem}_report.json")
    result.report = report
    summarise(report, cfg, log)

    # Keep the master beside the deliverables; drop scratch unless asked.
    shutil.copyfile(work / "master.wav", job_dir / "narration_master.wav")
    if not cfg.keep_temp:
        shutil.rmtree(work, ignore_errors=True)

    result.seconds = time.perf_counter() - started
    log.user(f"Job complete in {result.seconds:.1f}s.")
    return result
