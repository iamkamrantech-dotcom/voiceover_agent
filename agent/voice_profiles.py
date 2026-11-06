"""Client voice profiles: prepare candidates, score them, keep the history.

A profile is one client recording plus the reference clips cut from it. The
active profile decides which reference Chatterbox clones from, and old
profiles are never deleted, so switching back is always possible.

Candidate selection reuses tools/prepare_voice6.py - the windows whose
FIRST 6 SECONDS are clean continuous speech, because that is the only part
the standard model's speaker encoder actually reads (CLAUDE.md 4b-2).
"""

from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
TOOLS = PROJECT_ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from agent.config import Config  # noqa: E402
from agent import log as agent_log  # noqa: E402

VOICES_DIR = PROJECT_ROOT / "voices"
ACTIVE_FILE = VOICES_DIR / "active.json"
TEST_SENTENCE = ("Welcome back to the channel. Today we are looking at five "
                 "places you have never heard of.")


@dataclass
class Candidate:
    name: str
    path: str
    start: float
    end: float
    score: float
    similarity: float | None = None
    sample: str | None = None


@dataclass
class Profile:
    name: str
    created: str
    source: str
    candidates: list[dict]
    best: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def profile_dir(name: str) -> Path:
    return VOICES_DIR / name


def list_profiles() -> list[Profile]:
    out: list[Profile] = []
    if not VOICES_DIR.is_dir():
        return out
    for folder in sorted(VOICES_DIR.iterdir(), reverse=True):
        meta = folder / "profile.json"
        if folder.is_dir() and meta.is_file():
            try:
                out.append(Profile(**json.loads(meta.read_text(encoding="utf-8"))))
            except Exception:
                continue
    return out


def active_reference() -> Path | None:
    """The reference clip Config should clone from, if one has been set."""
    if not ACTIVE_FILE.is_file():
        return None
    try:
        data = json.loads(ACTIVE_FILE.read_text(encoding="utf-8"))
        path = Path(data["reference"])
        return path if path.is_file() else None
    except Exception:
        return None


def active_info() -> dict | None:
    if not ACTIVE_FILE.is_file():
        return None
    try:
        return json.loads(ACTIVE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def set_active(profile_name: str, candidate: str, log=None) -> dict:
    folder = profile_dir(profile_name)
    reference = folder / candidate
    if not reference.is_file():
        raise FileNotFoundError(f"{candidate} not found")
    source = next((p for p in folder.glob("source.*")), None)
    data = {
        "profile": profile_name,
        "candidate": candidate,
        "reference": str(reference),
        "source": str(source) if source else None,
        "set_at": datetime.now().isoformat(timespec="seconds"),
    }
    VOICES_DIR.mkdir(parents=True, exist_ok=True)
    ACTIVE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    if log:
        log.user(f"Active voice set to {profile_name}/{candidate}")
    return data


def prepare_profile(recording: Path, cfg: Config, name: str | None = None,
                    engine: str | None = None, log=None,
                    score: bool = True) -> Profile:
    """Cut candidates from a new recording and (optionally) score each one
    by cloning a short test sentence and measuring speaker similarity."""
    log = log or agent_log.get()
    import librosa
    import soundfile as sf
    from prepare_voice6 import CLIP_SECONDS, analyse, pick_spread, process

    name = name or f"voice_{datetime.now():%Y%m%d_%H%M%S}"
    folder = profile_dir(name)
    folder.mkdir(parents=True, exist_ok=True)

    stored_source = folder / f"source{recording.suffix.lower()}"
    if Path(recording).resolve() != stored_source.resolve():
        shutil.copyfile(recording, stored_source)

    log.user("Reading recording...")
    audio, sr = librosa.load(str(stored_source), sr=24_000, mono=True)
    if len(audio) < 24_000 * (CLIP_SECONDS + 1):
        raise ValueError("The recording is too short. At least 15 seconds are "
                         "chahiye.")

    windows = pick_spread(analyse(audio, sr), 4)
    candidates: list[Candidate] = []
    for i, window in enumerate(windows, start=1):
        start = int(window.start_s * sr)
        clip = process(audio[start:start + int(CLIP_SECONDS * sr)], sr)
        path = folder / f"ref_{i}.wav"
        sf.write(str(path), clip, sr, subtype="PCM_16")
        candidates.append(Candidate(
            name=path.name, path=str(path),
            start=round(window.start_s, 2), end=round(window.end_s, 2),
            score=round(window.score, 1),
        ))
    log.user(f"{len(candidates)} candidate clips ready.")

    if score:
        _score_candidates(candidates, folder, cfg, engine or cfg.tts_engine, log)

    best = None
    scored = [c for c in candidates if c.similarity is not None]
    if scored:
        best = max(scored, key=lambda c: c.similarity).name
    elif candidates:
        best = candidates[0].name

    profile = Profile(
        name=name,
        created=datetime.now().isoformat(timespec="seconds"),
        source=str(stored_source),
        candidates=[asdict(c) for c in candidates],
        best=best,
    )
    (folder / "profile.json").write_text(
        json.dumps(profile.to_dict(), indent=2), encoding="utf-8")
    return profile


def _score_candidates(candidates: list[Candidate], folder: Path, cfg: Config,
                      engine: str, log) -> None:
    """Clone a short sentence with each candidate and score the result
    against the full recording. One model load for all of them."""
    from agent.voice.base import make_synthesizer

    source = next((p for p in folder.glob("source.*")), None)
    samples: list[tuple[Candidate, Path]] = []

    log.user("Generating a test sentence with each candidate...")
    original = cfg.voice_sample
    synth = make_synthesizer(cfg, engine, log=log)
    synth.load()
    try:
        for candidate in candidates:
            cfg.voice_sample = Path(candidate.path)
            out = folder / f"sample_{candidate.name}"
            synth.synthesize(TEST_SENTENCE, out, seed=cfg.tts_seed or 1234)
            candidate.sample = str(out)
            samples.append((candidate, out))
    finally:
        synth.free()
        cfg.voice_sample = original

    if engine == "mock" or source is None:
        # Tones are not speech, so similarity would be meaningless.
        log.debug("skipping similarity scoring for engine=%s", engine)
        return

    try:
        from voice_similarity import SpeakerEncoder, cosine_percent

        log.user("Scoring each sample against the client recording...")
        encoder = SpeakerEncoder()
        target = encoder.embed_file(source)
        for candidate, sample in samples:
            candidate.similarity = round(
                cosine_percent(target, encoder.embed_file(sample)), 1)
        encoder.free()
    except Exception as exc:
        log.warning(f"Could not score similarity: {exc}")
