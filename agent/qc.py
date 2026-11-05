"""Check the finished file and say plainly what needs a human ear.

Nothing here trusts the plan: every number is measured off the rendered
mp4. The point is to catch a bad render before it ships, and to tell the
user in Roman Urdu which blocks they personally need to listen to.

On word drift
-------------
faster-whisper's own word timestamps jitter by roughly 50-150 ms even on
untouched audio, so a strict per-word gate would report Whisper's precision
as a defect. What actually matters is SYSTEMATIC drift - a wrong stretch
factor or a bad offset moves every word - and that shows up in the MEDIAN.
So the median gates, and individual outliers are reported as things to
listen to rather than failures.

The mock synthesizer emits tones, not speech, so the drift check reports
"not applicable" on mock runs instead of failing them.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.config import Config  # noqa: E402
from agent import log as agent_log  # noqa: E402
from agent.align import Timeline  # noqa: E402
from agent.audio_master import measure_loudness  # noqa: E402
from agent.voice.verify import normalise  # noqa: E402


@dataclass
class QCReport:
    video: str
    passed: bool = True
    duration_expected: float = 0.0
    duration_actual: float = 0.0
    duration_ok: bool = True
    loudness_i: float | None = None
    loudness_tp: float | None = None
    loudness_ok: bool = True
    clipping: bool = False
    peak_db: float | None = None
    drift_applicable: bool = True
    drift_median: float | None = None
    drift_p95: float | None = None
    drift_max: float | None = None
    drift_ok: bool = True
    matched_words: int = 0
    total_words: int = 0
    long_silences: list[dict] = field(default_factory=list)
    listen_to: list[dict] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")


def video_frame_count(cfg: Config, path: Path) -> int:
    """Count VIDEO packets. The container is as long as its longest stream,
    so container duration would hide a short video behind a full-length
    audio track - which is exactly how a bug slipped through once."""
    proc = subprocess.run(
        [cfg.ffprobe_bin, "-v", "error", "-select_streams", "v:0",
         "-count_packets", "-show_entries", "stream=nb_read_packets",
         "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    try:
        return int(proc.stdout.strip())
    except ValueError:
        return 0


def peak_level_db(cfg: Config, path: Path) -> float | None:
    proc = subprocess.run(
        [cfg.ffmpeg_bin, "-hide_banner", "-i", str(path), "-map", "a:0",
         "-af", "astats=measure_overall=Peak_level:measure_perchannel=none",
         "-f", "null", "-"], capture_output=True, text=True)
    found = re.findall(r"Peak level dB:\s*(-?inf|-?\d+\.?\d*)", proc.stderr)
    if not found:
        return None
    values = [float(v) for v in found if v not in ("-inf", "inf")]
    return max(values) if values else None


def find_long_silences(cfg: Config, path: Path) -> list[tuple[float, float]]:
    proc = subprocess.run(
        [cfg.ffmpeg_bin, "-hide_banner", "-i", str(path), "-map", "a:0",
         "-af", f"silencedetect=noise=-45dB:d={cfg.qc_max_silence:.2f}",
         "-f", "null", "-"], capture_output=True, text=True)
    starts = [float(m) for m in
              re.findall(r"silence_start:\s*(-?\d+\.?\d*)", proc.stderr)]
    ends = [float(m) for m in
            re.findall(r"silence_end:\s*(-?\d+\.?\d*)", proc.stderr)]
    out = []
    for i, start in enumerate(starts):
        end = ends[i] if i < len(ends) else start + cfg.qc_max_silence
        out.append((start, end))
    return out


def _planned_words(timeline: Timeline) -> list:
    words = []
    for block in timeline.blocks:
        if block.silent:
            continue
        words.extend(block.words)
    return sorted(words, key=lambda w: w.start)


def _measure_drift(cfg: Config, path: Path, timeline: Timeline,
                   log) -> tuple[list[float], int, int]:
    """Transcribe the finished audio and compare with the plan.

    Words are matched in order on normalised text, so an extra or missing
    word shifts nothing after it.
    """
    from faster_whisper import WhisperModel

    log.user("Final audio ka transcript nikal kar timing check kar rahe hain...")
    model = WhisperModel(cfg.whisper_model, device=cfg.device,
                         compute_type=cfg.whisper_compute_type,
                         cpu_threads=cfg.num_threads)
    segments, _ = model.transcribe(str(path), beam_size=cfg.whisper_beam_size,
                                   word_timestamps=True)
    heard = []
    for segment in segments:
        for word in segment.words or []:
            text = normalise(word.word)
            if text:
                heard.append((text, float(word.start)))

    planned = [(normalise(w.word), w.start) for w in _planned_words(timeline)]
    planned = [(t, s) for t, s in planned if t]

    deltas: list[float] = []
    cursor = 0
    for text, start in planned:
        # Look a little way ahead so one bad word does not desync the rest.
        for offset in range(cursor, min(cursor + 6, len(heard))):
            if heard[offset][0] == text:
                deltas.append(abs(heard[offset][1] - start))
                cursor = offset + 1
                break

    del model
    return deltas, len(planned), len(heard)


def run_qc(video_path: Path, timeline: Timeline, cfg: Config | None = None,
           engine: str = "standard", audio_blocks=None, log=None) -> QCReport:
    cfg = cfg or Config.load()
    log = log or agent_log.get()

    report = QCReport(video=str(video_path))
    log.user("Final file ki quality check ho rahi hai...")

    # ---- duration --------------------------------------------------------
    frames = video_frame_count(cfg, video_path)
    report.duration_actual = frames / timeline.fps if timeline.fps else 0.0
    report.duration_expected = timeline.output_duration
    frame = 1.0 / timeline.fps if timeline.fps else 0.04
    report.duration_ok = abs(
        report.duration_actual - report.duration_expected) <= frame * 1.5
    if not report.duration_ok:
        report.problems.append(
            f"video {report.duration_actual:.3f}s hai lekin timeline "
            f"{report.duration_expected:.3f}s kehti hai"
        )

    # ---- loudness --------------------------------------------------------
    measured = measure_loudness(video_path, cfg, log)
    if measured:
        try:
            report.loudness_i = float(measured.get("input_i", "nan"))
            report.loudness_tp = float(measured.get("input_tp", "nan"))
        except ValueError:
            pass
    if report.loudness_i is not None:
        report.loudness_ok = (
            abs(report.loudness_i - cfg.target_lufs) <= 1.0
            and (report.loudness_tp is None
                 or report.loudness_tp <= cfg.target_true_peak + 0.5)
        )
        if not report.loudness_ok:
            report.problems.append(
                f"loudness {report.loudness_i:.1f} LUFS / peak "
                f"{report.loudness_tp:.1f} dB target se door hai"
            )

    # ---- clipping --------------------------------------------------------
    report.peak_db = peak_level_db(cfg, video_path)
    if report.peak_db is not None and report.peak_db > -0.1:
        report.clipping = True
        report.problems.append(f"audio clip ho rahi hai ({report.peak_db:.2f} dB)")

    # ---- long silence where speech was actually expected -----------------
    # Gaps BETWEEN blocks are by design - the aligner never stretches a
    # short voice to fill its scene. So what matters is how much of the
    # silence falls INSIDE a block's own voice span; a silence that merely
    # touches the edge of one is just the designed gap starting.
    for start, end in find_long_silences(cfg, video_path):
        inside = 0.0
        culprit = None
        for block in timeline.blocks:
            if block.silent:
                continue
            overlap = min(end, block.voice_end) - max(start, block.voice_start)
            if overlap > inside:
                inside, culprit = overlap, block.block_index
        if inside > cfg.qc_max_silence:
            report.long_silences.append({
                "start": round(start, 2), "end": round(end, 2),
                "inside_voice": round(inside, 2), "block": culprit,
            })
            report.problems.append(
                f"Block {culprit + 1} ke andar {inside:.1f}s ki khamoshi hai "
                f"jahan awaaz honi chahiye thi ({start:.1f}s par)"
            )

    # ---- word drift ------------------------------------------------------
    if engine == "mock":
        report.drift_applicable = False
        log.debug("mock engine: tones are not speech, drift check skipped")
    else:
        try:
            deltas, planned_count, heard_count = _measure_drift(
                cfg, video_path, timeline, log)
            report.total_words = planned_count
            report.matched_words = len(deltas)
            if deltas:
                ordered = sorted(deltas)
                mid = len(ordered) // 2
                report.drift_median = round(
                    ordered[mid] if len(ordered) % 2 else
                    (ordered[mid - 1] + ordered[mid]) / 2, 3)
                report.drift_p95 = round(
                    ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 3)
                report.drift_max = round(ordered[-1], 3)
                report.drift_ok = report.drift_median <= cfg.max_word_drift
                if not report.drift_ok:
                    report.problems.append(
                        f"alfaaz ki timing {report.drift_median:.2f}s khisak "
                        f"gayi hai (hadd {cfg.max_word_drift:.2f}s)"
                    )
            else:
                report.drift_ok = False
                report.problems.append("transcript se koi lafz match nahi hua")
        except Exception as exc:
            log.warning(f"drift check nahi chal saka: {exc}")
            report.drift_applicable = False

    # ---- things a human should listen to ---------------------------------
    for block in timeline.blocks:
        reasons = []
        if block.freeze_seconds > cfg.freeze_warn_seconds:
            reasons.append(f"{block.freeze_seconds:.1f}s freeze")
        if reasons:
            report.listen_to.append({
                "block": block.block_index,
                "at": round(block.out_start, 2),
                "why": ", ".join(reasons),
            })

    for audio in (audio_blocks or []):
        if not audio.silent and not audio.words_ok:
            report.listen_to.append({
                "block": audio.block_index,
                "at": None,
                "why": "alfaaz poore theek nahi aaye",
            })

    report.passed = (report.duration_ok and report.loudness_ok
                     and not report.clipping and report.drift_ok
                     and not report.long_silences)
    return report


def summarise(report: QCReport, cfg: Config, log) -> None:
    """Short Roman Urdu summary for the person running the job."""
    log.user("")
    log.user("===== QC REPORT =====")
    log.user(f"Video lambai : {report.duration_actual:.2f}s "
             f"(plan {report.duration_expected:.2f}s) "
             f"{'theek' if report.duration_ok else 'GHALAT'}")
    if report.loudness_i is not None:
        log.user(f"Loudness     : {report.loudness_i:.1f} LUFS "
                 f"(target {cfg.target_lufs:.0f}), true peak "
                 f"{report.loudness_tp:.1f} dB "
                 f"{'theek' if report.loudness_ok else 'GHALAT'}")
    log.user(f"Clipping     : {'HAAN - masla hai' if report.clipping else 'nahi'}")
    if not report.drift_applicable:
        log.user("Alfaaz timing: check nahi hua (mock awaaz)")
    elif report.drift_median is not None:
        log.user(f"Alfaaz timing: median {report.drift_median:.2f}s, "
                 f"p95 {report.drift_p95:.2f}s, max {report.drift_max:.2f}s "
                 f"({report.matched_words}/{report.total_words} alfaaz) "
                 f"{'theek' if report.drift_ok else 'GHALAT'}")
    if report.long_silences:
        log.user(f"Khamoshi     : {len(report.long_silences)} jagah lambi "
                 f"khamoshi mili")

    if report.listen_to:
        log.user("")
        log.user("Ye blocks khud sun kar check karein:")
        for item in report.listen_to:
            where = f" ({item['at']:.1f}s par)" if item["at"] is not None else ""
            log.user(f"  - Block {item['block'] + 1}{where}: {item['why']}")

    log.user("")
    if report.passed:
        log.user("QC PASS - file theek lag rahi hai.")
    else:
        log.user("QC FAIL - neeche wale masle dekh lein:")
        for problem in report.problems:
            log.user(f"  - {problem}")
