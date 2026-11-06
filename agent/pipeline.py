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
from agent import versions  # noqa: E402
from agent.align import check_timeline, plan_timeline  # noqa: E402
from agent.audio_master import master  # noqa: E402
from agent.config import Config  # noqa: E402
from agent.preview import build_voice_track, render_preview  # noqa: E402
from agent.qc import QCReport, run_qc, summarise  # noqa: E402
from agent.render import render_final  # noqa: E402
from agent.scenes import Detection, detect_scenes  # noqa: E402
from agent.waveform import build_peaks  # noqa: E402
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

def fit_row(index: int, scene_ids: list[int], start: float, end: float,
            words: int, is_final: bool, cfg: Config) -> ScriptCheckRow:
    """How well `words` of narration fits the window `start`-`end`.

    One implementation, used by both the CLI check and the dashboard
    storyboard, so the two can never disagree about whether a block fits.
    """
    tail = cfg.tail_out_final if is_final else max(
        cfg.tail_out, cfg.block_gap - cfg.lead_in)
    available = (end - start) - cfg.lead_in - tail
    estimated = words / cfg.narration_wps if cfg.narration_wps > 0 else 0.0

    # Pause tightening cannot be predicted without audio, so this
    # deliberately assumes only the speed-up ceiling can help.
    extension = max(0.0, estimated - available * cfg.max_speedup)
    cut_words = 0
    if extension > cfg.freeze_warn_seconds:
        cut_words = int(math.ceil(
            (extension - cfg.freeze_warn_seconds) * cfg.narration_wps))

    return ScriptCheckRow(
        block=index, scene_ids=list(scene_ids), start=start, end=end,
        words=words, estimated=estimated, available=available,
        extension=extension, cut_words=cut_words,
    )


def check_script(video: Path, script: Path, cfg: Config,
                 log) -> tuple[ScriptSplit, list[ScriptCheckRow]]:
    detection = detect_scenes(video, cfg, log=log)
    split = parse_script_file(script, detection.scenes, cfg, log)

    rows = [
        fit_row(block.index, block.scene_ids, block.start, block.end,
                block.word_count, position == len(split.blocks) - 1, cfg)
        for position, block in enumerate(split.blocks)
    ]
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
    result = JobResult(job_dir=job_dir)

    log.user(f"Starting job: {video.name} + {script.name}")
    log.user(f"Engine: {engine}, best-of-{best_of}"
             + (" (draft)" if draft else ""))
    log.user(f"Job folder: {job_dir}")

    # 1. scenes
    step("scenes", "Detecting scenes...")
    detection = detect_scenes(video, cfg,
                              keyframe_dir=job_dir / "keyframes", log=log)
    # Saved so a later revision re-uses this answer instead of reading every
    # frame of the video again for a result that cannot have changed.
    detection.save(job_dir / "scenes.json")

    # 2. script
    step("script", "Splitting the script across scenes...")
    split = parse_script_file(script, detection.scenes, cfg, log)
    split.save(job_dir / "split.json")
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

    finish_job(video, detection, audio_blocks, cfg, log, job_dir=job_dir,
               result=result, music=music,
               keep_original_audio=keep_original_audio, burn_subs=burn_subs,
               engine=engine, make_preview=make_preview, out_path=out_path,
               progress=progress, cancel=cancel)

    result.seconds = time.perf_counter() - started
    log.user(f"Job complete in {result.seconds:.1f}s.")
    return result


