"""Phase test: prove Chatterbox TTS + faster-whisper work on this CPU-only machine.

Run from the project root:

    venv\\Scripts\\python.exe tests\\test_voice.py              # standard model
    venv\\Scripts\\python.exe tests\\test_voice.py --model both # standard + turbo

User-facing messages are in simple Roman Urdu (project rule); code and
comments stay English.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INPUT_DIR = PROJECT_ROOT / "input"
OUTPUT_DIR = PROJECT_ROOT / "outputs"

SENTENCE = (
    "Welcome back to the channel. Today we are looking at five places "
    "you have never heard of."
)

AUDIO_EXTS = (".wav", ".mp3", ".flac", ".m4a", ".ogg", ".opus")

# Turbo asserts the reference clip is longer than this.
TURBO_MIN_REF_SECONDS = 5.0


def log(msg: str) -> None:
    """User-facing log line, Roman Urdu."""
    print(f"[agent] {msg}", flush=True)


def find_voice_sample() -> Path | None:
    """Prefer input/voice_sample.wav, else the first audio file in input/."""
    preferred = INPUT_DIR / "voice_sample.wav"
    if preferred.is_file():
        return preferred
    if INPUT_DIR.is_dir():
        for path in sorted(INPUT_DIR.iterdir()):
            if path.is_file() and path.suffix.lower() in AUDIO_EXTS:
                return path
    return None


def ref_duration_seconds(path: Path) -> float | None:
    try:
        import soundfile as sf

        info = sf.info(str(path))
        return info.frames / info.samplerate
    except Exception:
        return None


@dataclass
class TtsResult:
    label: str
    wav_path: Path
    gen_seconds: float
    audio_seconds: float
    load_seconds: float

    @property
    def ratio(self) -> float:
        """How many times slower than real time. >1 means slower."""
        return self.gen_seconds / self.audio_seconds if self.audio_seconds else float("nan")


def synthesize(label: str, out_path: Path, voice_sample: Path | None) -> TtsResult:
    """Load one Chatterbox variant on CPU and generate SENTENCE."""
    import torch
    import torchaudio

    torch.set_num_threads(os.cpu_count() or 4)

    if label == "turbo":
        from chatterbox.tts_turbo import ChatterboxTurboTTS as Model
    else:
        from chatterbox.tts import ChatterboxTTS as Model

    log(f"{label}: loading model (first run downloads it)...")
    t0 = time.perf_counter()
    model = Model.from_pretrained(device="cpu")
    load_seconds = time.perf_counter() - t0
    log(f"{label}: model loaded in {load_seconds:.1f}s.")

    kwargs: dict = {}
    if voice_sample is not None:
        kwargs["audio_prompt_path"] = str(voice_sample)

    log(f"{label}: generating narration, slow on CPU...")
    t0 = time.perf_counter()
    with torch.no_grad():
        wav = model.generate(SENTENCE, **kwargs)
    gen_seconds = time.perf_counter() - t0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    torchaudio.save(str(out_path), wav, model.sr)

    audio_seconds = wav.shape[-1] / model.sr
    log(f"{label}: audio saved to {out_path}")

    # Free memory before the next variant loads.
    del model
    return TtsResult(label, out_path, gen_seconds, audio_seconds, load_seconds)


def transcribe(wav_path: Path) -> tuple[str, list, float, float]:
    """faster-whisper small.en, int8, CPU. Returns transcript, words, load+run times."""
    from faster_whisper import WhisperModel

    log("Loading Whisper (small.en, int8, CPU)...")
    t0 = time.perf_counter()
    model = WhisperModel(
        "small.en",
        device="cpu",
        compute_type="int8",
        cpu_threads=os.cpu_count() or 4,
    )
    load_seconds = time.perf_counter() - t0

    log(f"Transcribing {wav_path.name}")
    t0 = time.perf_counter()
    segments, _info = model.transcribe(
        str(wav_path),
        beam_size=5,
        word_timestamps=True,
    )
    words = []
    text_parts = []
    for segment in segments:  # generator -> consuming it does the real work
        text_parts.append(segment.text)
        for word in segment.words or []:
            words.append(word)
    run_seconds = time.perf_counter() - t0

    return "".join(text_parts).strip(), words, load_seconds, run_seconds


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model",
        choices=["standard", "turbo", "both"],
        default="both",
        help="Which Chatterbox variant(s) to test.",
    )
    args = parser.parse_args()

    print("=" * 72)
    print("  Voiceover Agent - voice & speech smoke test (CPU)")
    print("=" * 72)

    import torch

    log(f"torch {torch.__version__} | CUDA available: {torch.cuda.is_available()}")
    if torch.cuda.is_available():
        log("A GPU is present, but this test runs on CPU anyway.")
    else:
        log("No NVIDIA GPU, everything runs on CPU.")

    voice_sample = find_voice_sample()
    ref_seconds = None
    if voice_sample is not None:
        ref_seconds = ref_duration_seconds(voice_sample)
        dur_txt = f"{ref_seconds:.1f}s" if ref_seconds else "lambai maloom nahi"
        log(f"Voice reference found: {voice_sample.name} ({dur_txt}), cloning this.")
    else:
        log("No voice sample found (input/ is empty), using the DEFAULT voice.")
        log("Put 7-20 seconds of clean audio in input/voice_sample.wav to clone a voice.")

    wanted = ["standard", "turbo"] if args.model == "both" else [args.model]

    # Turbo refuses reference clips shorter than 5 seconds.
    turbo_sample = voice_sample
    if voice_sample is not None and ref_seconds is not None and ref_seconds <= TURBO_MIN_REF_SECONDS:
        log(
            f"Warning: Turbo needs a sample longer than {TURBO_MIN_REF_SECONDS:.0f}s, "
            f"yours is {ref_seconds:.1f}s, so Turbo will use its default voice."
        )
        turbo_sample = None

    results: list[TtsResult] = []
    for label in wanted:
        out_name = "test_voice.wav" if label == "standard" else "test_voice_turbo.wav"
        sample = turbo_sample if label == "turbo" else voice_sample
        try:
            results.append(synthesize(label, OUTPUT_DIR / out_name, sample))
        except Exception as exc:  # keep going so one broken variant does not kill the test
            log(f"PROBLEM: {label} model failed -> {type(exc).__name__}: {exc}")

    if not results:
        log("No model ran. Test failed.")
        return 1

    print()
    print("-" * 72)
    print("  TTS SPEED (CPU)")
    print("-" * 72)
    for r in results:
        print(
            f"  {r.label:<9} load {r.load_seconds:7.1f}s | "
            f"generate {r.gen_seconds:7.1f}s | audio {r.audio_seconds:5.1f}s | "
            f"{r.ratio:5.2f}x real time"
        )

    for r in results:
        print()
        print("-" * 72)
        print(f"  TRANSCRIPT CHECK - {r.label}  ({r.wav_path.name})")
        print("-" * 72)
        try:
            text, words, w_load, w_run = transcribe(r.wav_path)
        except Exception as exc:
            log(f"PROBLEM: whisper failed -> {type(exc).__name__}: {exc}")
            continue

        print(f"  whisper load: {w_load:.1f}s | transcribe: {w_run:.1f}s "
              f"({w_run / r.audio_seconds:.2f}x real time)")
        print(f"  expected : {SENTENCE}")
        print(f"  heard    : {text}")
        print(f"  words    : {len(words)}")
        for w in words:
            print(f"      {w.start:6.2f} -> {w.end:6.2f}  {w.word}")

    print()
    log("Test complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
