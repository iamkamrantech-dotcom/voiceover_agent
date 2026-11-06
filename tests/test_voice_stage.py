"""Exercise the whole voice stage.

  MOCK RUN  real scenes from tests/media/cuts.mp4, real script splitting,
            real caching / best-of-N / retry orchestration, fake audio.
            Runs in seconds because no model is ever loaded.

  REAL RUN  Chatterbox on 2 short blocks at best-of-1, so it stays quick
            while still proving the real engine works through the stage.

    venv\\Scripts\\python.exe tests\\test_voice_stage.py            (both)
    venv\\Scripts\\python.exe tests\\test_voice_stage.py --mock-only
"""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agent import log as agent_log              # noqa: E402
from agent import voice_stage                   # noqa: E402
from agent.config import Config                 # noqa: E402
from agent.scenes import detect_scenes          # noqa: E402
from agent.script_parser import Block, parse_script  # noqa: E402
from agent.voice.base import VerifyResult       # noqa: E402
from agent.voice_stage import run_voice_stage, save_report  # noqa: E402

MEDIA = PROJECT_ROOT / "tests" / "media"

SCRIPT = (
    "Welcome back to the channel and thanks for joining me today. "
    "This first place is somewhere almost nobody has visited. "
    "The second location is stranger still and very hard to reach. "
    "Our third stop sits high above the clouds all year round. "
    "And the final place on the list might surprise you the most. "
    "Let me know in the comments which one you would visit first."
)


class Results:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str, bool]] = []

    def add(self, name: str, expected: str, got: str, ok: bool) -> None:
        self.rows.append((name, expected, got, ok))

    def report(self) -> bool:
        width = max(len(r[0]) for r in self.rows)
        print(f"  {'check':<{width + 2}}{'expected':<26}{'got':<26}result")
        print("  " + "-" * (width + 2 + 26 + 26 + 6))
        for name, expected, got, ok in self.rows:
            print(f"  {name:<{width + 2}}{expected:<26}{got:<26}"
                  f"{'PASS' if ok else 'FAIL'}")
        failed = sum(1 for r in self.rows if not r[3])
        print(f"\n  {len(self.rows) - failed}/{len(self.rows)} checks passed")
        return failed == 0


def mock_run(cfg: Config, log, r: Results) -> None:
    print()
    print("=" * 88)
    print("  MOCK RUN - full stage, no models")
    print("=" * 88)

    cache = cfg.output_dir / "_test_cache" / "mock"
    if cache.exists():
        shutil.rmtree(cache)
    cfg.tts_cache_dir = cache

    video = MEDIA / "cuts.mp4"
    detection = detect_scenes(video, cfg, log=log)
    r.add("scenes detected", "4", str(len(detection.scenes)),
          len(detection.scenes) == 4)

    split = parse_script(SCRIPT, detection.scenes, cfg, log)
    r.add("split method", "time", split.method, split.method == "time")
    r.add("blocks built", "4", str(len(split.blocks)), len(split.blocks) == 4)

    t0 = time.perf_counter()
    results = run_voice_stage(split.blocks, cfg, engine="mock", best_of=3, log=log)
    elapsed = time.perf_counter() - t0

    r.add("mock run is fast", "<20s", f"{elapsed:.1f}s", elapsed < 20)
    r.add("one result per block", str(len(split.blocks)), str(len(results)),
          len(results) == len(split.blocks))

    spoken = [x for x in results if not x.silent]
    all_wavs = all(x.wav and Path(x.wav).is_file() for x in spoken)
    r.add("every wav written", "yes", "yes" if all_wavs else "no", all_wavs)

    have_words = all(len(x.words) > 0 for x in spoken)
    r.add("word timings present", "yes", "yes" if have_words else "no", have_words)

    words_match = all(
        len(x.words) == len(x.text.split()) for x in spoken
    )
    r.add("timing count = word count", "yes", "yes" if words_match else "no",
          words_match)

    durations_ok = all(x.duration > 0.5 for x in spoken)
    r.add("durations sane", ">0.5s each", "yes" if durations_ok else "no",
          durations_ok)

    takes_made = len(list(cache.glob("*.wav")))
    r.add("best-of-3 takes cached", f"{len(spoken) * 3}", str(takes_made),
          takes_made == len(spoken) * 3)

    # --- caching: a second run must not synthesise anything new -----------
    before = {p: p.stat().st_mtime_ns for p in cache.glob("*.wav")}
    t0 = time.perf_counter()
    again = run_voice_stage(split.blocks, cfg, engine="mock", best_of=3, log=log)
    cached_elapsed = time.perf_counter() - t0
    after = {p: p.stat().st_mtime_ns for p in cache.glob("*.wav")}
    untouched = before == after
    r.add("cache reused on re-run", "no files rewritten",
          "reused" if untouched else "rewritten", untouched)
    r.add("same take chosen", "identical wav",
          "yes" if again[0].wav == results[0].wav else "no",
          again[0].wav == results[0].wav)
    r.add("cached run faster", f"<{elapsed:.1f}s", f"{cached_elapsed:.1f}s",
          cached_elapsed <= elapsed)

    # --- PASS 3: retries ---------------------------------------------------
    rounds = {"n": 0}
    original = voice_stage.make_verifier

    def failing_verifier(cfg_, engine_, log=None):
        from agent.voice.mock_synth import MockVerifier

        verifier = MockVerifier(cfg_, log=log)
        real_verify = verifier.verify
        # Capture which verification round THIS instance belongs to, before
        # the counter moves on - otherwise the closure reads the updated
        # value and never fails anything.
        my_round = rounds["n"]
        rounds["n"] += 1

        def verify(wav: Path, expected_text: str) -> VerifyResult:
            result = real_verify(wav, expected_text)
            # Reject everything in the first round, so PASS 3 must retry.
            if my_round < 1:
                return VerifyResult(result.similarity, False, "wrong words", [])
            return result

        verifier.verify = verify  # type: ignore[method-assign]
        return verifier

    voice_stage.make_verifier = failing_verifier
    try:
        one_block = [Block(index=0, text="A short line for the retry test.",
                           scene_ids=[0], start=0.0, end=5.0)]
        retry_cache = cfg.output_dir / "_test_cache" / "retry"
        if retry_cache.exists():
            shutil.rmtree(retry_cache)
        cfg.tts_cache_dir = retry_cache
        retried = run_voice_stage(one_block, cfg, engine="mock", best_of=2, log=log)
        tried = retried[0].takes_tried
        r.add("pass 3 retried", ">2 takes tried", f"{tried} takes", tried > 2)
        r.add("pass 3 recovered", "words_ok", str(retried[0].words_ok),
              retried[0].words_ok)
    finally:
        voice_stage.make_verifier = original
        cfg.tts_cache_dir = cache

    print()
    print(f"  {'#':<4}{'scenes':<10}{'scene s':>9}{'audio s':>9}"
          f"{'over':>8}{'sim':>7}{'words':>7}  text")
    print("  " + "-" * 94)
    for x in results:
        label = ",".join(str(i) for i in x.scene_ids)
        sim = f"{x.similarity:.1f}" if x.similarity is not None else "-"
        print(f"  {x.block_index:<4}{label:<10}{x.scene_duration:9.2f}"
              f"{x.duration:9.2f}{x.overflow:+8.2f}{sim:>7}"
              f"{'OK' if x.words_ok else 'BAD':>7}  {x.text[:36]}")

    save_report(results, cfg.output_dir / "voice_stage" / "mock_report.json")


