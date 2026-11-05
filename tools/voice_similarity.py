"""Speaker similarity scoring with SpeechBrain ECAPA-TDNN.

Deliberately uses a model that is INDEPENDENT of Chatterbox. Chatterbox ships
its own voice encoder, but scoring with that would reward whatever the TTS
already optimises for, not whether a human hears the client.

Score = cosine similarity between speaker embeddings, shown as a percentage.
It is a similarity measure, not a probability: for ECAPA, same-speaker pairs
usually land around 60-90%, different speakers around 10-40%.

    venv\\Scripts\\python.exe tools\\voice_similarity.py TARGET.wav CAND.wav [...]
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

import librosa
import numpy as np

warnings.filterwarnings("ignore", category=UserWarning)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = PROJECT_ROOT / "models" / "ecapa"

ECAPA_SR = 16_000
CHUNK_SECONDS = 8.0
MIN_CHUNK_SECONDS = 2.0


def log(msg: str) -> None:
    print(f"[agent] {msg}", flush=True)


class SpeakerEncoder:
    """Thin wrapper so the experiment can load it, use it, and free it."""

    def __init__(self) -> None:
        import torch
        from speechbrain.inference.speaker import EncoderClassifier

        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        kwargs = dict(
            source="speechbrain/spkrec-ecapa-voxceleb",
            savedir=str(MODEL_DIR),
            run_opts={"device": "cpu"},
        )
        try:
            # speechbrain >=1.0: copy instead of symlink (Windows-friendly)
            from speechbrain.utils.fetching import LocalStrategy

            self.model = EncoderClassifier.from_hparams(
                local_strategy=LocalStrategy.COPY, **kwargs
            )
        except Exception:
            self.model = EncoderClassifier.from_hparams(**kwargs)
        self.torch = torch

    def embed_file(self, path: Path | str) -> np.ndarray:
        """One L2-normalised embedding per file, averaged over speech chunks."""
        y, _ = librosa.load(str(path), sr=ECAPA_SR, mono=True)
        if y.size == 0:
            raise ValueError(f"Empty audio: {path}")

        chunk = int(CHUNK_SECONDS * ECAPA_SR)
        min_chunk = int(MIN_CHUNK_SECONDS * ECAPA_SR)

        pieces: list[np.ndarray] = []
        if len(y) <= chunk:
            pieces = [y]
        else:
            for start in range(0, len(y), chunk):
                seg = y[start : start + chunk]
                if len(seg) >= min_chunk:
                    pieces.append(seg)

        # Drop near-silent chunks so padding/pauses do not drag the average.
        loud = [p for p in pieces if float(np.sqrt(np.mean(p**2))) > 1e-3]
        if loud:
            pieces = loud

        embeds = []
        for seg in pieces:
            tensor = self.torch.from_numpy(seg).float().unsqueeze(0)
            with self.torch.no_grad():
                emb = self.model.encode_batch(tensor).squeeze().cpu().numpy()
            emb = emb / (np.linalg.norm(emb) + 1e-9)
            embeds.append(emb)

        mean = np.mean(embeds, axis=0)
        return mean / (np.linalg.norm(mean) + 1e-9)

    def free(self) -> None:
        self.model = None


def cosine_percent(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity as a percentage, floored at 0."""
    return max(0.0, float(np.dot(a, b))) * 100.0


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2

    target = Path(sys.argv[1])
    candidates = [Path(p) for p in sys.argv[2:]]

    log("ECAPA speaker model load ho raha hai...")
    enc = SpeakerEncoder()

    log(f"Target embedding ban rahi hai: {target.name}")
    target_emb = enc.embed_file(target)

    print()
    print(f"  {'file':<42} {'similarity':>10}")
    print("  " + "-" * 53)
    for path in candidates:
        try:
            score = cosine_percent(target_emb, enc.embed_file(path))
            print(f"  {path.name:<42} {score:9.1f}%")
        except Exception as exc:
            print(f"  {path.name:<42}  FAIL: {exc}")

    enc.free()
    return 0


if __name__ == "__main__":
    sys.exit(main())
