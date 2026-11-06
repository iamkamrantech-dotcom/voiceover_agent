"""Final narration audio: voice chain, optional ducked music, loudness.

The voice chain is deliberately light. The whole project exists to make the
narration sound like the CLIENT, and heavy processing undoes that - so this
is a high-pass to clear rumble, a small dip where boxiness lives, a small
presence lift, de-essing and a gentle compressor. Nothing that reshapes the
voice.

Music, when supplied, is looped to length, faded at both ends, set with
MUSIC_DB and ducked under the voice with a sidechain compressor keyed off
the voice itself.

Loudness is TWO-PASS loudnorm: pass 1 measures the real programme, pass 2
corrects it. One-pass loudnorm guesses from a running window and misses the
target; two-pass hits -14 LUFS / -1.5 dBTP properly. The measured numbers
are returned so QC reports what actually happened, not the target.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.config import Config  # noqa: E402
from agent import log as agent_log  # noqa: E402


@dataclass
class MasterResult:
    path: str
    target_i: float
    target_tp: float
    measured_i: float | None = None
    measured_tp: float | None = None
    measured_lra: float | None = None
    measured_thresh: float | None = None
    output_i: float | None = None
    output_tp: float | None = None
    output_lra: float | None = None
    music: str | None = None
    two_pass: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


def _voice_chain(cfg: Config) -> str:
    return (
        f"aresample={cfg.final_audio_rate}:resampler=soxr,"
        f"highpass=f={cfg.voice_hp_hz:.0f},"
        f"equalizer=f={cfg.voice_cut_hz:.0f}:t=q:w=1.2:g={cfg.voice_cut_gain:.2f},"
        f"equalizer=f={cfg.voice_presence_hz:.0f}:t=q:w=1.2:"
        f"g={cfg.voice_presence_gain:.2f},"
        f"deesser=i={cfg.deesser_intensity:.2f},"
        f"acompressor=threshold={cfg.comp_threshold_db:.1f}dB:"
        f"ratio={cfg.comp_ratio:.2f}:attack={cfg.comp_attack_ms:.0f}:"
        f"release={cfg.comp_release_ms:.0f}:makeup=1.5"
    )


def _music_chain(cfg: Config, duration: float) -> str:
    fade = max(0.1, min(cfg.music_fade, duration / 3))
    return (
        f"aresample={cfg.final_audio_rate}:resampler=soxr,"
        f"atrim=0:{duration:.6f},asetpts=PTS-STARTPTS,"
        f"volume={cfg.music_db:.2f}dB,"
        f"afade=t=in:st=0:d={fade:.3f},"
        f"afade=t=out:st={max(0.0, duration - fade):.3f}:d={fade:.3f}"
    )


def build_graph(cfg: Config, duration: float, with_music: bool,
                loudnorm: str) -> str:
    """Voice (+ ducked music) into the given loudnorm filter."""
    voice = f"[0:a]{_voice_chain(cfg)}[voice]"
    if not with_music:
        return f"{voice};[voice]{loudnorm}[aout]"

    return ";".join([
        voice,
        # One copy feeds the mix, the other keys the ducking.
        "[voice]asplit=2[voice_mix][voice_key]",
        f"[1:a]{_music_chain(cfg, duration)}[music]",
        (f"[music][voice_key]sidechaincompress="
         f"threshold={cfg.duck_threshold_db:.1f}dB:"
         f"ratio={cfg.duck_ratio:.2f}:attack={cfg.duck_attack_ms:.0f}:"
         f"release={cfg.duck_release_ms:.0f}[ducked]"),
        # normalize=0 or amix halves every level it touches.
        "[voice_mix][ducked]amix=inputs=2:duration=first:"
        "dropout_transition=0:normalize=0[mixed]",
        f"[mixed]{loudnorm}[aout]",
    ])


def _parse_loudnorm_json(stderr: str) -> dict | None:
    start = stderr.rfind("{")
    end = stderr.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(stderr[start:end + 1])
    except json.JSONDecodeError:
        return None


def _inputs(cfg: Config, voice: Path, music: Path | None) -> list[str]:
    args = ["-i", str(voice)]
    if music is not None:
        # Loop the music so a short bed still covers a long video.
        args = ["-i", str(voice), "-stream_loop", "-1", "-i", str(music)]
    return args


def has_audio_stream(cfg: Config, path: Path) -> bool:
    proc = subprocess.run(
        [cfg.ffprobe_bin, "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    return "audio" in proc.stdout


def master(voice_wav: Path, out_path: Path, duration: float,
           cfg: Config | None = None, music: Path | None = None,
           log=None) -> MasterResult:
    cfg = cfg or Config.load()
    log = log or agent_log.get()

    if music is not None and not has_audio_stream(cfg, music):
        log.warning(f"{music.name} has no audio stream, skipping music.")
        music = None

    with_music = music is not None
    result = MasterResult(path=str(out_path), target_i=cfg.target_lufs,
                          target_tp=cfg.target_true_peak,
                          music=str(music) if music else None)

    # ---- pass 1: measure -------------------------------------------------
    log.user("Measuring loudness (pass 1)...")
    measure = (f"loudnorm=I={cfg.target_lufs}:TP={cfg.target_true_peak}:"
               f"LRA=11:print_format=json")
    graph = build_graph(cfg, duration, with_music, measure)
    proc = subprocess.run(
        [cfg.ffmpeg_bin, "-y", "-hide_banner", *_inputs(cfg, voice_wav, music),
         "-filter_complex", graph, "-map", "[aout]",
         # Measure EXACTLY the programme that gets written. The voice track
         # carries a second of tail padding; measuring that but writing
         # without it means pass 2 corrects for the wrong programme.
         "-t", f"{duration:.6f}", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    measured = _parse_loudnorm_json(proc.stderr)

    if measured is None:
        log.warning("No loudnorm measurement, falling back to a single pass.")
        log.debug("ffmpeg stderr tail: %s", proc.stderr.strip()[-600:])
        result.two_pass = False
    else:
        result.measured_i = float(measured.get("input_i", "nan"))
        result.measured_tp = float(measured.get("input_tp", "nan"))
        result.measured_lra = float(measured.get("input_lra", "nan"))
        result.measured_thresh = float(measured.get("input_thresh", "nan"))
        log.user(f"Measured {result.measured_i:.1f} LUFS, "
                 f"true peak {result.measured_tp:.1f} dB.")

    # ---- pass 2: correct -------------------------------------------------
    log.user("Mastering final audio (pass 2)...")
    if measured is not None:
        apply_filter = (
            f"loudnorm=I={cfg.target_lufs}:TP={cfg.target_true_peak}:LRA=11:"
            f"measured_I={measured['input_i']}:measured_TP={measured['input_tp']}:"
            f"measured_LRA={measured['input_lra']}:"
            f"measured_thresh={measured['input_thresh']}:"
            f"offset={measured.get('target_offset', '0.0')}:"
            f"linear=true:print_format=json"
        )
    else:
        apply_filter = (f"loudnorm=I={cfg.target_lufs}:"
                        f"TP={cfg.target_true_peak}:LRA=11:print_format=json")

    graph = build_graph(cfg, duration, with_music, apply_filter)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [cfg.ffmpeg_bin, "-y", "-hide_banner", *_inputs(cfg, voice_wav, music),
         "-filter_complex", graph, "-map", "[aout]",
         "-ar", str(cfg.final_audio_rate), "-c:a", "pcm_s16le",
         "-t", f"{duration:.6f}", str(out_path)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        log.error(f"Audio master fail: {proc.stderr.strip()[-500:]}")
        raise RuntimeError("FFmpeg audio mastering failed")

    applied = _parse_loudnorm_json(proc.stderr)
    if applied:
        result.output_i = float(applied.get("output_i", "nan"))
        result.output_tp = float(applied.get("output_tp", "nan"))
        result.output_lra = float(applied.get("output_lra", "nan"))

    _verify_and_correct(out_path, duration, cfg, result, log)

    log.user(f"Audio ready: {out_path.name}")
    return result


def _verify_and_correct(path: Path, duration: float, cfg: Config,
                        result: MasterResult, log) -> None:
    """Measure what was actually written and fix any residual error.

    loudnorm's `linear=true` quietly falls back to dynamic mode when the
    static gain it wants would breach the true-peak ceiling - which happens
    once music is mixed in. The result lands near the target but not on it.
    Rather than trust the filter, measure the finished file and apply a
    single corrective gain, limited so the true peak stays legal.
    """
    final = measure_loudness(path, cfg, log)
    if not final:
        return
    try:
        measured_i = float(final.get("input_i", "nan"))
        measured_tp = float(final.get("input_tp", "nan"))
    except ValueError:
        return

    result.output_i = measured_i
    result.output_tp = measured_tp
    delta = cfg.target_lufs - measured_i

    if abs(delta) <= 0.2 or not (-12.0 < delta < 12.0):
        log.debug("loudness within tolerance (%.2f LU off)", delta)
        return

    log.debug("correcting loudness by %+.2f LU (was %.2f LUFS)",
              delta, measured_i)
    limit = 10 ** (cfg.target_true_peak / 20.0)
    fixed = path.with_name(path.stem + "_fixed.wav")
    proc = subprocess.run(
        [cfg.ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
         "-i", str(path), "-af",
         f"volume={delta:.3f}dB,alimiter=limit={limit:.4f}:level=disabled",
         "-ar", str(cfg.final_audio_rate), "-c:a", "pcm_s16le",
         "-t", f"{duration:.6f}", str(fixed)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        log.warning("Loudness correction failed, keeping the first render.")
        log.debug("ffmpeg: %s", proc.stderr.strip()[-400:])
        fixed.unlink(missing_ok=True)
        return

    fixed.replace(path)
    again = measure_loudness(path, cfg, log)
    if again:
        try:
            result.output_i = float(again.get("input_i", "nan"))
            result.output_tp = float(again.get("input_tp", "nan"))
        except ValueError:
            pass
    log.user(f"Loudness corrected to {result.output_i:.2f} LUFS.")


def measure_loudness(path: Path, cfg: Config, log=None) -> dict | None:
    """Read back the real loudness of a finished file (used by QC)."""
    proc = subprocess.run(
        [cfg.ffmpeg_bin, "-hide_banner", "-i", str(path),
         "-map", "a:0", "-af",
         f"loudnorm=I={cfg.target_lufs}:TP={cfg.target_true_peak}:"
         f"LRA=11:print_format=json", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    return _parse_loudnorm_json(proc.stderr)
