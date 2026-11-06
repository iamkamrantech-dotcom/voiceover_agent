"""Check all three script-splitting methods pick themselves correctly and
produce sane blocks.

    venv\\Scripts\\python.exe tests\\test_script_parser.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agent import log as agent_log          # noqa: E402
from agent.config import Config             # noqa: E402
from agent.script_parser import parse_script, split_sentences  # noqa: E402


def fake_scenes(durations: list[float]):
    scenes, t = [], 0.0
    for d in durations:
        scenes.append({"start": t, "end": t + d})
        t += d
    return scenes


MARKER_SCRIPT = """
Scene 1: Welcome back to the channel, this is where it begins.

[Scene 2] The second picture shows something different.

Image 3 - And here is the third and final shot.
"""

PARAGRAPH_SCRIPT = """First paragraph for the opening image.

Second paragraph while the middle image is showing.

Third paragraph as the last image appears.
"""

TIME_SCRIPT = (
    "The first sentence sets everything up for the viewer. "
    "A second sentence keeps the momentum going nicely. "
    "Then a third one arrives with more detail. "
    "A fourth sentence adds yet more useful context here. "
    "The fifth sentence begins to wrap things up. "
    "And a sixth sentence finally closes the video out."
)

LONG_SCRIPT = " ".join(
    [f"This is filler sentence number {i} with quite a few words in it."
     for i in range(1, 61)]
)


class Results:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str, bool]] = []

    def add(self, name: str, expected: str, got: str, ok: bool) -> None:
        self.rows.append((name, expected, got, ok))

    def report(self) -> bool:
        width = max(len(r[0]) for r in self.rows)
        print(f"  {'check':<{width + 2}}{'expected':<28}{'got':<28}result")
        print("  " + "-" * (width + 2 + 28 + 28 + 6))
        for name, expected, got, ok in self.rows:
            print(f"  {name:<{width + 2}}{expected:<28}{got:<28}"
                  f"{'PASS' if ok else 'FAIL'}")
        failed = sum(1 for r in self.rows if not r[3])
        print(f"\n  {len(self.rows) - failed}/{len(self.rows)} checks passed")
        return failed == 0


def main() -> int:
    cfg = Config.load()
    log = agent_log.setup(cfg.log_dir, cfg.log_level, run_name="test_script")
    r = Results()

    # ---- 1. markers ------------------------------------------------------
    scenes = fake_scenes([5, 4, 6])
    split = parse_script(MARKER_SCRIPT, scenes, cfg, log)
    r.add("markers: method", "markers", split.method, split.method == "markers")
    r.add("markers: block count", "3", str(len(split.blocks)),
          len(split.blocks) == 3)
    first_ok = split.blocks[0].text.startswith("Welcome back")
    r.add("markers: marker stripped", "no 'Scene 1:' prefix",
          split.blocks[0].text[:18] + "...", first_ok)
    r.add("markers: 3rd block", "third and final",
          "found" if "third and final" in split.blocks[2].text else "missing",
          "third and final" in split.blocks[2].text)

    # ---- 2. paragraphs ---------------------------------------------------
    scenes = fake_scenes([5, 5, 5])
    split = parse_script(PARAGRAPH_SCRIPT, scenes, cfg, log)
    r.add("paragraphs: method", "paragraphs", split.method,
          split.method == "paragraphs")
    r.add("paragraphs: block count", "3", str(len(split.blocks)),
          len(split.blocks) == 3)
    r.add("paragraphs: order kept", "Second in block 2",
          "yes" if split.blocks[1].text.startswith("Second") else "no",
          split.blocks[1].text.startswith("Second"))

    # ---- 3. time-based ---------------------------------------------------
    scenes = fake_scenes([10, 4, 4, 10])
    split = parse_script(TIME_SCRIPT, scenes, cfg, log)
    r.add("time: method", "time", split.method, split.method == "time")
    r.add("time: block count", "4", str(len(split.blocks)),
          len(split.blocks) == 4)

    # every scene should get text when sentences >= scenes
    all_have_text = all(not b.is_silent for b in split.blocks)
    r.add("time: every scene has text", "yes",
          "yes" if all_have_text else "no", all_have_text)

    # longer scenes should receive at least as many words as shorter ones
    words = [b.word_count for b in split.blocks]
    proportional = words[0] >= words[1] and words[3] >= words[2]
    r.add("time: long scenes get more", "w0>=w1 and w3>=w2",
          f"{words}", proportional)

    # order must never go backwards
    text_positions = [TIME_SCRIPT.find(b.text.split(".")[0][:25])
                      for b in split.blocks]
    monotonic = all(a <= b for a, b in zip(text_positions, text_positions[1:]))
    r.add("time: order preserved", "non-decreasing",
          "yes" if monotonic else "no", monotonic)

    # ---- 4. fewer sentences than scenes ----------------------------------
    scenes = fake_scenes([3, 3, 3, 3, 3, 3])
    split = parse_script("Only one sentence here. And a second one.",
                         scenes, cfg, log)
    merged = len(split.blocks) < 6
    r.add("few sentences: scenes merged", "<6 blocks", str(len(split.blocks)),
          merged)
    covered = sum(len(b.scene_ids) for b in split.blocks)
    r.add("few sentences: all scenes covered", "6", str(covered), covered == 6)

    # ---- 5. overflow warning ---------------------------------------------
    scenes = fake_scenes([5, 5])
    split = parse_script(LONG_SCRIPT, scenes, cfg, log)
    warned = any("too long" in w for w in split.warnings)
    r.add("overflow warning", "warns", "warned" if warned else "silent", warned)

    # ---- 6. sentence splitter -------------------------------------------
    long_one = "word " * 120
    pieces = split_sentences(long_one.strip(), max_chars=250)
    short_enough = all(len(p) <= 250 for p in pieces)
    r.add("splitter: respects max chars", "all <=250",
          f"max {max(len(p) for p in pieces)}", short_enough)

    # ---- 7. prev/next text ----------------------------------------------
    scenes = fake_scenes([5, 4, 6])
    split = parse_script(MARKER_SCRIPT, scenes, cfg, log)
    links_ok = (
        split.blocks[0].prev_text == ""
        and split.blocks[1].prev_text == split.blocks[0].text
        and split.blocks[1].next_text == split.blocks[2].text
        and split.blocks[2].next_text == ""
    )
    r.add("neighbour text wired", "prev/next set",
          "yes" if links_ok else "no", links_ok)

    print()
    print("=" * 94)
    print("  SCRIPT PARSER TEST")
    print("=" * 94)
    ok = r.report()
    print()
    log.user("All tests passed." if ok else "Some tests failed.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
