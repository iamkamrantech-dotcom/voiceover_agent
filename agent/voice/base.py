"""The swappable voice stage (see CLAUDE.md 4c).

    text ──▶ [ SYNTHESIZER ]──▶ speech ──▶ [ CONVERTER? ]──▶ final audio

Two small protocols and a factory. Nothing outside this package may import a
concrete engine: ask the factory, get something that speaks this interface.
Everything downstream deals in wav files only - never tensors or model
objects - so swapping Chatterbox for anything else changes nothing else.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.config import Config  # noqa: E402


@dataclass
class SynthResult:
    path: Path
    duration: float
    sample_rate: int
    chunks: int = 1


@dataclass
class WordTiming:
    word: str
    start: float
    end: float


@dataclass
class VerifyResult:
    similarity: float | None
    words_ok: bool
    transcript: str = ""
    words: list[WordTiming] = field(default_factory=list)


@runtime_checkable
class Synthesizer(Protocol):
    """Turns text into a wav file."""

    name: str
    sample_rate: int

    def load(self) -> None: ...
    def synthesize(self, text: str, out_path: Path,
                   seed: int | None = None) -> SynthResult: ...
    def free(self) -> None: ...


@runtime_checkable
class VoiceConverter(Protocol):
    """Changes the voice of an existing wav. Always runs AFTER the
    synthesizer, and must not alter word timing - any alignment taken
    before conversion has to be recomputed afterwards."""

    name: str

    def load(self) -> None: ...
    def convert(self, in_path: Path, out_path: Path) -> Path: ...
    def free(self) -> None: ...


@runtime_checkable
class Verifier(Protocol):
    """Scores a wav: is it the right voice, and are the words right?"""

    name: str

    def load(self) -> None: ...
    def verify(self, wav: Path, expected_text: str) -> VerifyResult: ...
    def free(self) -> None: ...


# --------------------------------------------------------------------------
# factories
# --------------------------------------------------------------------------

def make_synthesizer(cfg: Config, name: str | None = None,
                     log=None) -> Synthesizer:
    engine = (name or cfg.tts_engine).lower()

    if engine == "mock":
        from agent.voice.mock_synth import MockSynthesizer

        return MockSynthesizer(cfg, log=log)

    if engine in ("standard", "turbo"):
        from agent.voice.chatterbox_synth import ChatterboxSynthesizer

        return ChatterboxSynthesizer(cfg, variant=engine, log=log)

    raise ValueError(
        f"TTS_ENGINE '{engine}' pehchana nahi gaya. "
        f"Use: standard | turbo | mock"
    )


def make_converter(cfg: Config, name: str | None = None,
                   log=None) -> VoiceConverter | None:
    converter = (name or cfg.voice_converter).lower()
    if converter in ("", "none", "off", "false"):
        return None
    # chatterbox_vc and rvc are planned; neither is wired up yet.
    raise ValueError(
        f"VOICE_CONVERTER '{converter}' abhi available nahi hai. "
        f"Filhal sirf 'none' chalega."
    )


def make_verifier(cfg: Config, synth_name: str | None = None,
                  log=None) -> Verifier:
    """The mock synthesizer produces tones, not speech, so it gets a verifier
    that reads the timings the mock itself recorded."""
    engine = (synth_name or cfg.tts_engine).lower()
    if engine == "mock":
        from agent.voice.mock_synth import MockVerifier

        return MockVerifier(cfg, log=log)

    from agent.voice.verify import RealVerifier

    return RealVerifier(cfg, log=log)
