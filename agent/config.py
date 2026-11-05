"""Every setting lives here, loaded from .env.

Nothing else in the project may read os.environ or hardcode a number: import
Config, call Config.load(), pass it down. Defaults below mirror .env.example,
so the agent runs even with no .env present.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field, fields
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Fallback locations for the winget FFmpeg build, used only when PATH misses it.
_WINGET_FFMPEG = (
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "Microsoft" / "WinGet" / "Packages"
    / "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
    / "ffmpeg-9.0.2-full_build" / "bin"
)


def _find_tool(name: str) -> str:
    """PATH first, then the known winget install, else the bare name."""
    found = shutil.which(name)
    if found:
        return found
    candidate = _WINGET_FFMPEG / f"{name}.exe"
    if candidate.is_file():
        return str(candidate)
    return name


def _str(key: str, default: str) -> str:
    value = os.environ.get(key)
    return default if value is None or value.strip() == "" else value.strip()


def _int(key: str, default: int) -> int:
    try:
        return int(float(_str(key, str(default))))
    except ValueError:
        return default


def _float(key: str, default: float) -> float:
    try:
        return float(_str(key, str(default)))
    except ValueError:
        return default


def _bool(key: str, default: bool) -> bool:
    return _str(key, "true" if default else "false").lower() in (
        "1", "true", "yes", "y", "on",
    )


def _opt_int(key: str, default: int | None) -> int | None:
    raw = os.environ.get(key, "")
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(float(raw))
    except ValueError:
        return default


def _path(key: str, default: str) -> Path:
    raw = _str(key, default)
    p = Path(raw)
    return p if p.is_absolute() else (PROJECT_ROOT / p)


@dataclass
class Config:
    """All runtime settings. Build it with Config.load()."""

    # ---- paths ----
    project_root: Path = PROJECT_ROOT
    input_video: Path = PROJECT_ROOT / "input/video.mp4"
    script_file: Path = PROJECT_ROOT / "input/script.txt"
    voice_sample: Path = PROJECT_ROOT / "input/ref6_3.wav"
    voice_target: Path = PROJECT_ROOT / "input/voice_sample.mp3"
    output_dir: Path = PROJECT_ROOT / "outputs"
    work_dir: Path = PROJECT_ROOT / "outputs/_work"
    keep_temp: bool = False

    # ---- hardware ----
    device: str = "cpu"
    num_threads: int = 8

    # ---- external tools ----
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"

    # ---- tts ----
    tts_engine: str = "standard"
    tts_exaggeration: float = 0.5
    tts_cfg_weight: float = 0.5
    tts_temperature: float = 0.8
    tts_seed: int | None = 1234
    tts_cache: bool = True
    tts_cache_dir: Path = PROJECT_ROOT / "outputs/_tts_cache"
    tts_best_of_n: int = 3
    tts_best_of_n_draft: int = 1
    tts_retry_rounds: int = 2
    # Chatterbox degrades on long text, so blocks are cut into chunks.
    tts_max_chunk_chars: int = 250
    tts_pause_sentence: float = 0.30
    tts_pause_comma: float = 0.15
    tts_trim_top_db: int = 40

    # ---- script splitting ----
    narration_wps: float = 2.5
    script_overflow_ratio: float = 1.10

    # ---- voice converter slot ----
    voice_converter: str = "none"

    # ---- similarity qc ----
    min_similarity: float = 70.0
    # ECAPA needs a few seconds of audio to be meaningful. Measured on one
    # fixed clip: 1.0s->50%, 1.8s->71%, 2.5s->69%, 3.5s->79%, 5.0s->85%.
    # Below this length the score is recorded but never used as a gate.
    min_similarity_seconds: float = 3.5

    # ---- faster-whisper ----
    whisper_model: str = "base"
    whisper_compute_type: str = "int8"
    whisper_language: str = "en"
    whisper_beam_size: int = 5

    # ---- scene detection ----
    scene_threshold: float = 0.35
    min_scene_seconds: float = 1.0
    scene_sample_fps: float = 10.0
    scene_lag_seconds: float = 0.5
    scene_hist_weight: float = 0.6
    scene_small_width: int = 32
    scene_small_height: int = 18
    fade_min_frames: int = 2

    # ---- script to image matching ----
    match_mode: str = "auto"
    scene_marker_pattern: str = r"^\s*Scene\s*(\d+)\s*[:\-]"

    # ---- timing / fit ----
    min_speed: float = 0.92
    max_speed: float = 1.12
    lead_in: float = 0.25
    tail_out: float = 0.35
    tail_out_final: float = 0.60
    block_gap: float = 0.35
    max_drift: float = 0.40
    # Fixing an overlong block, cheapest first:
    max_speedup: float = 1.08          # (b) voice speed-up ceiling
    pause_tighten_above: float = 0.25  # (a) only pauses longer than this
    pause_floor_sentence: float = 0.18
    pause_floor_comma: float = 0.10
    max_video_slowdown: float = 1.15   # (c) stretch video up to hold_end
    audio_stretch_filter: str = "auto"  # auto | rubberband | atempo

    # ---- living hold ----
    # A long freeze held perfectly still reads as a stall, so the frozen
    # frame gets a gentle push-in. zoom | none
    hold_motion: str = "zoom"
    hold_motion_min_seconds: float = 0.5
    hold_zoom_max: float = 0.04

    # ---- preview render ----
    preview_height: int = 480
    preview_crf: int = 28
    preview_preset: str = "veryfast"
    preview_subtitles: bool = True

    # ---- final render ----
    final_profile: str = "high"
    final_gop_divisor: int = 2
    final_audio_bitrate: str = "320k"
    final_audio_rate: int = 48000

    # ---- audio mastering ----
    voice_hp_hz: float = 75.0
    voice_cut_hz: float = 250.0
    voice_cut_gain: float = -2.5
    voice_presence_hz: float = 3000.0
    voice_presence_gain: float = 2.0
    deesser_intensity: float = 0.25
    comp_threshold_db: float = -18.0
    comp_ratio: float = 2.5
    comp_attack_ms: float = 10.0
    comp_release_ms: float = 180.0
    music_db: float = -22.0
    music_fade: float = 1.5
    duck_threshold_db: float = -30.0
    duck_ratio: float = 8.0
    duck_attack_ms: float = 20.0
    duck_release_ms: float = 350.0

    # ---- subtitles ----
    subtitle_min_seconds: float = 0.8

    # ---- qc ----
    max_word_drift: float = 0.10
    qc_max_silence: float = 3.0
    freeze_warn_seconds: float = 1.5

    # ---- audio ----
    target_lufs: float = -14.0
    target_true_peak: float = -1.5
    audio_sample_rate: int = 44100

    # ---- subtitles ----
    make_subtitles: bool = True
    subtitle_format: str = "srt"
    subtitle_max_chars: int = 42
    subtitle_max_lines: int = 2
    burn_in_subtitles: bool = False

    # ---- render ----
    video_codec: str = "libx264"
    video_crf: int = 18
    video_preset: str = "medium"
    audio_codec: str = "aac"
    audio_bitrate: str = "192k"

    # ---- logging ----
    log_level: str = "INFO"
    log_dir: Path = PROJECT_ROOT / "outputs/logs"

    # ---- testing ----
    fast_test: bool = True

    # ---- web app (later) ----
    api_host: str = "127.0.0.1"
    api_port: int = 8000

    @classmethod
    def load(cls, env_file: Path | str | None = None) -> "Config":
        """Read .env (if present) and build the config."""
        env_path = Path(env_file) if env_file else (PROJECT_ROOT / ".env")
        if env_path.is_file():
            load_dotenv(env_path, override=False)

        return cls(
            input_video=_path("INPUT_VIDEO", "input/video.mp4"),
            script_file=_path("SCRIPT_FILE", "input/script.txt"),
            voice_sample=_path("VOICE_SAMPLE", "input/ref6_3.wav"),
            voice_target=_path("VOICE_TARGET", "input/voice_sample.mp3"),
            output_dir=_path("OUTPUT_DIR", "outputs"),
            work_dir=_path("WORK_DIR", "outputs/_work"),
            keep_temp=_bool("KEEP_TEMP", False),

            device=_str("DEVICE", "cpu"),
            num_threads=_int("NUM_THREADS", os.cpu_count() or 4),

            ffmpeg_bin=_str("FFMPEG_BIN", _find_tool("ffmpeg")),
            ffprobe_bin=_str("FFPROBE_BIN", _find_tool("ffprobe")),

            tts_engine=_str("TTS_ENGINE", "standard").lower(),
            tts_exaggeration=_float("TTS_EXAGGERATION", 0.5),
            tts_cfg_weight=_float("TTS_CFG_WEIGHT", 0.5),
            tts_temperature=_float("TTS_TEMPERATURE", 0.8),
            tts_seed=_opt_int("TTS_SEED", 1234),
            tts_cache=_bool("TTS_CACHE", True),
            tts_cache_dir=_path("TTS_CACHE_DIR", "outputs/_tts_cache"),
            tts_best_of_n=max(1, _int("TTS_BEST_OF_N", 3)),
            tts_best_of_n_draft=max(1, _int("TTS_BEST_OF_N_DRAFT", 1)),
            tts_retry_rounds=max(0, _int("TTS_RETRY_ROUNDS", 2)),
            tts_max_chunk_chars=_int("TTS_MAX_CHUNK_CHARS", 250),
            tts_pause_sentence=_float("TTS_PAUSE_SENTENCE", 0.30),
            tts_pause_comma=_float("TTS_PAUSE_COMMA", 0.15),
            tts_trim_top_db=_int("TTS_TRIM_TOP_DB", 40),

            narration_wps=_float("NARRATION_WPS", 2.5),
            script_overflow_ratio=_float("SCRIPT_OVERFLOW_RATIO", 1.10),

            voice_converter=_str("VOICE_CONVERTER", "none").lower(),
            min_similarity=_float("MIN_SIMILARITY", 70.0),
            min_similarity_seconds=_float("MIN_SIMILARITY_SECONDS", 3.5),

            whisper_model=_str("WHISPER_MODEL", "base"),
            whisper_compute_type=_str("WHISPER_COMPUTE_TYPE", "int8"),
            whisper_language=_str("WHISPER_LANGUAGE", "en"),
            whisper_beam_size=_int("WHISPER_BEAM_SIZE", 5),

            scene_threshold=_float("SCENE_THRESHOLD", 0.35),
            min_scene_seconds=_float("MIN_SCENE_SECONDS", 1.0),
            scene_sample_fps=_float("SCENE_SAMPLE_FPS", 10.0),
            scene_lag_seconds=_float("SCENE_LAG_SECONDS", 0.5),
            scene_hist_weight=_float("SCENE_HIST_WEIGHT", 0.6),
            scene_small_width=_int("SCENE_SMALL_WIDTH", 32),
            scene_small_height=_int("SCENE_SMALL_HEIGHT", 18),
            fade_min_frames=_int("FADE_MIN_FRAMES", 2),

            match_mode=_str("MATCH_MODE", "auto").lower(),
            scene_marker_pattern=_str(
                "SCENE_MARKER_PATTERN", r"^\s*Scene\s*(\d+)\s*[:\-]"
            ),

            min_speed=_float("MIN_SPEED", 0.92),
            max_speed=_float("MAX_SPEED", 1.12),
            lead_in=_float("LEAD_IN", 0.25),
            tail_out=_float("TAIL_OUT", 0.35),
            tail_out_final=_float("TAIL_OUT_FINAL", 0.60),
            block_gap=_float("BLOCK_GAP", 0.35),
            max_drift=_float("MAX_DRIFT", 0.40),
            max_speedup=_float("MAX_SPEEDUP", 1.08),
            pause_tighten_above=_float("PAUSE_TIGHTEN_ABOVE", 0.25),
            pause_floor_sentence=_float("PAUSE_FLOOR_SENTENCE", 0.18),
            pause_floor_comma=_float("PAUSE_FLOOR_COMMA", 0.10),
            max_video_slowdown=_float("MAX_VIDEO_SLOWDOWN", 1.15),
            audio_stretch_filter=_str("AUDIO_STRETCH_FILTER", "auto").lower(),

            hold_motion=_str("HOLD_MOTION", "zoom").lower(),
            hold_motion_min_seconds=_float("HOLD_MOTION_MIN_SECONDS", 0.5),
            hold_zoom_max=_float("HOLD_ZOOM_MAX", 0.04),

            preview_height=_int("PREVIEW_HEIGHT", 480),
            preview_crf=_int("PREVIEW_CRF", 28),
            preview_preset=_str("PREVIEW_PRESET", "veryfast"),
            preview_subtitles=_bool("PREVIEW_SUBTITLES", True),

            final_profile=_str("FINAL_PROFILE", "high"),
            final_gop_divisor=max(1, _int("FINAL_GOP_DIVISOR", 2)),
            final_audio_bitrate=_str("FINAL_AUDIO_BITRATE", "320k"),
            final_audio_rate=_int("FINAL_AUDIO_RATE", 48000),

            voice_hp_hz=_float("VOICE_HP_HZ", 75.0),
            voice_cut_hz=_float("VOICE_CUT_HZ", 250.0),
            voice_cut_gain=_float("VOICE_CUT_GAIN", -2.5),
            voice_presence_hz=_float("VOICE_PRESENCE_HZ", 3000.0),
            voice_presence_gain=_float("VOICE_PRESENCE_GAIN", 2.0),
            deesser_intensity=_float("DEESSER_INTENSITY", 0.25),
            comp_threshold_db=_float("COMP_THRESHOLD_DB", -18.0),
            comp_ratio=_float("COMP_RATIO", 2.5),
            comp_attack_ms=_float("COMP_ATTACK_MS", 10.0),
            comp_release_ms=_float("COMP_RELEASE_MS", 180.0),
            music_db=_float("MUSIC_DB", -22.0),
            music_fade=_float("MUSIC_FADE", 1.5),
            duck_threshold_db=_float("DUCK_THRESHOLD_DB", -30.0),
            duck_ratio=_float("DUCK_RATIO", 8.0),
            duck_attack_ms=_float("DUCK_ATTACK_MS", 20.0),
            duck_release_ms=_float("DUCK_RELEASE_MS", 350.0),

            subtitle_min_seconds=_float("SUBTITLE_MIN_SECONDS", 0.8),

            max_word_drift=_float("MAX_WORD_DRIFT", 0.10),
            qc_max_silence=_float("QC_MAX_SILENCE", 3.0),
            freeze_warn_seconds=_float("FREEZE_WARN_SECONDS", 1.5),

            target_lufs=_float("TARGET_LUFS", -14.0),
            target_true_peak=_float("TARGET_TRUE_PEAK", -1.5),
            audio_sample_rate=_int("AUDIO_SAMPLE_RATE", 44100),

            make_subtitles=_bool("MAKE_SUBTITLES", True),
            subtitle_format=_str("SUBTITLE_FORMAT", "srt").lower(),
            subtitle_max_chars=_int("SUBTITLE_MAX_CHARS", 42),
            subtitle_max_lines=_int("SUBTITLE_MAX_LINES", 2),
            burn_in_subtitles=_bool("BURN_IN_SUBTITLES", False),

            video_codec=_str("VIDEO_CODEC", "libx264"),
            video_crf=_int("VIDEO_CRF", 18),
            video_preset=_str("VIDEO_PRESET", "medium"),
            audio_codec=_str("AUDIO_CODEC", "aac"),
            audio_bitrate=_str("AUDIO_BITRATE", "192k"),

            log_level=_str("LOG_LEVEL", "INFO").upper(),
            log_dir=_path("LOG_DIR", "outputs/logs"),

            fast_test=_bool("FAST_TEST", True),

            api_host=_str("API_HOST", "127.0.0.1"),
            api_port=_int("API_PORT", 8000),
        )

    def best_of_n(self, draft: bool = False) -> int:
        return self.tts_best_of_n_draft if draft else self.tts_best_of_n

    def ensure_dirs(self) -> None:
        for d in (self.output_dir, self.work_dir, self.tts_cache_dir, self.log_dir):
            d.mkdir(parents=True, exist_ok=True)

    def describe(self) -> str:
        lines = []
        for f in fields(self):
            lines.append(f"  {f.name:<24} = {getattr(self, f.name)}")
        return "\n".join(lines)


if __name__ == "__main__":
    print(Config.load().describe())
