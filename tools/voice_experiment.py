"""Find the Chatterbox setup that sounds most like the client.

Stages (exactly one model is in memory at a time; each is freed before the
next loads, because this machine is tight on RAM):

  A. Turbo        x ref_1 / ref_2 / ref_3
  B. Standard     x best reference, at 3 (exaggeration, cfg_weight) settings
  C. ChatterboxVC x the two best results so far, retargeted to the best ref

Every output is scored against the FULL original recording with ECAPA, and
transcribed with faster-whisper so a mispronouncing take can never win.

Re-running is safe: finished clips are reused instead of regenerated.

    venv\\Scripts\\python.exe tools\\voice_experiment.py
"""

from __future__ import annotations

import gc
import json
import re
import shutil
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

INPUT_DIR = PROJECT_ROOT / "input"
OUT_DIR = PROJECT_ROOT / "outputs" / "voice_test"
RESULTS_JSON = OUT_DIR / "results.json"
ORIGINAL = INPUT_DIR / "voice_sample.mp3"

TEXT = (
    "Welcome back to the channel. Today we are looking at five places "
    "you have never heard of."
)

REFS = {f"ref{i}": INPUT_DIR / f"ref_{i}.wav" for i in (1, 2, 3)}
STANDARD_SETTINGS = [(0.5, 0.5), (0.4, 0.3), (0.6, 0.3)]


def log(msg: str) -> None:
    print(f"[agent] {msg}", flush=True)


