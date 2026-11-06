"""Chatterbox synthesizer.

Chatterbox gets noticeably worse on long input, so a block is never handed
over whole: it is cut into sentence-sized chunks (under ~250 chars), each
chunk is generated on its own, and the pieces are joined with a natural
pause - longer after a full stop than after a comma. Edge silence is then
trimmed so the block starts and ends cleanly.

The model is loaded once per run and freed explicitly; this machine does not
have the RAM to keep two models resident.
"""

from __future__ import annotations

import gc
import random
import sys
import warnings
from pathlib import Path

import numpy as np

warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.config import Config  # noqa: E402
from agent import log as agent_log  # noqa: E402
from agent.script_parser import chunk_for_tts, trailing_punctuation  # noqa: E402
from agent.voice.base import SynthResult  # noqa: E402

SENTENCE_ENDS = ".!?"
COMMA_ENDS = ",;:"


class ChatterboxSynthesizer:
    def __init__(self, cfg: Config, variant: str = "standard", log=None) -> None:
        self.cfg = cfg
        self.variant = variant
        self.name = variant
        self.log = log or agent_log.get()
        self.model = None
        self.sample_rate = 24_000

    # -- lifecycle ---------------------------------------------------------

    def load(self) -> None:
        if self.model is not None:
            return
        self.log.user(f"Loading Chatterbox ({self.variant}) model...")
        import torch

        torch.set_num_threads(self.cfg.num_threads)

        if self.variant == "turbo":
            from chatterbox.tts_turbo import ChatterboxTurboTTS as Model
        else:
            from chatterbox.tts import ChatterboxTTS as Model

        self.model = Model.from_pretrained(device=self.cfg.device)
        self.sample_rate = self.model.sr
        self.log.debug("chatterbox %s loaded, sr=%d", self.variant, self.sample_rate)

    def free(self) -> None:
        if self.model is None:
            return
        self.model = None
        gc.collect()
        self.log.debug("chatterbox %s freed", self.variant)

    # -- generation --------------------------------------------------------

    def _seed(self, seed: int | None) -> None:
        if seed is None:
            return
        import torch

        torch.manual_seed(seed)
        random.seed(seed)
        np.random.seed(seed % (2**32))

    def _pause_after(self, chunk: str) -> float:
        last = trailing_punctuation(chunk)
        if last in SENTENCE_ENDS:
            return self.cfg.tts_pause_sentence
        if last in COMMA_ENDS:
            return self.cfg.tts_pause_comma
        return self.cfg.tts_pause_comma

    def _generate_chunk(self, text: str):
        import torch

        kwargs = {"audio_prompt_path": str(self.cfg.voice_sample)}
        if self.variant != "turbo":
            # Turbo ignores these and logs a warning if they are passed.
            kwargs["exaggeration"] = self.cfg.tts_exaggeration
            kwargs["cfg_weight"] = self.cfg.tts_cfg_weight
        kwargs["temperature"] = self.cfg.tts_temperature

        with torch.no_grad():
            wav = self.model.generate(text, **kwargs)
        return wav.squeeze(0).cpu().numpy().astype(np.float32)

    def synthesize(self, text: str, out_path: Path,
                   seed: int | None = None) -> SynthResult:
        if self.model is None:
            self.load()

        import soundfile as sf

        chunks = chunk_for_tts(text, self.cfg.tts_max_chunk_chars)
        if not chunks:
            raise ValueError("Cannot synthesize empty text.")

        self._seed(seed)
        sr = self.sample_rate
        pieces: list[np.ndarray] = []

        for i, chunk in enumerate(chunks):
            self.log.debug("chunk %d/%d (%d chars): %s",
                           i + 1, len(chunks), len(chunk), chunk[:60])
            audio = self._generate_chunk(chunk)
            pieces.append(audio)
            if i < len(chunks) - 1:
                gap = int(self._pause_after(chunk) * sr)
                if gap > 0:
                    pieces.append(np.zeros(gap, dtype=np.float32))

        joined = np.concatenate(pieces) if len(pieces) > 1 else pieces[0]
        joined = self._trim_edges(joined, sr)

        out_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(out_path), joined, sr, subtype="PCM_16")

        return SynthResult(
            path=out_path,
            duration=len(joined) / sr,
            sample_rate=sr,
            chunks=len(chunks),
        )

    def _trim_edges(self, audio: np.ndarray, sr: int) -> np.ndarray:
        """Silence at the very start and end only - pauses inside the block
        are deliberate and must survive."""
        try:
            import librosa

            trimmed, _ = librosa.effects.trim(
                audio, top_db=self.cfg.tts_trim_top_db
            )
            if trimmed.size > sr * 0.05:
                return trimmed.astype(np.float32)
        except Exception as exc:
            self.log.debug("trim skipped: %s", exc)
        return audio
