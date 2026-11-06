"""Fit each block's narration into its scenes, the way an editor would.

For every block the voice gets a window: the block's scenes, minus a breath
at the start (LEAD_IN) and a little air at the end (TAIL_OUT, more on the
final block), and always at least BLOCK_GAP of silence before the next
block starts speaking.

If the voice fits, it is simply placed at the start of the window. A short
voice is NEVER stretched slower to fill the gap - silence is fine, and
slowed speech sounds wrong.

If the voice is too long, three fixes in order, cheapest and most natural
first:

  (a) TIGHTEN PAUSES - shorten the silences inside the block, using the word
      timings. Never below 0.18 s after a sentence or 0.10 s after a comma,
      so the delivery keeps its rhythm. Costs nothing, nobody hears it.
  (b) SPEED UP THE VOICE - up to MAX_SPEEDUP (8%), pitch preserved, with
      rubberband if this FFmpeg build has it (better on speech) or atempo.
  (c) EXTEND THE VIDEO - slow the part up to hold_end by at most 15%, then
      freeze on the hold_end frame for whatever is still missing. The
      outgoing transition then plays at normal speed. The freeze is ALWAYS
      on hold_end, which is the last clean frame, never a blended fade
      frame - freezing on a half-dissolved image looks broken.

Word timings are recreated after every change, so subtitles stay exact, and
every later block shifts by the total extension accumulated before it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.config import Config  # noqa: E402
from agent import log as agent_log  # noqa: E402
from agent.scenes import Detection, Scene  # noqa: E402
from agent.voice.base import WordTiming  # noqa: E402
from agent.voice_stage import BlockAudio  # noqa: E402

SENTENCE_ENDS = ".!?"
COMMA_ENDS = ",;:"
EDGE_GUARD = 0.02  # never cut this close to a word, timings are approximate


@dataclass
class BlockPlan:
    block_index: int
    scene_ids: list[int]
    silent: bool

    # source (original video) times
    src_start: float
    src_end: float
    hold_end: float

    # output timeline
    out_start: float
    out_end: float
    voice_start: float
    voice_end: float
    wav: str | None

    # Where the clean part ends: hold_end plus one frame, so hold_end itself
    # is the last frame kept and therefore the frame a freeze clones.
    split_at: float = 0.0
    # Outgoing transition, carried through so the renderer never has to
    # guess whether the tail of this block is a cut or a dissolve.
    out_transition: str = "end"
    out_transition_start: float = 0.0
    out_transition_end: float = 0.0

    # what was done
    pause_saved: float = 0.0
    stretch: float = 1.0
    video_slowdown: float = 1.0
    freeze_seconds: float = 0.0
    extension: float = 0.0
    overflow_before: float = 0.0

    words: list[WordTiming] = field(default_factory=list)

    @property
    def out_duration(self) -> float:
        return self.out_end - self.out_start

    @property
    def src_duration(self) -> float:
        return self.src_end - self.src_start

    @property
    def voice_duration(self) -> float:
        return self.voice_end - self.voice_start

    def to_dict(self) -> dict:
        d = asdict(self)
        d["words"] = [asdict(w) for w in self.words]
        return d


@dataclass
class Timeline:
    source_duration: float
    output_duration: float
    total_extension: float
    fps: float
    blocks: list[BlockPlan] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "source_duration": round(self.source_duration, 3),
            "output_duration": round(self.output_duration, 3),
            "total_extension": round(self.total_extension, 3),
            "fps": self.fps,
            "blocks": [b.to_dict() for b in self.blocks],
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")


# --------------------------------------------------------------------------
# (a) pause tightening
# --------------------------------------------------------------------------

def _pause_floor(word: str, cfg: Config) -> float:
    stripped = word.strip().rstrip("\"')]")
    last = stripped[-1] if stripped else ""
    if last in SENTENCE_ENDS:
        return cfg.pause_floor_sentence
    if last in COMMA_ENDS:
        return cfg.pause_floor_comma
    return cfg.pause_floor_comma


def tighten_pauses(wav_in: Path, words: list[WordTiming], need: float,
                   cfg: Config, wav_out: Path,
                   log) -> tuple[float, list[WordTiming], float]:
    """Remove up to `need` seconds from the silences between words.

    Returns (seconds_saved, new_word_timings, new_duration).
    """
    import soundfile as sf

    audio, sr = sf.read(str(wav_in), dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    duration = len(audio) / sr

    if need <= 0 or len(words) < 2:
        return 0.0, list(words), duration

    # Every gap that is long enough to be worth trimming.
    cuts: list[tuple[int, float, float, float]] = []  # i, gap_start, gap_end, floor
    for i in range(len(words) - 1):
        gap_start = words[i].end
        gap_end = words[i + 1].start
        gap = gap_end - gap_start
        if gap <= cfg.pause_tighten_above:
            continue
        floor = _pause_floor(words[i].word, cfg)
        usable = gap - floor - 2 * EDGE_GUARD
        if usable > 0.005:
            cuts.append((i, gap_start, gap_end, floor))

    if not cuts:
        return 0.0, list(words), duration

    available = sum((ge - gs) - fl - 2 * EDGE_GUARD for _, gs, ge, fl in cuts)
    take = min(need, available)
    ratio = take / available if available > 0 else 0.0
    log.debug("pause tighten: need %.3f, available %.3f, taking %.3f",
              need, available, take)

    # Build the shortened audio and the shifted timings together.
    pieces: list[np.ndarray] = []
    new_words: list[WordTiming] = []
    cursor_s = 0.0
    removed_total = 0.0
    cut_map = {c[0]: c for c in cuts}

    for i, word in enumerate(words):
        new_words.append(WordTiming(word.word,
                                    word.start - removed_total,
                                    word.end - removed_total))
        if i in cut_map:
            _, gap_start, gap_end, floor = cut_map[i]
            gap = gap_end - gap_start
            remove = min((gap - floor - 2 * EDGE_GUARD) * ratio, gap - floor)
            keep_until = gap_start + EDGE_GUARD + (gap - remove - 2 * EDGE_GUARD)
            pieces.append(audio[int(cursor_s * sr):int(keep_until * sr)])
            cursor_s = gap_end - EDGE_GUARD
            removed_total += remove

    pieces.append(audio[int(cursor_s * sr):])
    joined = np.concatenate([p for p in pieces if p.size]) if pieces else audio

    wav_out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(wav_out), joined, sr, subtype="PCM_16")
    new_duration = len(joined) / sr
    return removed_total, new_words, new_duration


# --------------------------------------------------------------------------
# (b) voice speed-up
# --------------------------------------------------------------------------

_stretch_filter_cache: dict[str, str] = {}


def pick_stretch_filter(cfg: Config, log) -> str:
    """rubberband sounds better on speech; fall back to atempo."""
    choice = cfg.audio_stretch_filter
    if choice in ("rubberband", "atempo"):
        return choice
    if "value" in _stretch_filter_cache:
        return _stretch_filter_cache["value"]

    found = "atempo"
    try:
        proc = subprocess.run([cfg.ffmpeg_bin, "-hide_banner", "-filters"],
                              capture_output=True, text=True)
        if " rubberband " in proc.stdout:
            found = "rubberband"
    except Exception as exc:
        log.debug("filter probe failed: %s", exc)
    _stretch_filter_cache["value"] = found
    log.debug("audio stretch filter: %s", found)
    return found


def stretch_audio(wav_in: Path, factor: float, cfg: Config, wav_out: Path,
                  log) -> float:
    """Speed speech up by `factor` (1.08 = 8% faster) keeping pitch."""
    name = pick_stretch_filter(cfg, log)
    if name == "rubberband":
        audio_filter = f"rubberband=tempo={factor:.6f}:pitchq=quality"
    else:
        audio_filter = f"atempo={factor:.6f}"

    wav_out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [cfg.ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
           "-i", str(wav_in), "-filter:a", audio_filter, str(wav_out)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        log.warning(f"stretch fail ({name}): {proc.stderr.strip()[:200]}")
        shutil.copyfile(wav_in, wav_out)
        return 1.0

    import soundfile as sf

    info = sf.info(str(wav_out))
    return info.frames / info.samplerate


# --------------------------------------------------------------------------
# planning
# --------------------------------------------------------------------------

def _scene_by_id(detection: Detection) -> dict[int, Scene]:
    return {s.index: s for s in detection.scenes}


def plan_timeline(detection: Detection, audio_blocks: list[BlockAudio],
                  cfg: Config | None = None, work_dir: Path | None = None,
                  log=None) -> Timeline:
    cfg = cfg or Config.load()
    log = log or agent_log.get()
    work = Path(work_dir or (cfg.work_dir / "align"))
    work.mkdir(parents=True, exist_ok=True)

    scenes = _scene_by_id(detection)
    fps = detection.fps
    frame = 1.0 / fps if fps else 0.04

    plans: list[BlockPlan] = []
    shift = 0.0  # everything after an extension moves later by this much

    log.user(f"Planning timeline for {len(audio_blocks)} blocks.")

    for position, block in enumerate(audio_blocks):
        is_final = position == len(audio_blocks) - 1
        src_start = block.scene_start
        src_end = block.scene_end

        last_scene = scenes.get(block.scene_ids[-1]) if block.scene_ids else None
        hold_end = last_scene.hold_end if last_scene else src_end
        hold_end = max(src_start, min(hold_end, src_end))
        split_at = min(src_end, hold_end + frame)

        transition = last_scene.transition_out if last_scene else None
        t_kind = transition.kind if transition else "end"
        t_start = transition.start if transition else src_end
        t_end = transition.end if transition else src_end

        out_start = src_start + shift

        if block.silent or not block.wav:
            plans.append(BlockPlan(
                block_index=block.block_index, scene_ids=list(block.scene_ids),
                silent=True, src_start=src_start, src_end=src_end,
                hold_end=hold_end, split_at=split_at,
                out_transition=t_kind, out_transition_start=t_start,
                out_transition_end=t_end, out_start=out_start,
                out_end=out_start + (src_end - src_start),
                voice_start=out_start, voice_end=out_start, wav=None,
            ))
            continue

        tail = cfg.tail_out_final if is_final else max(cfg.tail_out,
                                                       cfg.block_gap - cfg.lead_in)
        available = (src_end - src_start) - cfg.lead_in - tail
        voice_duration = block.duration
        words = list(block.words)
        wav = Path(block.wav)

        pause_saved = 0.0
        stretch = 1.0
        slowdown = 1.0
        freeze = 0.0
        overflow_before = max(0.0, voice_duration - available)

        if overflow_before > 1e-6:
            log.user(f"Block {block.block_index + 1}: narration is "
                     f"{overflow_before:.2f}s too long, adjusting...")

            # ---- (a) tighten pauses --------------------------------------
            need = voice_duration - available
            tightened = work / f"block{block.block_index:03d}_tight.wav"
            pause_saved, words, voice_duration = tighten_pauses(
                wav, words, need, cfg, tightened, log
            )
            if pause_saved > 1e-4:
                wav = tightened
                log.user(f"  Tightened pauses, saved {pause_saved:.2f}s.")

            # ---- (b) speed the voice up ----------------------------------
            if voice_duration > available + 1e-6:
                wanted = voice_duration / available
                stretch = min(cfg.max_speedup, wanted)
                if stretch > 1.001:
                    stretched = work / f"block{block.block_index:03d}_fast.wav"
                    voice_duration = stretch_audio(wav, stretch, cfg,
                                                   stretched, log)
                    wav = stretched
                    words = [WordTiming(w.word, w.start / stretch, w.end / stretch)
                             for w in words]
                    log.user(f"  Sped narration up by {((stretch - 1) * 100):.0f}%.")
                else:
                    stretch = 1.0

            # ---- (c) extend the video ------------------------------------
            if voice_duration > available + 1e-6:
                need_extra = voice_duration - available
                # The part that actually gets slowed is what the renderer
                # trims: src_start -> split_at (hold_end inclusive).
                clean_part = max(0.0, split_at - src_start)
                gain = clean_part * (cfg.max_video_slowdown - 1.0)
                use_slow = min(need_extra, gain)
                if clean_part > 1e-6 and use_slow > 1e-6:
                    slowdown = (clean_part + use_slow) / clean_part
                freeze = max(0.0, need_extra - use_slow)
                # keep the freeze on a whole number of frames
                if freeze > 0:
                    freeze = round(freeze / frame) * frame
                log.user(f"  Extended video: {use_slow:.2f}s slowdown + "
                         f"{freeze:.2f}s freeze on hold_end.")

        # Extension = the time the slowed section gained, plus the freeze.
        clean_part = max(0.0, split_at - src_start)
        slow_gain = clean_part * (slowdown - 1.0) if slowdown > 1.0 else 0.0
        extension = slow_gain + freeze

        out_end = out_start + (src_end - src_start) + extension
        voice_start = out_start + cfg.lead_in
        voice_end = voice_start + voice_duration

        plans.append(BlockPlan(
            block_index=block.block_index, scene_ids=list(block.scene_ids),
            silent=False, src_start=src_start, src_end=src_end,
            hold_end=hold_end, split_at=split_at, out_transition=t_kind,
            out_transition_start=t_start, out_transition_end=t_end,
            out_start=out_start, out_end=out_end,
            voice_start=voice_start, voice_end=voice_end, wav=str(wav),
            pause_saved=round(pause_saved, 3), stretch=round(stretch, 4),
            video_slowdown=round(slowdown, 4), freeze_seconds=round(freeze, 3),
            extension=round(extension, 3),
            overflow_before=round(overflow_before, 3),
            words=[WordTiming(w.word, round(voice_start + w.start, 3),
                              round(voice_start + w.end, 3)) for w in words],
        ))
        shift += extension

    source_duration = detection.duration
    timeline = Timeline(
        source_duration=source_duration,
        output_duration=source_duration + shift,
        total_extension=shift,
        fps=fps,
        blocks=plans,
    )

    log.user(f"Timeline ready: {source_duration:.2f}s -> "
             f"{timeline.output_duration:.2f}s "
             f"(+{shift:.2f}s added).")
    return timeline


def check_timeline(timeline: Timeline, cfg: Config) -> list[str]:
    """Problems an editor would notice. Empty list means clean."""
    problems: list[str] = []
    blocks = timeline.blocks

    for i, b in enumerate(blocks):
        if b.silent:
            continue
        if b.voice_end > b.out_end + 1e-3:
            problems.append(
                f"block {b.block_index}: voice ends {b.voice_end:.3f} "
                f"past segment end {b.out_end:.3f}"
            )
        later = next((x for x in blocks[i + 1:] if not x.silent), None)
        if later is not None:
            gap = later.voice_start - b.voice_end
            if gap < cfg.block_gap - 1e-3:
                problems.append(
                    f"block {b.block_index}: only {gap:.3f}s before block "
                    f"{later.block_index} speaks (need {cfg.block_gap:.2f}s)"
                )
        if b.words:
            if b.words[0].start < b.voice_start - 1e-3:
                problems.append(f"block {b.block_index}: first word before voice start")
            if b.words[-1].end > b.voice_end + 0.05:
                problems.append(
                    f"block {b.block_index}: last word ends {b.words[-1].end:.3f} "
                    f"after voice end {b.voice_end:.3f}"
                )
    return problems
