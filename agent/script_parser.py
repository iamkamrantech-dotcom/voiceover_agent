"""Split the script into blocks, one block per scene (or per group of scenes).

Three methods, tried in this order:

1. MARKERS     - the script labels its own scenes ("Scene 1:", "[Scene 2]",
                 "Image 3 -"). Trust the writer; use them.
2. PARAGRAPHS  - no markers, but the paragraph count equals the scene count,
                 so one paragraph per scene is clearly intended.
3. TIME        - neither applies. Split into sentences, then use dynamic
                 programming to hand consecutive sentences to scenes so each
                 scene's speaking time matches its screen time as closely as
                 possible. Order never goes backwards.

A scene with no text of its own joins the PREVIOUS block, so the narration
simply continues over it. Leading scenes with no text stay silent, because
there is nothing before them to continue.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.config import Config  # noqa: E402
from agent import log as agent_log  # noqa: E402

# "Scene 1:", "[Scene 2]", "Image 3 -", "(Shot 4)", "Slide 5."
MARKER_RE = re.compile(
    r"^[ \t]*[\[\(]?[ \t]*"
    r"(?:scene|image|shot|slide|pic|picture|frame)"
    r"[ \t]*#?[ \t]*(\d+)[ \t]*"
    r"[\]\)]?[ \t]*[:\-–—.]?[ \t]*",
    re.IGNORECASE | re.MULTILINE,
)

SENTENCE_END_RE = re.compile(r"(?<=[.!?])[\"')\]]*\s+")
COMMA_SPLIT_RE = re.compile(r"(?<=[,;:])\s+")


@dataclass
class Block:
    """One unit of narration, covering one or more scenes."""

    index: int
    text: str
    scene_ids: list[int] = field(default_factory=list)
    start: float = 0.0
    end: float = 0.0
    prev_text: str = ""
    next_text: str = ""

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def word_count(self) -> int:
        return len(self.text.split())

    @property
    def is_silent(self) -> bool:
        return not self.text.strip()

    def estimated_speech_seconds(self, wps: float) -> float:
        return self.word_count / wps if wps > 0 else 0.0


@dataclass
class ScriptSplit:
    method: str
    blocks: list[Block]
    warnings: list[str] = field(default_factory=list)

    @property
    def spoken_blocks(self) -> list[Block]:
        return [b for b in self.blocks if not b.is_silent]


# --------------------------------------------------------------------------
# text helpers (also used by the TTS chunker)
# --------------------------------------------------------------------------

def split_sentences(text: str, max_chars: int = 250) -> list[str]:
    """Sentences, with very long ones broken at commas so no single piece is
    unwieldy. Chatterbox quality drops on long inputs, and the time-based
    split needs reasonably small units to balance well."""
    text = " ".join(text.split())
    if not text:
        return []

    pieces = [p.strip() for p in SENTENCE_END_RE.split(text) if p.strip()]

    out: list[str] = []
    for piece in pieces:
        if len(piece) <= max_chars:
            out.append(piece)
            continue
        # Too long: break at commas, regrouping into <= max_chars parts.
        parts = [p.strip() for p in COMMA_SPLIT_RE.split(piece) if p.strip()]
        buffer = ""
        for part in parts:
            candidate = f"{buffer} {part}".strip()
            if buffer and len(candidate) > max_chars:
                out.append(buffer)
                buffer = part
            else:
                buffer = candidate
        if buffer:
            out.append(buffer)

    # Still too long (no commas at all): hard-wrap on word boundaries.
    final: list[str] = []
    for piece in out:
        while len(piece) > max_chars:
            cut = piece.rfind(" ", 0, max_chars)
            if cut <= 0:
                break
            final.append(piece[:cut].strip())
            piece = piece[cut:].strip()
        if piece:
            final.append(piece)
    return final


def chunk_for_tts(text: str, max_chars: int) -> list[str]:
    """Pieces small enough for Chatterbox to handle well."""
    return split_sentences(text, max_chars=max_chars)


def trailing_punctuation(text: str) -> str:
    stripped = text.rstrip().rstrip("\"')]")
    return stripped[-1] if stripped else ""


# --------------------------------------------------------------------------
# method 1: markers
# --------------------------------------------------------------------------

def _parse_markers(script: str) -> list[tuple[int, str]] | None:
    matches = list(MARKER_RE.finditer(script))
    if len(matches) < 2:
        return None

    out: list[tuple[int, str]] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(script)
        body = script[m.end():end].strip()
        out.append((int(m.group(1)), body))
    return out


# --------------------------------------------------------------------------
# method 3: time-based dynamic programming
# --------------------------------------------------------------------------

def _dp_assign(sentences: list[str], durations: list[float],
               wps: float) -> list[list[int]]:
    """Give consecutive sentences to scenes so speaking time matches screen
    time. Returns, per scene, the list of sentence indices.

    dp[k][i] = cheapest way to cover the first i sentences with k scenes.
    Cost of a group = (how long it takes to say - how long it is on screen)^2.
    """
    n, m = len(sentences), len(durations)
    if m == 0:
        return []
    if n == 0:
        return [[] for _ in range(m)]

    words = [len(s.split()) for s in sentences]
    prefix = [0]
    for w in words:
        prefix.append(prefix[-1] + w)

    # Every scene gets at least one sentence when there are enough to go round.
    min_per = 1 if n >= m else 0

    INF = float("inf")
    dp = [[INF] * (n + 1) for _ in range(m + 1)]
    back = [[-1] * (n + 1) for _ in range(m + 1)]
    dp[0][0] = 0.0

    for k in range(1, m + 1):
        on_screen = durations[k - 1]
        # Leave room for the remaining scenes to meet their minimum.
        for j in range(0, n + 1):
            remaining = m - k
            if j > n - remaining * min_per:
                continue
            best, best_i = INF, -1
            for i in range(0, j - min_per + 1):
                if dp[k - 1][i] == INF:
                    continue
                spoken = (prefix[j] - prefix[i]) / wps if wps > 0 else 0.0
                cost = dp[k - 1][i] + (spoken - on_screen) ** 2
                if cost < best:
                    best, best_i = cost, i
            if best_i >= 0:
                dp[k][j] = best
                back[k][j] = best_i

    if dp[m][n] == INF:  # pathological; fall back to an even split
        groups, per = [], max(1, n // m)
        idx = 0
        for k in range(m):
            take = per if k < m - 1 else n - idx
            groups.append(list(range(idx, idx + max(0, take))))
            idx += max(0, take)
        return groups

    groups: list[list[int]] = []
    j = n
    for k in range(m, 0, -1):
        i = back[k][j]
        groups.append(list(range(i, j)))
        j = i
    groups.reverse()
    return groups


# --------------------------------------------------------------------------
# assembly
# --------------------------------------------------------------------------

def _build_blocks(per_scene_text: list[str], scenes) -> list[Block]:
    """Fold empty scenes into the previous block; keep leading ones silent."""
    blocks: list[Block] = []
    for scene_index, text in enumerate(per_scene_text):
        scene = scenes[scene_index]
        start = getattr(scene, "start", scene["start"] if isinstance(scene, dict) else 0.0)
        end = getattr(scene, "end", scene["end"] if isinstance(scene, dict) else 0.0)

        if text.strip():
            blocks.append(Block(index=len(blocks), text=text.strip(),
                                scene_ids=[scene_index], start=start, end=end))
        elif blocks and not blocks[-1].is_silent:
            # Voice carries on over this scene.
            blocks[-1].scene_ids.append(scene_index)
            blocks[-1].end = end
        elif blocks and blocks[-1].is_silent:
            blocks[-1].scene_ids.append(scene_index)
            blocks[-1].end = end
        else:
            # Nothing before it, so it simply stays quiet.
            blocks.append(Block(index=len(blocks), text="",
                                scene_ids=[scene_index], start=start, end=end))

    for i, block in enumerate(blocks):
        block.index = i
        block.prev_text = blocks[i - 1].text if i > 0 else ""
        block.next_text = blocks[i + 1].text if i + 1 < len(blocks) else ""
    return blocks


def _scene_bounds(scenes) -> list[tuple[float, float]]:
    out = []
    for s in scenes:
        if isinstance(s, dict):
            out.append((float(s["start"]), float(s["end"])))
        else:
            out.append((float(s.start), float(s.end)))
    return out


def parse_script(script: str, scenes, cfg: Config | None = None,
                 log=None) -> ScriptSplit:
    """Split `script` across `scenes` (Scene objects or dicts with start/end)."""
    cfg = cfg or Config.load()
    log = log or agent_log.get()

    bounds = _scene_bounds(scenes)
    n_scenes = len(bounds)
    if n_scenes == 0:
        raise ValueError("No scenes found. Run scene detection first.")

    script = script.strip()
    warnings: list[str] = []
    per_scene: list[str] = ["" for _ in range(n_scenes)]
    method = "time"

    # ---- 1. markers -------------------------------------------------------
    marked = _parse_markers(script)
    if marked:
        method = "markers"
        for number, body in marked:
            target = min(max(number - 1, 0), n_scenes - 1)
            per_scene[target] = (per_scene[target] + " " + body).strip()
        if len(marked) > n_scenes:
            warnings.append(
                f"The script has {len(marked)} markers but the video has only "
                f"{n_scenes} scenes. The extra markers were merged into the last scene."
            )
        log.user(f"Found {len(marked)} script markers, using them.")

    else:
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", script) if p.strip()]

        # ---- 2. one paragraph per scene -----------------------------------
        if len(paragraphs) == n_scenes and n_scenes > 1:
            method = "paragraphs"
            per_scene = paragraphs
            log.user(f"{n_scenes} paragraphs and {n_scenes} scenes, "
                     f"one paragraph per scene.")

        # ---- 3. time-based split ------------------------------------------
        else:
            method = "time"
            sentences = split_sentences(script, max_chars=cfg.tts_max_chunk_chars)
            if not sentences:
                raise ValueError("The script is empty.")
            durations = [end - start for start, end in bounds]
            groups = _dp_assign(sentences, durations, cfg.narration_wps)
            per_scene = [" ".join(sentences[i] for i in group) for group in groups]
            log.user(f"No markers and no matching paragraphs, so splitting by time: "
                     f"{len(sentences)} sentences across {n_scenes} scenes.")
            if len(sentences) < n_scenes:
                warnings.append(
                    f"Only {len(sentences)} sentences for {n_scenes} scenes, so "
                    f"narration will carry over some scenes."
                )

    # attach timings
    blocks = _build_blocks(
        per_scene,
        [type("S", (), {"start": b[0], "end": b[1]})() for b in bounds],
    )

    # ---- overflow warning -------------------------------------------------
    total_words = sum(len(t.split()) for t in per_scene)
    needed = total_words / cfg.narration_wps if cfg.narration_wps > 0 else 0.0
    video_seconds = bounds[-1][1] - bounds[0][0]
    if video_seconds > 0 and needed > video_seconds * cfg.script_overflow_ratio:
        over = needed - video_seconds
        msg = (f"The script is too long: {total_words} words take about "
               f"{needed:.0f}s to speak, but the video is only "
               f"{video_seconds:.0f}s long ({over:.0f}s over). "
               f"Shorten the script or lengthen the video.")
        warnings.append(msg)
        log.warning(msg)

    for w in warnings:
        log.debug("script warning: %s", w)

    return ScriptSplit(method=method, blocks=blocks, warnings=warnings)


def parse_script_file(path: Path | str, scenes, cfg: Config | None = None,
                      log=None) -> ScriptSplit:
    text = Path(path).read_text(encoding="utf-8")
    return parse_script(text, scenes, cfg=cfg, log=log)