def load_results() -> dict:
    if RESULTS_JSON.is_file():
        try:
            return json.loads(RESULTS_JSON.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_results(results: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_JSON.write_text(json.dumps(results, indent=2), encoding="utf-8")


def free(obj) -> None:
    del obj
    gc.collect()


# Whisper writes "5" where the script says "five". That is a transcription
# formatting choice, NOT a mispronunciation, so map digits to words before
# comparing or correct takes get rejected.
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
    """Lowercase, drop punctuation, and spell out digits, so the comparison
    only fails on genuinely wrong or missing words."""
    cleaned = re.sub(r"[^a-z0-9 ]", " ", text.lower())
    return " ".join(NUM_WORDS.get(tok, tok) for tok in cleaned.split())


# --------------------------------------------------------------------------
# Stage A/B/C - generation
# --------------------------------------------------------------------------

def stage_turbo(results: dict) -> None:
    todo = [(f"turbo_{k}", v) for k, v in REFS.items()
            if not (OUT_DIR / f"turbo_{k}.wav").is_file()]
    if not todo:
        log("Stage A: already done, skipping.")
        return

    import torch
    import torchaudio
    from chatterbox.tts_turbo import ChatterboxTurboTTS

    log("Stage A: loading Turbo model...")
    model = ChatterboxTurboTTS.from_pretrained(device="cpu")

    for name, ref in todo:
        log(f"Stage A: generating {name}...")
        t0 = time.perf_counter()
        with torch.no_grad():
            wav = model.generate(TEXT, audio_prompt_path=str(ref))
        elapsed = time.perf_counter() - t0
        path = OUT_DIR / f"{name}.wav"
        torchaudio.save(str(path), wav, model.sr)
        results[name] = {
            "file": path.name,
            "engine": "turbo",
            "reference": ref.name,
            "settings": "defaults (turbo ignores exaggeration/cfg)",
            "gen_seconds": round(elapsed, 1),
            "audio_seconds": round(wav.shape[-1] / model.sr, 2),
        }
        save_results(results)

    free(model)
    log("Stage A: Turbo model released from memory.")


def stage_standard(results: dict, best_ref_key: str) -> None:
    ref = REFS[best_ref_key]
    todo = [
        (f"standard_{best_ref_key}_e{e}_c{c}", e, c)
        for e, c in STANDARD_SETTINGS
        if not (OUT_DIR / f"standard_{best_ref_key}_e{e}_c{c}.wav").is_file()
    ]
    if not todo:
        log("Stage B: already done, skipping.")
        return

    import torch
    import torchaudio
    from chatterbox.tts import ChatterboxTTS

    log("Stage B: loading Standard model...")
    model = ChatterboxTTS.from_pretrained(device="cpu")

    for name, exag, cfg in todo:
        log(f"Stage B: generating {name} (slow)...")
        t0 = time.perf_counter()
        with torch.no_grad():
            wav = model.generate(
                TEXT,
                audio_prompt_path=str(ref),
                exaggeration=exag,
                cfg_weight=cfg,
            )
        elapsed = time.perf_counter() - t0
        path = OUT_DIR / f"{name}.wav"
        torchaudio.save(str(path), wav, model.sr)
        results[name] = {
            "file": path.name,
            "engine": "standard",
            "reference": ref.name,
            "settings": f"exaggeration={exag}, cfg_weight={cfg}",
            "gen_seconds": round(elapsed, 1),
            "audio_seconds": round(wav.shape[-1] / model.sr, 2),
        }
        save_results(results)

    free(model)
    log("Stage B: Standard model released from memory.")


def stage_vc(results: dict, sources: list[str], best_ref_key: str) -> None:
    ref = REFS[best_ref_key]
    todo = [s for s in sources if not (OUT_DIR / f"vc_{s}.wav").is_file()]
    if not todo:
        log("Stage C: already done, skipping.")
        return

    import torch
    import torchaudio
    from chatterbox.vc import ChatterboxVC

    log("Stage C: loading voice-conversion model...")
    model = ChatterboxVC.from_pretrained(device="cpu")

    for src in todo:
        name = f"vc_{src}"
        log(f"Stage C: generating {name}...")
        t0 = time.perf_counter()
        with torch.no_grad():
            wav = model.generate(
                audio=str(OUT_DIR / f"{src}.wav"),
                target_voice_path=str(ref),
            )
        elapsed = time.perf_counter() - t0
        path = OUT_DIR / f"{name}.wav"
        torchaudio.save(str(path), wav, model.sr)
        base = results.get(src, {})
        results[name] = {
            "file": path.name,
            "engine": "vc",
            "reference": ref.name,
            "settings": f"ChatterboxVC on {src}",
            # VC cost is on top of the clip it converts
            "gen_seconds": round(elapsed + base.get("gen_seconds", 0.0), 1),
            "audio_seconds": round(wav.shape[-1] / model.sr, 2),
        }
        save_results(results)

    free(model)
    log("Stage C: VC model released from memory.")


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

def score_similarity(results: dict, names: list[str]) -> None:
    todo = [n for n in names if "similarity" not in results.get(n, {})]
    if not todo:
        return

    from voice_similarity import SpeakerEncoder, cosine_percent

    log("Loading ECAPA and scoring similarity...")
    enc = SpeakerEncoder()
    target = enc.embed_file(ORIGINAL)

    for name in todo:
        path = OUT_DIR / results[name]["file"]
        try:
            results[name]["similarity"] = round(
                cosine_percent(target, enc.embed_file(path)), 1
            )
        except Exception as exc:
            log(f"Warning: could not score {name} -> {exc}")
            results[name]["similarity"] = 0.0
        save_results(results)

    enc.free()
    free(enc)
    log("ECAPA released from memory.")


def check_words(results: dict) -> None:
    expected_now = normalise(TEXT)

    # Cheap pass first: re-evaluate anything already transcribed, so a fix to
    # the comparison rules does not need Whisper to run again.
    for name, r in results.items():
        if "transcript" in r:
            r["words_ok"] = normalise(r["transcript"]) == expected_now

    todo = [n for n, r in results.items() if "transcript" not in r]
    if not todo:
        save_results(results)
        return

    from faster_whisper import WhisperModel

    log("Loading Whisper and checking the words...")
    model = WhisperModel("small.en", device="cpu", compute_type="int8")
    expected = normalise(TEXT)

    for name in todo:
        path = OUT_DIR / results[name]["file"]
        try:
            segments, _ = model.transcribe(str(path), beam_size=5)
            heard = "".join(s.text for s in segments).strip()
            results[name]["transcript"] = heard
            results[name]["words_ok"] = normalise(heard) == expected
        except Exception as exc:
            log(f"Warning: could not transcribe {name} -> {exc}")
            results[name]["transcript"] = ""
            results[name]["words_ok"] = False
        save_results(results)

    free(model)
    log("Whisper released from memory.")


# --------------------------------------------------------------------------

def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for key, path in REFS.items():
        if not path.is_file():
            log(f"{path.name} not found. Run tools/prepare_voice.py first.")
            return 1

    results = load_results()

    # --- Stage A: Turbo with each reference -------------------------------
    stage_turbo(results)
    turbo_names = [n for n in results if n.startswith("turbo_")]
    score_similarity(results, turbo_names)

    best_turbo = max(turbo_names, key=lambda n: results[n].get("similarity", 0))
    best_ref_key = best_turbo.replace("turbo_", "")
    log(f"Best reference: {REFS[best_ref_key].name} "
        f"({results[best_turbo]['similarity']}%)")

    # --- Stage B: Standard with the best reference ------------------------
    stage_standard(results, best_ref_key)
    score_similarity(results, list(results.keys()))

    # --- Stage C: voice conversion on the two best so far -----------------
    ranked = sorted(
        (n for n in results if not n.startswith("vc_")),
        key=lambda n: results[n].get("similarity", 0),
        reverse=True,
    )
    top_two = ranked[:2]
    log(f"Top 2 for voice conversion: {', '.join(top_two)}")
    stage_vc(results, top_two, best_ref_key)
    score_similarity(results, list(results.keys()))

    # --- Words ------------------------------------------------------------
    check_words(results)
    save_results(results)

    # --- Report -----------------------------------------------------------
    order = sorted(results, key=lambda n: results[n].get("similarity", 0), reverse=True)

    print()
    print("=" * 96)
    print("  RANKED RESULTS  (similarity vs the full original recording)")
    print("=" * 96)
    print(f"  {'#':<3} {'setup':<34} {'sim%':>6} {'words':>6} {'gen s':>7}  settings")
    print("  " + "-" * 92)
    for i, name in enumerate(order, start=1):
        r = results[name]
        ok = "OK" if r.get("words_ok") else "BAD"
        print(f"  {i:<3} {name:<34} {r.get('similarity', 0):6.1f} {ok:>6} "
              f"{r.get('gen_seconds', 0):7.1f}  {r.get('settings', '')}")

    print()
    print("  Baseline: previous clone scored 76.3% (standard) / 74.6% (turbo)")
    print("  A reference clip cut from the original scores ~96% (ceiling).")

    # Only clips with correct words may be offered for listening.
    clean = [n for n in order if results[n].get("words_ok")]
    rejected = [n for n in order if not results[n].get("words_ok")]
    if rejected:
        print()
        print("  Rejected for wrong words:")
        for n in rejected:
            print(f"    {n}: {results[n].get('transcript', '')!r}")

    print()
    for i, name in enumerate(clean[:3], start=1):
        dest = OUT_DIR / f"BEST_{i}.wav"
        shutil.copyfile(OUT_DIR / results[name]["file"], dest)
        print(f"  BEST_{i}.wav  <-  {name}  ({results[name]['similarity']}%)")

    if clean:
        winner = clean[0]
        print()
        print(f"  WINNER: {winner}")
        print(f"    engine     : {results[winner]['engine']}")
        print(f"    reference  : {results[winner]['reference']}")
        print(f"    settings   : {results[winner]['settings']}")
        print(f"    similarity : {results[winner]['similarity']}%")

    print()
    log("Experiment complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
