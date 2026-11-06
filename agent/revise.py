"""Change one block of a finished job and rebuild the video.

A job is hours of CPU time on this machine. Before this module existed, one
bad block out of forty meant running the whole thing again - so in practice
nobody fixed anything. The pipeline already made the fix cheap: the TTS
cache is keyed on text + reference + settings + seed, so re-running touches
only what actually changed. What was missing was a way in.

Three changes are possible, in rising order of cost:

    choose_take   use a take that was already generated and scored.
                  Nothing is synthesised. No model is loaded at all.
    more_takes    generate N more takes for ONE block, on fresh seeds.
    edit_text     new words for ONE block, so new cache keys for it alone.

After the change the job re-enters pipeline.finish_job - the same function
the original render used - and the result is recorded as a new version.
v1 is never overwritten: comparing the new take against the old one by ear
is the whole reason for making the change.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent import log as agent_log  # noqa: E402
from agent import versions  # noqa: E402
from agent.config import Config  # noqa: E402
from agent.pipeline import finish_job  # noqa: E402
from agent.scenes import Detection, detect_scenes  # noqa: E402
from agent.script_parser import Block, ScriptSplit  # noqa: E402
from agent.voice_stage import (  # noqa: E402
    BlockAudio, assemble_block, save_report, take_from_record, takes_for_block,
)
from agent.voice.base import WordTiming  # noqa: E402

CHOOSE_TAKE = "choose_take"
MORE_TAKES = "more_takes"
EDIT_TEXT = "edit_text"
ACTIONS = (CHOOSE_TAKE, MORE_TAKES, EDIT_TEXT)

# Takes asked for from the review panel get their own seed band, so they can
# never collide with the base takes (seed + i) or the retry rounds
# (seed + 1000 * round + i) and silently reuse another take's audio.
REVIEW_SEED_BASE = 10_000


class ReviseError(Exception):
    """Something about the request cannot be honoured. The message is shown
    to the user, so it says what to do about it."""


@dataclass
class BlockChange:
    block_index: int
    action: str
    seed: int | None = None        # choose_take
    extra: int = 2                 # more_takes
    text: str | None = None        # edit_text

    def to_dict(self) -> dict:
        return {"block": self.block_index, "action": self.action,
                "seed": self.seed, "extra": self.extra, "text": self.text}

    @classmethod
    def from_dict(cls, data: dict) -> "BlockChange":
        action = str(data.get("action") or "")
        if action not in ACTIONS:
            raise ReviseError(f"'{action}' is not something that can be changed.")
        try:
            index = int(data.get("block", data.get("block_index")))
        except (TypeError, ValueError):
            raise ReviseError("Which block to change was not given.")
        seed = data.get("seed")
        return cls(
            block_index=index,
            action=action,
            seed=int(seed) if seed is not None else None,
            extra=max(1, min(8, int(data.get("extra") or 2))),
            text=data.get("text"),
        )


@dataclass
class RevisionPlan:
    """What a revision will actually do, shown before it starts.

    Generating narration is the expensive part, so the honest thing to show
    is whether any will be generated at all.
    """
    block_index: int
    action: str
    note: str
    takes_to_make: int = 0
    words: int = 0
    estimate_seconds: float = 0.0
    steps: list[str] = field(default_factory=list)
    version: int = 2

    def to_dict(self) -> dict:
        return {
            "block": self.block_index, "action": self.action,
            "note": self.note, "takes_to_make": self.takes_to_make,
            "words": self.words, "estimate_seconds": self.estimate_seconds,
            "steps": self.steps, "version": self.version,
        }


# --------------------------------------------------------------------------
# loading a finished job back into memory
# --------------------------------------------------------------------------

def load_blocks(job_dir: Path) -> list[BlockAudio]:
    """The per-block record a finished job left behind."""
    import json

    path = Path(job_dir) / "voice_stage.json"
    if not path.is_file():
        raise ReviseError(
            "This job has no narration record, so single blocks cannot be "
            "changed. Run it again to be able to revise it.")
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: list[BlockAudio] = []
    for item in raw:
        item = dict(item)
        item["wav"] = Path(item["wav"]) if item.get("wav") else None
        item["words"] = [WordTiming(**w) for w in item.get("words", [])]
        item.setdefault("takes", [])
        out.append(BlockAudio(**item))
    return out


def load_detection(job_dir: Path, video: Path, cfg: Config, log) -> Detection:
    """Scenes, from the job's own record when it has one.

    Jobs made before 8B did not save it. Detection is deterministic for the
    same video and settings, so it can be redone - it just costs a pass over
    every frame, which is why it is saved now.
    """
    saved = Path(job_dir) / "scenes.json"
    if saved.is_file():
        try:
            return Detection.load(saved)
        except Exception as exc:
            log.debug("scenes.json could not be read (%s); detecting again", exc)
    log.user("This job predates saved scene data, so scenes are being "
             "detected again. This happens once.")
    detection = detect_scenes(video, cfg,
                              keyframe_dir=Path(job_dir) / "keyframes", log=log)
    detection.save(saved)
    return detection


def load_split(job_dir: Path, blocks: list[BlockAudio]) -> ScriptSplit:
    """The script division that was used.

    Rebuilt from the narration record for older jobs - it carries each
    block's text, scenes and time window, which is everything a Block needs.
    """
    saved = Path(job_dir) / "split.json"
    if saved.is_file():
        try:
            return ScriptSplit.load(saved)
        except Exception:
            pass

    rebuilt = [
        Block(index=b.block_index, text=b.text, scene_ids=list(b.scene_ids),
              start=b.scene_start, end=b.scene_end)
        for b in blocks
    ]
    for i, block in enumerate(rebuilt):
        block.prev_text = rebuilt[i - 1].text if i else ""
        block.next_text = rebuilt[i + 1].text if i + 1 < len(rebuilt) else ""
    return ScriptSplit(method="rebuilt", blocks=rebuilt)


def _block_or_fail(blocks: list[BlockAudio], index: int) -> BlockAudio:
    found = next((b for b in blocks if b.block_index == index), None)
    if found is None:
        raise ReviseError(f"Block {index + 1} is not part of this job.")
    if found.silent:
        raise ReviseError(
            f"Block {index + 1} has no narration of its own, so there is "
            f"nothing to change. Edit the block it continues from instead.")
    return found


# --------------------------------------------------------------------------
# planning
# --------------------------------------------------------------------------

REBUILD_STEPS = ["Planning the timeline", "Writing subtitles",
                 "Mastering audio", "Rendering the video", "Running checks"]


def plan_revision(job_dir: Path, change: BlockChange, cfg: Config,
                  speed=None, engine: str | None = None) -> RevisionPlan:
    """What will be redone, and roughly how long it will take.

    Deliberately shown before anything starts: on this machine a surprise
    can cost an hour.
    """
    from agent.jobs import MODEL_LOAD_SECONDS

    job_dir = Path(job_dir)
    blocks = load_blocks(job_dir)
    block = _block_or_fail(blocks, change.block_index)
    engine = (engine or cfg.tts_engine).lower()

    takes_to_make = 0
    words = 0
    if change.action == CHOOSE_TAKE:
        if change.seed is None:
            raise ReviseError("No take was chosen.")
        if not any(t.get("seed") == change.seed for t in block.takes):
            raise ReviseError("That take is not one of this block's takes.")
        note = f"Block {change.block_index + 1}: a different take chosen"
    elif change.action == MORE_TAKES:
        takes_to_make = change.extra
        words = len(block.text.split())
        note = (f"Block {change.block_index + 1}: "
                f"{change.extra} more take{'s' if change.extra != 1 else ''}")
    else:
        text = (change.text or "").strip()
        if not text:
            raise ReviseError(
                "The block cannot be left empty. Delete the job, or move the "
                "words into a neighbouring block instead.")
        if text == block.text.strip():
            raise ReviseError("The text has not changed.")
        takes_to_make = cfg.best_of_n()
        words = len(text.split())
        note = f"Block {change.block_index + 1}: text edited"

    seconds = 0.0
    if takes_to_make:
        per_word = (speed.seconds_per_word(engine) if speed is not None
                    else {"standard": 5.5, "turbo": 1.6, "mock": 0.01}
                    .get(engine, 3.0))
        seconds += words * takes_to_make * per_word
        seconds += MODEL_LOAD_SECONDS.get(engine, 40.0)

    # The rebuild itself. Master and render both cover the whole programme,
    # and QC transcribes the finished file, so this part does not shrink just
    # because only one block changed.
    duration = max((b.scene_end for b in blocks), default=0.0)
    seconds += 10.0 + duration * 1.4

    steps = ([f"Generating {takes_to_make} take"
              f"{'s' if takes_to_make != 1 else ''} for block "
              f"{change.block_index + 1}", "Checking the new takes"]
             if takes_to_make else [])
    steps += REBUILD_STEPS

    return RevisionPlan(
        block_index=change.block_index, action=change.action, note=note,
        takes_to_make=takes_to_make, words=words,
        estimate_seconds=round(seconds, 1), steps=steps,
        version=versions.next_number(job_dir),
    )


# --------------------------------------------------------------------------
# doing it
# --------------------------------------------------------------------------

def _apply(change: BlockChange, block_audio: BlockAudio, block: Block,
           cfg: Config, engine: str, log, progress=None, cancel=None) -> BlockAudio:
    """Produce the new BlockAudio for the one block that is changing."""
    existing = [take_from_record(t) for t in block_audio.takes]

    if change.action == CHOOSE_TAKE:
        chosen = next((t for t in existing if t.seed == change.seed), None)
        if chosen is None:
            raise ReviseError("That take is not one of this block's takes.")
        if not Path(chosen.path).is_file():
            raise ReviseError(
                "That take's audio is no longer on disk. Generate more takes "
                "for this block instead.")
        log.user(f"Block {change.block_index + 1}: using the take on seed "
                 f"{chosen.seed}.")
        return assemble_block(block, existing, cfg, log, chosen=chosen)

    if change.action == MORE_TAKES:
        used = {t.seed for t in existing}
        round_number = 1
        while any(REVIEW_SEED_BASE * round_number + i in used
                  for i in range(change.extra)):
            round_number += 1
        base = REVIEW_SEED_BASE * round_number
        seeds = [base + i for i in range(change.extra)]
        log.user(f"Block {change.block_index + 1}: generating "
                 f"{change.extra} more take{'s' if change.extra != 1 else ''}.")
        fresh = takes_for_block(block, seeds, cfg, engine, log,
                                progress=progress, cancel=cancel)
        return assemble_block(block, existing + fresh, cfg, log)

    # edit_text: new words mean new cache keys, so nothing of this block's
    # old audio applies - and the old takes belong to text that is gone.
    block.text = (change.text or "").strip()
    seeds = [(cfg.tts_seed or 0) + i for i in range(cfg.best_of_n())]
    log.user(f"Block {change.block_index + 1}: generating narration for the "
             f"new text.")
    fresh = takes_for_block(block, seeds, cfg, engine, log,
                            progress=progress, cancel=cancel)
    return assemble_block(block, fresh, cfg, log)


def run_revision(job_dir: Path, change: BlockChange, cfg: Config, log=None,
                 video: Path | None = None, music: Path | None = None,
                 keep_original_audio: bool = False, burn_subs: bool = False,
                 engine: str | None = None, progress=None, cancel=None):
    """Apply one block change and rebuild the video as a new version."""
    log = log or agent_log.get()
    job_dir = Path(job_dir)
    engine = (engine or cfg.tts_engine).lower()

    blocks = load_blocks(job_dir)
    target = _block_or_fail(blocks, change.block_index)

    if video is None:
        raise ReviseError("The job's source video is not recorded.")
    video = Path(video)
    if not video.is_file():
        raise ReviseError(
            f"The source video is missing: {video.name}. It was moved or "
            f"deleted after the job ran.")

    if progress:
        progress(stage="voice", detail=f"Changing block {change.block_index + 1}...")

    detection = load_detection(job_dir, video, cfg, log)
    split = load_split(job_dir, blocks)
    block = next((b for b in split.blocks if b.index == change.block_index), None)
    if block is None:
        raise ReviseError(f"Block {change.block_index + 1} is not in the script.")

    revised = _apply(change, target, block, cfg, engine, log,
                     progress=progress, cancel=cancel)
    blocks = [revised if b.block_index == change.block_index else b
              for b in blocks]

    # The script record must agree with the audio, or the next revision
    # would start from text that was never spoken.
    if change.action == EDIT_TEXT:
        for i, b in enumerate(split.blocks):
            if b.index == change.block_index:
                b.text = revised.text
            b.prev_text = split.blocks[i - 1].text if i else ""
            b.next_text = (split.blocks[i + 1].text
                           if i + 1 < len(split.blocks) else "")
        split.save(job_dir / "split.json")

    save_report(blocks, job_dir / "voice_stage.json")

    version = versions.next_number(job_dir)
    note = _note_for(change, revised)
    log.user(f"Rebuilding the video as version {version}.")

    return finish_job(
        video, detection, blocks, cfg, log, job_dir=job_dir, music=music,
        keep_original_audio=keep_original_audio, burn_subs=burn_subs,
        engine=engine, version=version, note=note,
        changes=[change.to_dict()], progress=progress, cancel=cancel,
    )


def _note_for(change: BlockChange, revised: BlockAudio) -> str:
    n = change.block_index + 1
    if change.action == CHOOSE_TAKE:
        return (f"Block {n}: take {revised.take_number} of "
                f"{len(revised.takes)} chosen")
    if change.action == MORE_TAKES:
        return (f"Block {n}: {change.extra} more take"
                f"{'s' if change.extra != 1 else ''}, "
                f"take {revised.take_number} of {len(revised.takes)} chosen")
    return f"Block {n}: text edited"
