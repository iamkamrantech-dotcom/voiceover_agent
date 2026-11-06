"""What every setting is, in one place.

Config knows a setting's type and default; .env.example explains it in a
sentence. Neither is reachable from the dashboard, so the Settings page used
to show eight bare keys and a list of values nobody could interpret.

This is the missing third thing: the name a person would use, the one line
that says what the setting does, and the default - which is what makes
"reset this one setting" possible at all. Reset removes the line from .env
rather than writing the default in as a literal, so a later change to a
default is actually picked up.

Rules that apply to the whole table:

  * Everything here is editable while nothing is running. A job reads its
    settings as it goes, so changing them underneath it would produce a
    video made half one way and half another.
  * Paths, the device and the password's value are never editable: the
    first two would point a running install at somewhere that may not
    exist, and a password should not be readable just because it is
    settable.
  * A setting that no code reads does not appear. A control that does
    nothing is worse than no control.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.config import Config  # noqa: E402

VOICE = "Voice"
TIMING = "Timing and fit"
AUDIO = "Audio"
OUTPUT = "Output"
SECURITY = "Security"

SECTIONS = (VOICE, TIMING, AUDIO, OUTPUT, SECURITY)

SECTION_HELP = {
    VOICE: "How the narration is generated and how closely it has to match "
           "the client.",
    TIMING: "How narration is fitted to the pictures when it does not land "
            "exactly.",
    AUDIO: "Levels and processing applied to the finished soundtrack.",
    OUTPUT: "What the rendered file and its subtitles look like.",
    SECURITY: "Who can reach this dashboard, and from where.",
}


@dataclass
class SettingDef:
    key: str
    section: str
    label: str
    help: str
    type: str                       # str | int | float | bool | password
    default: object
    attr: str = ""                  # the Config field, when the name differs
    choices: list[str] = field(default_factory=list)
    minimum: float | None = None
    maximum: float | None = None
    writable: bool = True
    restart_required: bool = False
    unit: str = ""

    def config_attr(self) -> str:
        return self.attr or self.key.lower()


def _d(key, section, label, help, type, default, **kw) -> SettingDef:
    return SettingDef(key=key, section=section, label=label, help=help,
                      type=type, default=default, **kw)


SETTINGS: list[SettingDef] = [
    # ---- Voice -----------------------------------------------------------
    _d("TTS_ENGINE", VOICE, "Voice model",
       "Standard sounds closest to the client. Turbo is about 3.5x faster "
       "and measurably less like them. Mock makes tones, for testing only.",
       "str", "standard", choices=["standard", "turbo", "mock"]),
    _d("TTS_BEST_OF_N", VOICE, "Takes per block",
       "How many times each block is generated. The best-sounding take that "
       "says the right words is kept. This is the biggest proven quality "
       "lever, and it costs its number in time.",
       "int", 3, minimum=1, maximum=6),
    _d("TTS_BEST_OF_N_DRAFT", VOICE, "Takes per block in a draft",
       "The same, for draft runs, so checking the fit stays quick.",
       "int", 1, minimum=1, maximum=4),
    _d("TTS_RETRY_ROUNDS", VOICE, "Retry rounds",
       "If no take of a block says the right words, how many more rounds of "
       "fresh attempts to make before keeping the closest one.",
       "int", 2, minimum=0, maximum=5),
    _d("TTS_EXAGGERATION", VOICE, "Expressiveness",
       "How much emotion the delivery carries. 0.5 is neutral and scored "
       "best in testing. The turbo model ignores this.",
       "float", 0.5, minimum=0.0, maximum=1.0),
    _d("TTS_CFG_WEIGHT", VOICE, "Delivery guidance",
       "Lower gives a slower, calmer read. 0.5 scored best. The turbo model "
       "ignores this.",
       "float", 0.5, minimum=0.0, maximum=1.0),
    _d("TTS_TEMPERATURE", VOICE, "Variation between takes",
       "How different one take is from the next. Some variation is what "
       "makes generating several takes worth doing.",
       "float", 0.8, minimum=0.1, maximum=1.5),
    _d("TTS_SEED", VOICE, "Starting seed",
       "Take 1 uses this number, take 2 the next, and so on, so the same "
       "settings always produce the same audio.",
       "int", 1234, minimum=0),
    _d("MIN_SIMILARITY", VOICE, "Similarity to flag below",
       "Blocks scoring under this against the client's recording are listed "
       "for you to listen to. Two clips of the same person score about 90.",
       "float", 70.0, minimum=0.0, maximum=100.0, unit="%"),
    _d("MIN_SIMILARITY_SECONDS", VOICE, "Shortest block worth scoring",
       "Similarity scoring needs a few seconds of audio to mean anything. "
       "Shorter blocks are still scored, but never judged on it.",
       "float", 3.5, minimum=0.0, maximum=10.0, unit="s"),
    _d("TTS_MAX_CHUNK_CHARS", VOICE, "Longest piece to generate at once",
       "Quality drops on long input, so a block is split into pieces no "
       "longer than this and joined with a natural pause.",
       "int", 250, minimum=80, maximum=600),
    _d("TTS_PAUSE_SENTENCE", VOICE, "Pause after a full stop",
       "Silence inserted where two generated pieces meet at a sentence end.",
       "float", 0.30, minimum=0.0, maximum=1.5, unit="s"),
    _d("TTS_PAUSE_COMMA", VOICE, "Pause after a comma",
       "The same, where a piece ends mid-sentence.",
       "float", 0.15, minimum=0.0, maximum=1.0, unit="s"),
    _d("TTS_TRIM_TOP_DB", VOICE, "Silence trimming",
       "How aggressively silence at the very start and end of a block is "
       "removed. Pauses inside a block are deliberate and never trimmed.",
       "int", 40, minimum=10, maximum=80, unit="dB"),
    _d("VOICE_SAMPLE", VOICE, "Reference clip",
       "The clip the voice is cloned from. Chosen on the Voice page, not "
       "here.",
       "str", "input/ref6_3.wav", writable=False),
    _d("VOICE_TARGET", VOICE, "Client recording",
       "The full recording every take is scored against. Set with the voice "
       "profile.",
       "str", "input/voice_sample.mp3", writable=False),
    _d("WHISPER_MODEL", VOICE, "Transcription model",
       "Used for word timings and the final sync check. Larger is more "
       "accurate and much slower on this machine.",
       "str", "base", choices=["tiny", "base", "small", "medium", "large-v3"]),

    # ---- Timing and fit --------------------------------------------------
    _d("NARRATION_WPS", TIMING, "Words per second",
       "The reading pace used to predict how long a script will take before "
       "any audio exists. It drives the whole fit check.",
       "float", 2.5, minimum=1.0, maximum=5.0),
    _d("SCRIPT_OVERFLOW_RATIO", TIMING, "Warn when the script is this long",
       "A warning appears once the script needs more than this multiple of "
       "the video's own length.",
       "float", 1.10, minimum=1.0, maximum=3.0, unit="x"),
    _d("LEAD_IN", TIMING, "Breath before speaking",
       "Silence at the start of a block before the voice comes in.",
       "float", 0.25, minimum=0.0, maximum=2.0, unit="s"),
    _d("TAIL_OUT", TIMING, "Air after speaking",
       "Silence left at the end of a block.",
       "float", 0.35, minimum=0.0, maximum=3.0, unit="s"),
    _d("TAIL_OUT_FINAL", TIMING, "Air after the last block",
       "A little more at the very end, so the video does not stop on the "
       "last syllable.",
       "float", 0.60, minimum=0.0, maximum=5.0, unit="s"),
    _d("BLOCK_GAP", TIMING, "Gap between blocks",
       "The least silence between one block finishing and the next starting.",
       "float", 0.35, minimum=0.0, maximum=3.0, unit="s"),
    _d("MAX_SPEEDUP", TIMING, "Most the voice may be sped up",
       "When narration overruns its pictures the voice is sped up, pitch "
       "preserved. Past about 8% it starts to sound rushed.",
       "float", 1.08, minimum=1.0, maximum=1.3, unit="x"),
    _d("MAX_VIDEO_SLOWDOWN", TIMING, "Most the picture may be slowed",
       "If speeding the voice up is not enough, the picture slows towards "
       "its last clean frame before freezing on it.",
       "float", 1.15, minimum=1.0, maximum=2.0, unit="x"),
    _d("PAUSE_TIGHTEN_ABOVE", TIMING, "Only tighten pauses longer than",
       "The first and least audible fix for an overrun is shortening the "
       "silences inside a block. Shorter ones are left alone.",
       "float", 0.25, minimum=0.0, maximum=2.0, unit="s"),
    _d("PAUSE_FLOOR_SENTENCE", TIMING, "Shortest pause after a sentence",
       "Tightening never goes below this, so the rhythm survives.",
       "float", 0.18, minimum=0.05, maximum=1.0, unit="s"),
    _d("PAUSE_FLOOR_COMMA", TIMING, "Shortest pause after a comma",
       "The same floor, mid-sentence.",
       "float", 0.10, minimum=0.02, maximum=1.0, unit="s"),
    _d("HOLD_MOTION", TIMING, "Movement on a held frame",
       "A frozen frame held still reads as a stall, so a long hold gets a "
       "slow push-in. Set to none for a dead freeze.",
       "str", "zoom", choices=["zoom", "none"]),
    _d("HOLD_MOTION_MIN_SECONDS", TIMING, "Hold before movement starts",
       "Freezes shorter than this stay perfectly still - nobody notices "
       "them.",
       "float", 0.5, minimum=0.0, maximum=5.0, unit="s"),
    _d("HOLD_ZOOM_MAX", TIMING, "How far the push-in goes",
       "0.04 is a 4% move, deliberately almost invisible.",
       "float", 0.04, minimum=0.0, maximum=0.3),
    _d("MIN_SCENE_SECONDS", TIMING, "Shortest scene",
       "Anything shorter is merged into its neighbour, so a flicker in the "
       "video is not treated as a picture change.",
       "float", 1.0, minimum=0.1, maximum=10.0, unit="s"),
    _d("SCENE_THRESHOLD", TIMING, "Scene change sensitivity",
       "How different two frames must be to count as a new picture. Lower "
       "finds more changes, including ones that are not there.",
       "float", 0.35, minimum=0.05, maximum=0.95),

    # ---- Audio -----------------------------------------------------------
    _d("TARGET_LUFS", AUDIO, "Target loudness",
       "Where the finished soundtrack is normalised to. -14 is what YouTube "
       "plays back at.",
       "float", -14.0, minimum=-30.0, maximum=-5.0, unit="LUFS"),
    _d("TARGET_TRUE_PEAK", AUDIO, "Peak ceiling",
       "The loudest any sample may reach, leaving headroom so encoding does "
       "not clip.",
       "float", -1.5, minimum=-6.0, maximum=0.0, unit="dBTP"),
    _d("MUSIC_DB", AUDIO, "Music level",
       "How loud background music sits under the narration.",
       "float", -22.0, minimum=-60.0, maximum=0.0, unit="dB"),
    _d("MUSIC_FADE", AUDIO, "Music fade",
       "Fade in and out at the start and end of the music bed.",
       "float", 1.5, minimum=0.0, maximum=10.0, unit="s"),
    _d("DUCK_THRESHOLD_DB", AUDIO, "Ducking threshold",
       "How loud the voice must be before the music is pushed down for it.",
       "float", -30.0, minimum=-60.0, maximum=0.0, unit="dB"),
    _d("DUCK_RATIO", AUDIO, "Ducking strength",
       "How far the music drops when the voice is speaking.",
       "float", 8.0, minimum=1.0, maximum=20.0, unit=":1"),
    _d("VOICE_HP_HZ", AUDIO, "Rumble filter",
       "Everything below this is removed from the voice - room noise, "
       "handling, breath thumps.",
       "float", 75.0, minimum=20.0, maximum=200.0, unit="Hz"),
    _d("VOICE_PRESENCE_GAIN", AUDIO, "Presence lift",
       "A small boost around 3 kHz, where speech clarity lives.",
       "float", 2.0, minimum=-6.0, maximum=8.0, unit="dB"),
    _d("DEESSER_INTENSITY", AUDIO, "De-esser",
       "How hard sharp s sounds are tamed. The voice chain is deliberately "
       "light: heavy processing undoes the likeness this project exists for.",
       "float", 0.25, minimum=0.0, maximum=1.0),
    _d("COMP_THRESHOLD_DB", AUDIO, "Compression threshold",
       "Where levelling starts working on the loud parts.",
       "float", -18.0, minimum=-40.0, maximum=0.0, unit="dB"),
    _d("COMP_RATIO", AUDIO, "Compression ratio",
       "How much the loud parts are brought down towards the quiet ones.",
       "float", 2.5, minimum=1.0, maximum=10.0, unit=":1"),

    # ---- Output ----------------------------------------------------------
    _d("VIDEO_CRF", OUTPUT, "Video quality",
       "Lower is better and bigger. 18 is visually lossless for slideshows; "
       "23 is noticeably smaller.",
       "int", 18, minimum=10, maximum=35),
    _d("VIDEO_PRESET", OUTPUT, "Encoding speed",
       "Slower presets produce a smaller file at the same quality, and cost "
       "more CPU time.",
       "str", "medium",
       choices=["ultrafast", "veryfast", "faster", "fast", "medium", "slow"]),
    _d("AUDIO_CODEC", OUTPUT, "Audio codec",
       "aac is what every platform accepts. Change only if something "
       "downstream needs otherwise.",
       "str", "aac", choices=["aac", "libmp3lame", "flac"]),
    _d("FINAL_AUDIO_BITRATE", OUTPUT, "Audio bitrate",
       "320k leaves nothing on the table for speech.",
       "str", "320k", choices=["128k", "192k", "256k", "320k"]),
    _d("SUBTITLE_MAX_CHARS", OUTPUT, "Subtitle line length",
       "Characters per line before a cue is broken.",
       "int", 42, minimum=20, maximum=80),
    _d("SUBTITLE_MIN_SECONDS", OUTPUT, "Shortest subtitle",
       "How long a cue stays on screen, unless the next one needs to start "
       "sooner - cues never overlap.",
       "float", 0.8, minimum=0.2, maximum=5.0, unit="s"),
    _d("BURN_IN_SUBTITLES", OUTPUT, "Burn subtitles into the video",
       "Permanent, and forces a full re-encode. Leave off to ship the .srt "
       "alongside instead.",
       "bool", False),
    _d("PREVIEW_SUBTITLES", OUTPUT, "Subtitles on previews",
       "Previews exist to check sync by eye, which is easier with the words "
       "on screen.",
       "bool", True),
    _d("PREVIEW_HEIGHT", OUTPUT, "Preview size",
       "Previews are small and fast on purpose.",
       "int", 480, minimum=240, maximum=1080, unit="px"),
    _d("MAX_WORD_DRIFT", OUTPUT, "Sync tolerance",
       "How far the median word may land from where it was planned before "
       "the final check fails.",
       "float", 0.10, minimum=0.02, maximum=1.0, unit="s"),
    _d("QC_MAX_SILENCE", OUTPUT, "Dead air to flag",
       "Silence this long inside a block's narration is reported. Gaps "
       "between blocks are by design and never flagged.",
       "float", 3.0, minimum=0.5, maximum=20.0, unit="s"),
    _d("FREEZE_WARN_SECONDS", OUTPUT, "Freeze worth checking",
       "Blocks held longer than this are listed for you to watch.",
       "float", 1.5, minimum=0.2, maximum=20.0, unit="s"),
    _d("KEEP_TEMP", OUTPUT, "Keep working files",
       "Leaves each job's intermediate audio and filter graphs on disk. "
       "Useful when something looks wrong, expensive otherwise.",
       "bool", False),
    _d("OUTPUT_DIR", OUTPUT, "Output folder",
       "Where jobs, uploads and logs are written. Changing it would orphan "
       "everything already made, so it is set in .env only.",
       "str", "outputs", writable=False),

    # ---- Security --------------------------------------------------------
    _d("APP_PASSWORD", SECURITY, "Dashboard password",
       "Leave empty for no sign-in. Set one if this machine is reachable "
       "from anywhere but itself.",
       "password", ""),
    _d("MAX_UPLOAD_MB", SECURITY, "Largest upload",
       "Uploads over this are refused before they fill the disk.",
       "int", 2048, minimum=16, maximum=16384, unit="MB"),
    _d("API_HOST", SECURITY, "Listen on",
       "127.0.0.1 means this machine only. 0.0.0.0 exposes the dashboard to "
       "your network, so set a password first.",
       "str", "127.0.0.1", restart_required=True),
    _d("API_PORT", SECURITY, "Port",
       "Which port the dashboard answers on.",
       "int", 8000, minimum=1024, maximum=65535, restart_required=True),
    _d("DEVICE", SECURITY, "Processing device",
       "This machine has no usable GPU, so everything runs on the CPU.",
       "str", "cpu", writable=False),
    _d("NUM_THREADS", SECURITY, "CPU threads",
       "How many threads the models may use.",
       "int", 8, minimum=1, maximum=64, writable=False),
]

BY_KEY = {s.key: s for s in SETTINGS}
WRITABLE = {s.key: s for s in SETTINGS if s.writable}


def current_value(cfg: Config, setting: SettingDef):
    """What this install is actually using right now."""
    value = getattr(cfg, setting.config_attr(), None)
    if setting.type == "password":
        return bool(value)
    if isinstance(value, Path):
        try:
            return str(value.relative_to(PROJECT_ROOT))
        except ValueError:
            return str(value)
    return value


def _same(a, b) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        try:
            return abs(float(a) - float(b)) < 1e-9
        except (TypeError, ValueError):
            return str(a) == str(b)
    return a == b


def describe(cfg: Config, locked: bool = False,
             locked_reason: str = "") -> dict:
    """The whole table, with each setting's value, default and whether it
    has been changed - grouped the way a person would look for them."""
    groups = []
    for section in SECTIONS:
        items = []
        for setting in SETTINGS:
            if setting.section != section:
                continue
            value = current_value(cfg, setting)
            items.append({
                "key": setting.key,
                "label": setting.label,
                "help": setting.help,
                "type": setting.type,
                "value": value,
                "default": setting.default,
                "differs": setting.writable and not _same(value, setting.default),
                "choices": setting.choices,
                "minimum": setting.minimum,
                "maximum": setting.maximum,
                "unit": setting.unit,
                "writable": setting.writable and not locked,
                "editable": setting.writable,
                "restart_required": setting.restart_required,
            })
        groups.append({"name": section, "help": SECTION_HELP[section],
                       "settings": items})
    return {"groups": groups, "locked": locked, "locked_reason": locked_reason}


def coerce(setting: SettingDef, value):
    """Turn a value from the dashboard into what .env should hold.

    Raises ValueError with a sentence the user can act on.
    """
    if setting.type == "bool":
        if isinstance(value, str):
            value = value.strip().lower() in ("1", "true", "yes", "on")
        return "true" if value else "false"

    if setting.type in ("str", "password"):
        text = str(value)
        if setting.choices and text not in setting.choices:
            raise ValueError(
                f"{setting.label} must be one of: {', '.join(setting.choices)}.")
        return text

    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{setting.label} must be a number.")
    if setting.minimum is not None and number < setting.minimum:
        raise ValueError(
            f"{setting.label} cannot be below {_trim(setting.minimum)}.")
    if setting.maximum is not None and number > setting.maximum:
        raise ValueError(
            f"{setting.label} cannot be above {_trim(setting.maximum)}.")
    if setting.type == "int":
        return str(int(round(number)))
    return _trim(number)


def _trim(number: float) -> str:
    text = f"{float(number):g}"
    return text
