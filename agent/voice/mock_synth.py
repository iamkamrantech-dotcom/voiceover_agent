"""A fake synthesizer so pipeline tests run in seconds instead of hours.

Each word becomes a short tone whose pitch comes from the word itself and
whose length follows the word's length, with real pauses at punctuation. The
result is not speech - it just has the same SHAPE as speech, which is all the
orchestration needs to be tested.

Because tones are not words, Whisper cannot transcribe them. So the mock also
writes a sidecar JSON holding the exact word timings it used, and MockVerifier
reads that back. The mock path therefore exercises the whole voice stage -
caching, best-of-N, retries, timings - without loading a single model.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.config import Config  # noqa: E402
from agent import log as agent_log  # noqa: E402
from agent.script_parser import trailing_punctuation  # noqa: E402
from agent.voice.base import SynthResult, VerifyResult, WordTiming  # noqa: E402

SAMPLE_RATE = 24_000
SECONDS_PER_CHAR = 0.055
MIN_WORD_SECONDS = 0.14
GAP_SECONDS = 0.06


def _sidecar(path: Path) -> Path:
    return path.with_suffix(".words.json")


class MockSynthesizer:
    name = "mock"

    def __init__(self, cfg: Config, log=None) -> None:
        self.cfg = cfg
        self.log = log or agent_log.get()
        self.sample_rate = SAMPLE_RATE
        self._loaded = False

    def load(self) -> None:
        self._loaded = True
        self.log.debug("mock synthesizer ready (no model)")

    def free(self) -> None:
        self._loaded = False

    def _tone(self, word: str, seconds: float, seed: int) -> np.ndarray:
        digest = hashlib.sha1(f"{word}|{seed}".encode("utf-8")).digest()
        freq = 120.0 + (digest[0] / 255.0) * 180.0
        n = max(1, int(seconds * self.sample_rate))
        t = np.arange(n, dtype=np.float32) / self.sample_rate
        wave = 0.28 * np.sin(2 * np.pi * freq * t)
        wave += 0.08 * np.sin(2 * np.pi * freq * 2 * t)
        # soft edges so joins do not click
        ramp = max(1, int(0.01 * self.sample_rate))
        envelope = np.ones(n, dtype=np.float32)
        envelope[:ramp] = np.linspace(0, 1, ramp)
        envelope[-ramp:] = np.linspace(1, 0, ramp)
        return (wave * envelope).astype(np.float32)

    def synthesize(self, text: str, out_path: Path,
                   seed: int | None = None) -> SynthResult:
        import soundfile as sf

        seed = 0 if seed is None else seed
        words = text.split()
        if not words:
            raise ValueError("Khali text synthesize nahi ho sakta.")

        pieces: list[np.ndarray] = []
        timings: list[dict] = []
        cursor = 0.0

        for i, word in enumerate(words):
            seconds = max(MIN_WORD_SECONDS, len(word) * SECONDS_PER_CHAR)
            pieces.append(self._tone(word, seconds, seed))
            timings.append({"word": word, "start": round(cursor, 3),
                            "end": round(cursor + seconds, 3)})
            cursor += seconds

            if i < len(words) - 1:
                last = trailing_punctuation(word)
                if last in ".!?":
                    gap = self.cfg.tts_pause_sentence
                elif last in ",;:":
                    gap = self.cfg.tts_pause_comma
                else:
                    gap = GAP_SECONDS
                pieces.append(np.zeros(int(gap * self.sample_rate), dtype=np.float32))
                cursor += gap

        audio = np.concatenate(pieces)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(out_path), audio, self.sample_rate, subtype="PCM_16")
        _sidecar(out_path).write_text(
            json.dumps({"text": text, "words": timings}, indent=2),
            encoding="utf-8",
        )

        return SynthResult(path=out_path, duration=len(audio) / self.sample_rate,
                           sample_rate=self.sample_rate, chunks=len(words))


class MockVerifier:
    """Reads back what MockSynthesizer recorded. Similarity is a stable fake
    derived from the text, so best-of-N still has something to rank."""

    name = "mock"

    def __init__(self, cfg: Config, log=None) -> None:
        self.cfg = cfg
        self.log = log or agent_log.get()

    def load(self) -> None:
        self.log.debug("mock verifier ready (no model)")

    def free(self) -> None:
        pass

    def verify(self, wav: Path, expected_text: str) -> VerifyResult:
        side = _sidecar(Path(wav))
        if not side.is_file():
            return VerifyResult(similarity=0.0, words_ok=False,
                                transcript="", words=[])

        data = json.loads(side.read_text(encoding="utf-8"))
        words = [WordTiming(w["word"], w["start"], w["end"]) for w in data["words"]]
        transcript = data.get("text", "")

        # Deterministic pseudo-score in a believable range, so ranking takes
        # is exercised without pretending this measures anything real.
        digest = hashlib.sha1(str(wav.name).encode("utf-8")).digest()
        similarity = 70.0 + (digest[0] / 255.0) * 20.0

        return VerifyResult(
            similarity=round(similarity, 1),
            words_ok=transcript.split() == expected_text.split(),
            transcript=transcript,
            words=words,
        )