def real_run(cfg: Config, log, r: Results) -> None:
    print()
    print("=" * 88)
    print("  REAL RUN - Chatterbox, 2 short blocks, best-of-1")
    print("=" * 88)

    cfg.tts_cache_dir = cfg.output_dir / "_test_cache" / "real"
    blocks = [
        Block(index=0, text="Welcome back to the channel.",
              scene_ids=[0], start=0.0, end=5.0),
        # Long enough to clear MIN_SIMILARITY_SECONDS, so the similarity
        # gate is actually exercised rather than skipped as "too short".
        Block(index=1,
              text="Today we are looking at five places you have never "
                   "heard of before.",
              scene_ids=[1], start=5.0, end=9.0),
    ]

    t0 = time.perf_counter()
    results = run_voice_stage(blocks, cfg, engine=cfg.tts_engine,
                              best_of=1, log=log)
    elapsed = time.perf_counter() - t0

    r.add("real: results returned", "2", str(len(results)), len(results) == 2)
    wavs_ok = all(x.wav and Path(x.wav).is_file() for x in results)
    r.add("real: wavs written", "yes", "yes" if wavs_ok else "no", wavs_ok)
    words_ok = all(x.words_ok for x in results)
    r.add("real: words correct", "yes", "yes" if words_ok else "no", words_ok)
    timings_ok = all(len(x.words) > 0 for x in results)
    r.add("real: word timings", "yes", "yes" if timings_ok else "no", timings_ok)
    scored = [x for x in results if x.similarity is not None]
    r.add("real: similarity computed", "all blocks", f"{len(scored)}/{len(results)}",
          len(scored) == len(results))

    # ECAPA is duration-sensitive, so only gate blocks long enough to trust.
    gated = [x for x in scored if x.similarity_reliable]
    if gated:
        sim_ok = all(x.similarity >= cfg.min_similarity for x in gated)
        got = ", ".join(f"{x.similarity:.1f}" for x in gated)
    else:
        sim_ok, got = True, "none long enough"
    r.add("real: similarity >= min", f">={cfg.min_similarity:.0f}% (>=3.5s only)",
          got, sim_ok)

    print()
    print(f"  {'#':<4}{'audio s':>9}{'sim':>8}{'gated':>7}{'words':>7}"
          f"{'seed':>7}  transcript")
    print("  " + "-" * 92)
    for x in results:
        sim = f"{x.similarity:.1f}" if x.similarity is not None else "-"
        print(f"  {x.block_index:<4}{x.duration:9.2f}{sim:>8}"
              f"{'yes' if x.similarity_reliable else 'short':>7}"
              f"{'OK' if x.words_ok else 'BAD':>7}{x.seed:>7}  {x.transcript[:40]}")
    print(f"\n  total time: {elapsed:.1f}s")

    for x in results:
        if x.words:
            print(f"\n  Block {x.block_index} word timings:")
            for w in x.words:
                print(f"      {w.start:6.2f} -> {w.end:6.2f}  {w.word}")

    save_report(results, cfg.output_dir / "voice_stage" / "real_report.json")


def main() -> int:
    cfg = Config.load()
    log = agent_log.setup(cfg.log_dir, cfg.log_level, run_name="test_voice_stage")

    if not (MEDIA / "cuts.mp4").is_file():
        log.user("Test video not found. Run tests/make_test_videos.py first.")
        return 1

    r = Results()
    mock_run(cfg, log, r)

    if "--mock-only" not in sys.argv:
        real_run(cfg, log, r)
    else:
        print("\n  (real run skipped: --mock-only)")

    print()
    print("=" * 88)
    print("  VOICE STAGE TEST")
    print("=" * 88)
    ok = r.report()
    print()
    log.user("All tests passed." if ok else "Some tests failed.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
