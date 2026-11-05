"""Experiment 2: reference window tuned to the first 6 s, then best-of-N.

Step 1  standard @ 0.5/0.5 with ref6_1..4, plus ref_2 re-run at the SAME seed
        so references are compared on an equal random draw.
Step 2  best reference from step 1, generated 4x with different seeds, to
        measure how much of the score is just sampling luck.

One model in memory at a time; resumable.

    venv\\Scripts\\python.exe tools\\voice_experiment2.py
"""

from __future__ import annotations

import gc
import itertools
import json
import random
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
RESULTS_JSON = OUT_DIR / "results2.json"
ORIGINAL = INPUT_DIR / "voice_sample.mp3"

TEXT = (
    "Welcome back to the channel. Today we are looking at five places "
    "you have never heard of."
)

EXAG, CFG = 0.5, 0.5
FAIR_SEED = 1234                      # same draw for every reference
BON_SEEDS = [101, 202, 303, 404]      # best-of-N probe

REFS = {
    "ref6_1": INPUT_DIR / "ref6_1.wav",
    "ref6_2": INPUT_DIR / "ref6_2.wav",
    "ref6_3": INPUT_DIR / "ref6_3.wav",
    "ref6_4": INPUT_DIR / "ref6_4.wav",
    "ref_2": INPUT_DIR / "ref_2.wav",   # previous winner, for comparison
}

NUM_WORDS = {
    "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
    "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine",
    "10": "ten", "11": "eleven", "12": "twelve", "13": "thirteen",
    "14": "fourteen", "15": "fifteen", "16": "sixteen", "17": "seventeen",
    "18": "eighteen", "19": "nineteen", "20": "twenty", "&": "and",
}


def log(msg: str) -> None:
    print(f"[agent] {msg}", flush=True)


def normalise(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9 ]", " ", text.lower())
    return " ".join(NUM_WORDS.get(t, t) for t in cleaned.split())


