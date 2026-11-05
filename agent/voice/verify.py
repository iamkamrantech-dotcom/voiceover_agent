"""The real quality gate: is it the client's voice, and are the words right?

Two independent models, both already proven in tools/:
  * SpeechBrain ECAPA  - speaker similarity against the full client recording
  * faster-whisper     - transcript and word timings

Both are loaded together (they are the pass-2 pair) and freed together.
"""

from __future__ import annotations

import gc
import re
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=UserWarning)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
TOOLS = PROJECT_ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from agent.config import Config  # noqa: E402
from agent import log as agent_log  # noqa: E402
from agent.voice.base import VerifyResult, WordTiming  # noqa: E402

# Whisper writes "5" where the script says "five"; that is a transcription
# style, not a mispronunciation, so normalise before comparing.
NUM_WORDS = {
    "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
    "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine",
    "10": "ten", "11": "eleven", "12": "twelve", "13": "thirteen",
    "14": "fourteen", "15": "fifteen", "16": "sixteen", "17": "seventeen",
    "18": "eighteen", "19": "nineteen", "20": "twenty", "30": "thirty",
    "40": "forty", "50": "fifty", "60": "sixty", "70": "seventy",
    "80": "eighty", "90": "ninety", "100": "hundred", "1000": "thousand",
    "&": "and", "%": "percent",
}


def normalise(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9 ]", " ", text.lower())
    return " ".join(NUM_WORDS.get(t, t) for t in cleaned.split())


class RealVerifier:
    name = "ecapa+whisper"

    def __init__(self, cfg: Config, log=None) -> None:
        self.cfg = cfg
        self.log = log or agent_log.get()
        self.encoder = None
        self.whisper = None
        self.target_embedding = None

    def load(self) -> None:
        if self.encoder is None:
            from voice_similarity import SpeakerEncoder

            self.log.user("Quality check ke models load ho rahe hain...")
            self.encoder = SpeakerEncoder()
            if self.cfg.voice_target.is_file():
                self.target_embedding = self.encoder.embed_file(self.cfg.voice_target)
            else:
                self.log.warning(
                    f"VOICE_TARGET nahi mila ({self.cfg.voice_target.name}) - "
                    f"similarity score nahi niklega."
                )

        if self.whisper is None:
            from faster_whisper import WhisperModel

            self.whisper = WhisperModel(
                self.cfg.whisper_model,
                device=self.cfg.device,
                compute_type=self.cfg.whisper_compute_type,
                cpu_threads=self.cfg.num_threads,
            )
        self.log.debug("verifier loaded (whisper=%s)", self.cfg.whisper_model)

    def free(self) -> None:
        if self.encoder is not None:
            try:
                self.encoder.free()
            except Exception:
                pass
        self.encoder = None
        self.whisper = None
        self.target_embedding = None
        gc.collect()
        self.log.debug("verifier freed")

    def verify(self, wav: Path, expected_text: str) -> VerifyResult:
        if self.whisper is None:
            self.load()

        similarity = None
        if self.encoder is not None and self.target_embedding is not None:
            try:
                from voice_similarity import cosine_percent

                emb = self.encoder.embed_file(wav)
                similarity = round(cosine_percent(self.target_embedding, emb), 1)
            except Exception as exc:
                self.log.warning(f"similarity nahi nikal saki: {exc}")

        words: list[WordTiming] = []
        transcript = ""
        try:
            segments, _info = self.whisper.transcribe(
                str(wav),
                beam_size=self.cfg.whisper_beam_size,
                word_timestamps=True,
            )
            parts = []
            for segment in segments:
                parts.append(segment.text)
                for w in segment.words or []:
                    words.append(WordTiming(w.word.strip(), float(w.start),
                                            float(w.end)))
            transcript = "".join(parts).strip()
        except Exception as exc:
            self.log.warning(f"transcribe fail: {exc}")

        return VerifyResult(
            similarity=similarity,
            words_ok=normalise(transcript) == normalise(expected_text),
            transcript=transcript,
            words=words,
        )