def finish_job(video: Path, detection: Detection,
               audio_blocks: list, cfg: Config, log, job_dir: Path,
               result: JobResult | None = None, music: Path | None = None,
               keep_original_audio: bool = False, burn_subs: bool = False,
               engine: str = "standard", make_preview: bool = False,
               out_path: Path | None = None, version: int = 1,
               note: str = "First render", changes: list[dict] | None = None,
               progress=None, cancel=None) -> JobResult:
    """Everything after the narration exists: timeline, subtitles, audio
    master, render, QC - then record the result as a version.

    A first run and a revision both come through here. The first run has
    just made every block; a revision has changed exactly one of them. From
    this point on there is no difference, which is deliberate: a revised
    video must be built by the same code that built the original, or the
    two would drift apart.
    """
    job_dir = Path(job_dir)
    result = result or JobResult(job_dir=job_dir)
    work = versions.work_dir(job_dir, version)
    out_dir = versions.version_dir(job_dir, version)
    out_dir.mkdir(parents=True, exist_ok=True)

    def step(stage: str, detail: str) -> None:
        if progress:
            progress(stage=stage, detail=detail)
        _check(cancel)

    # 4. timeline
    step("align", "Planning timeline...")
    timeline = plan_timeline(detection, audio_blocks, cfg,
                             work_dir=work / "align", log=log)
    # The aligned wavs live in the work directory, which is deleted below -
    # so the timeline would point at files that no longer exist. Copy them
    # somewhere durable and point it there instead.
    keep_block_audio(timeline, out_dir / "blocks")
    result.timeline_json = out_dir / "timeline.json"
    timeline.save(result.timeline_json)
    for problem in check_timeline(timeline, cfg):
        log.warning(problem)
        result.warnings.append(problem)

    # 5. subtitles
    step("subtitles", "Writing subtitles...")
    srt_name = f"{video.stem}.srt" if version <= 1 else "subtitles.srt"
    result.subtitles = write_srt(timeline, cfg, out_dir / srt_name, log)

    # 6. audio master
    step("audio", "Mastering audio...")
    voice_track = build_voice_track(timeline, cfg, work / "voice_48k.wav",
                                    log, sample_rate=cfg.final_audio_rate)
    # Measure the waveform now, while the voice bus still exists. Keeping
    # the wav itself would cost ~55 MB a job; the peaks cost ~30 KB.
    wave_path = out_dir / "waveform.json"
    try:
        build_peaks(voice_track, wave_path)
    except Exception as exc:                      # never fail a job over this
        log.debug("waveform could not be measured: %s", exc)
        wave_path = None

    music_source = music
    if keep_original_audio:
        if music is not None:
            log.warning("Both --music and --keep-original-audio were given, "
                        "using the video's own audio.")
        music_source = video
    master(voice_track, work / "master.wav", timeline.output_duration, cfg,
           music=music_source, log=log)

    # 7. final render
    step("render", "Rendering final video...")
    final_path = out_path or (out_dir / ("final.mp4" if version > 1
                                         else f"{video.stem}_final.mp4"))
    render_final(video, timeline, work / "master.wav", final_path, cfg,
                 subtitle_path=result.subtitles, burn_subs=burn_subs,
                 work_dir=work / "render", log=log)
    result.video = final_path

    if make_preview:
        render_preview(video, timeline, out_dir / f"{video.stem}_preview.mp4",
                       cfg, work_dir=work / "preview", log=log)

    # 8. QC
    step("qc", "Running final checks...")
    report = run_qc(final_path, timeline, cfg, engine=engine,
                    audio_blocks=audio_blocks, log=log)
    report_name = f"{video.stem}_report.json" if version <= 1 else "report.json"
    report.save(out_dir / report_name)
    result.report = report
    summarise(report, cfg, log)

    # Keep the master beside the deliverables; drop scratch unless asked.
    shutil.copyfile(work / "master.wav", out_dir / "narration_master.wav")
    if not cfg.keep_temp:
        shutil.rmtree(work, ignore_errors=True)

    versions.record(
        job_dir, version, note, changes or [], video=final_path,
        subtitles=result.subtitles, timeline=result.timeline_json,
        report=out_dir / report_name, waveform=wave_path,
        qc_passed=report.passed, duration=timeline.output_duration,
    )
    return result


def keep_block_audio(timeline, out_dir: Path) -> None:
    """Copy each block's aligned wav out of the scratch directory.

    plan_timeline writes tightened and sped-up audio into its work folder,
    which the job deletes on the way out - and then timeline.json refers to
    files that are gone. Anything that wants to play a block back (the
    review panel) needs them to survive.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for block in timeline.blocks:
        if not block.wav:
            continue
        source = Path(block.wav)
        if not source.is_file():
            continue
        target = out_dir / f"block{block.block_index:03d}{source.suffix}"
        if source.resolve() != target.resolve():
            shutil.copyfile(source, target)
        block.wav = str(target)