def load_results() -> dict:
    if RESULTS_JSON.is_file():
        try:
            return json.loads(RESULTS_JSON.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_results(r: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_JSON.write_text(json.dumps(r, indent=2), encoding="utf-8")


def set_seed(seed: int) -> None:
    import torch

    torch.manual_seed(seed)
    random.seed(seed)
    try:
        import numpy as np

        np.random.seed(seed)
    except Exception:
        pass


def generate(jobs: list[tuple[str, Path, int]], results: dict) -> None:
    """jobs = [(name, reference_path, seed)]"""
    todo = [j for j in jobs if not (OUT_DIR / f"{j[0]}.wav").is_file()]
    if not todo:
        log("Sab clips pehle se mojood hain, skip.")
        return

    import torch
    import torchaudio
    from chatterbox.tts import ChatterboxTTS

    log(f"Standard model load ho raha hai ({len(todo)} clips banane hain)...")
    model = ChatterboxTTS.from_pretrained(device="cpu")

    for name, ref, seed in todo:
        log(f"{name} ban raha hai (seed {seed})...")
        set_seed(seed)
        t0 = time.perf_counter()
        with torch.no_grad():
            wav = model.generate(
                TEXT,
                audio_prompt_path=str(ref),
                exaggeration=EXAG,
                cfg_weight=CFG,
            )
        elapsed = time.perf_counter() - t0
        path = OUT_DIR / f"{name}.wav"
        torchaudio.save(str(path), wav, model.sr)
        results[name] = {
            "file": path.name,
            "reference": ref.name,
            "seed": seed,
            "settings": f"standard, exaggeration={EXAG}, cfg_weight={CFG}",
            "gen_seconds": round(elapsed, 1),
            "audio_seconds": round(wav.shape[-1] / model.sr, 2),
        }
        save_results(results)

    del model
    gc.collect()
    log("Standard model memory se hata diya gaya.")


def score(results: dict) -> None:
    todo = [n for n, r in results.items() if "similarity" not in r]
    if not todo:
        return

    from voice_similarity import SpeakerEncoder, cosine_percent

    log("ECAPA load ho raha hai...")
    enc = SpeakerEncoder()
    target = enc.embed_file(ORIGINAL)
    for name in todo:
        try:
            emb = enc.embed_file(OUT_DIR / results[name]["file"])
            results[name]["similarity"] = round(cosine_percent(target, emb), 1)
        except Exception as exc:
            log(f"Warning: {name} score fail -> {exc}")
            results[name]["similarity"] = 0.0
        save_results(results)
    enc.free()
    del enc
    gc.collect()
    log("ECAPA memory se hata diya gaya.")


def check_words(results: dict) -> None:
    expected = normalise(TEXT)
    for r in results.values():
        if "transcript" in r:
            r["words_ok"] = normalise(r["transcript"]) == expected

    todo = [n for n, r in results.items() if "transcript" not in r]
    if not todo:
        save_results(results)
        return

    from faster_whisper import WhisperModel

    log("Whisper load ho raha hai, alfaaz check ho rahe hain...")
    model = WhisperModel("small.en", device="cpu", compute_type="int8")
    for name in todo:
        try:
            segs, _ = model.transcribe(str(OUT_DIR / results[name]["file"]), beam_size=5)
            heard = "".join(s.text for s in segs).strip()
            results[name]["transcript"] = heard
            results[name]["words_ok"] = normalise(heard) == expected
        except Exception as exc:
            log(f"Warning: {name} transcribe fail -> {exc}")
            results[name]["transcript"] = ""
            results[name]["words_ok"] = False
        save_results(results)
    del model
    gc.collect()
    log("Whisper memory se hata diya gaya.")


def expected_best_of(scores: list[float]) -> dict[int, float]:
    """Empirical E[max] when drawing k of these samples without replacement."""
    out = {}
    for k in range(1, len(scores) + 1):
        combos = list(itertools.combinations(scores, k))
        out[k] = sum(max(c) for c in combos) / len(combos)
    return out


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, path in REFS.items():
        if not path.is_file():
            log(f"{path.name} nahi mila - pehle tools/prepare_voice6.py chalayein.")
            return 1

    results = load_results()

    # ---- Step 1: which reference window ---------------------------------
    log("STEP 1: reference window ka muqabla (sab ek hi seed par).")
    step1 = [(f"s1_{k}", p, FAIR_SEED) for k, p in REFS.items()]
    generate(step1, results)
    score(results)

    step1_names = [n for n in results if n.startswith("s1_")]
    best = max(step1_names, key=lambda n: results[n]["similarity"])
    best_ref_key = best[len("s1_"):]
    best_ref = REFS[best_ref_key]
    log(f"Behtareen reference: {best_ref.name} ({results[best]['similarity']}%)")

    # ---- Step 2: best-of-N ----------------------------------------------
    log(f"STEP 2: {best_ref.name} ke sath {len(BON_SEEDS)} alag seeds.")
    step2 = [(f"s2_{best_ref_key}_seed{s}", best_ref, s) for s in BON_SEEDS]
    generate(step2, results)
    score(results)
    check_words(results)
    save_results(results)

    # ---- Report ----------------------------------------------------------
    print()
    print("=" * 92)
    print(f"  STEP 1 - REFERENCE WINDOW  (standard, {EXAG}/{CFG}, seed {FAIR_SEED})")
    print("=" * 92)
    print(f"  {'reference':<12} {'sim%':>7} {'words':>6} {'gen s':>7}")
    print("  " + "-" * 36)
    for n in sorted(step1_names, key=lambda n: results[n]["similarity"], reverse=True):
        r = results[n]
        ok = "OK" if r.get("words_ok") else "BAD"
        marker = "  <-- previous winner" if r["reference"] == "ref_2.wav" else ""
        print(f"  {n[len('s1_'):]:<12} {r['similarity']:7.1f} {ok:>6} "
              f"{r['gen_seconds']:7.1f}{marker}")

    s2_names = [n for n in results if n.startswith("s2_")]
    s2_scores = [results[n]["similarity"] for n in s2_names]

    print()
    print("=" * 92)
    print(f"  STEP 2 - BEST-OF-N on {best_ref.name}")
    print("=" * 92)
    print(f"  {'seed':<8} {'sim%':>7} {'words':>6} {'gen s':>7}")
    print("  " + "-" * 32)
    for n in sorted(s2_names, key=lambda n: results[n]["similarity"], reverse=True):
        r = results[n]
        ok = "OK" if r.get("words_ok") else "BAD"
        print(f"  {r['seed']:<8} {r['similarity']:7.1f} {ok:>6} {r['gen_seconds']:7.1f}")

    if s2_scores:
        lo, hi = min(s2_scores), max(s2_scores)
        avg = sum(s2_scores) / len(s2_scores)
        mean_gen = sum(results[n]["gen_seconds"] for n in s2_names) / len(s2_names)
        print()
        print(f"  min {lo:.1f}%   max {hi:.1f}%   average {avg:.1f}%   spread {hi - lo:.1f} points")
        print()
        print("  Expected score if we generate N takes and keep the best:")
        eb = expected_best_of(s2_scores)
        for k, v in eb.items():
            extra = mean_gen * (k - 1)
            gain = v - eb[1]
            print(f"    best-of-{k}: {v:5.1f}%  (+{gain:4.2f} points, "
                  f"+{extra:5.0f}s per line)")

    # ---- Winner ----------------------------------------------------------
    every = [n for n in results if results[n].get("words_ok")]
    if not every:
        log("Koi bhi clip alfaaz ke test mein pass nahi hua.")
        return 1
    winner = max(every, key=lambda n: results[n]["similarity"])
    shutil.copyfile(OUT_DIR / results[winner]["file"], OUT_DIR / "BEST_NEW.wav")

    print()
    print(f"  BEST_NEW.wav  <-  {winner}  ({results[winner]['similarity']}%)")
    print(f"    reference : {results[winner]['reference']}")
    print(f"    seed      : {results[winner]['seed']}")
    print(f"    settings  : {results[winner]['settings']}")
    print()
    print("  For reference, experiment 1 best was 77.9% (standard, ref_2, random seed)")
    print("  and two real clips of the same speaker score ~90%.")

    bad = [n for n in results if not results[n].get("words_ok")]
    if bad:
        print()
        print("  Rejected for wrong words:")
        for n in bad:
            print(f"    {n}: {results[n].get('transcript', '')!r}")

    print()
    log("Experiment 2 mukammal.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
