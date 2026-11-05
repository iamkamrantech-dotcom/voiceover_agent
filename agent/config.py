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

    # ---- voice converter slot ----
    voice_converter: str = "none"

    # ---- similarity qc ----
    min_similarity: float = 70.0

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
    max_drift: float = 0.40

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

            voice_converter=_str("VOICE_CONVERTER", "none").lower(),
            min_similarity=_float("MIN_SIMILARITY", 70.0),

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
            max_drift=_float("MAX_DRIFT", 0.40),

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
