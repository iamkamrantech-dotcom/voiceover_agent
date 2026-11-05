"""Produce the final narration audio for every block, memory-safe.

Three passes, because only one model may be resident at a time on this
machine:

  PASS 1  load the synthesizer, generate N takes for every block
          (seed = TTS_SEED + take), cache each one, free the synthesizer.
  PASS 2  load ECAPA + faster-whisper, score every take, keep the best take
          whose WORDS ARE CORRECT, take its word timings, free the models.
  PASS 3  any block with no correct take is regenerated with fresh seeds,
          up to TTS_RETRY_ROUNDS extra rounds, then scored again.

Caching is keyed on text + reference + engine + settings + seed, so a
re-run never re-synthesises anything that has not changed.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.config import Config  # noqa: E402
from agent import log as agent_log  # noqa: E402
from agent.script_parser import Block  # noqa: E402
from agent.voice.base import (  # noqa: E402
    WordTiming, make_converter, make_synthesizer, make_verifier,
)


class JobCancelled(Exception):
    """Raised when a running job is cancelled, so the worker unwinds and
    frees its model instead of being killed mid-generation."""


def _check(cancel) -> None:
    if cancel is not None and cancel():
        raise JobCancelled()


@dataclass
class Take:
    seed: int
    path: Path
    duration: float
    similarity: float | None = None
    words_ok: bool = False
    transcript: str = ""
    words: list[WordTiming] = field(default_factory=list)


@dataclass
class BlockAudio:
    block_index: int
    text: str
    scene_ids: list[int]
    scene_start: float
    scene_end: float
    wav: Path | None
    duration: float
    similarity: float | None
    words_ok: bool
    transcript: str
    words: list[WordTiming]
    seed: int | None
    take_number: int
    takes_tried: int
    silent: bool = False
    # ECAPA is duration-sensitive: the same clip scores 50% at 1.0s and 85%
    # at 5.0s. On a short block the number is noise, so it is reported but
    # never compared against MIN_SIMILARITY.
    similarity_reliable: bool = True

    @property
    def scene_duration(self) -> float:
        return self.scene_end - self.scene_start

    @property
    def overflow(self) -> float:
        """Seconds by which the narration overruns its scenes."""
        return self.duration - self.scene_duration

    def to_dict(self) -> dict:
        data = asdict(self)
        data["wav"] = str(self.wav) if self.wav else None
        data["words"] = [asdict(w) for w in self.words]
        return data


def _cache_key(cfg: Config, text: str, seed: int, engine: str) -> str:
    parts = [
        text.strip(), engine, str(cfg.voice_sample),
        f"{cfg.tts_exaggeration}", f"{cfg.tts_cfg_weight}",
        f"{cfg.tts_temperature}", f"{cfg.tts_max_chunk_chars}",
        f"{cfg.tts_pause_sentence}", f"{cfg.tts_pause_comma}", str(seed),
    ]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:20]


def _take_path(cfg: Config, text: str, seed: int, engine: str) -> Path:
    return cfg.tts_cache_dir / f"{_cache_key(cfg, text, seed, engine)}.wav"


# --------------------------------------------------------------------------

def _generate_round(blocks_todo: list[tuple[Block, list[int]]], cfg: Config,
                    engine: str, log, total_blocks: int,
                    progress=None, cancel=None) -> dict[int, list[Take]]:
    """PASS 1 shape: one synthesizer load covering every take it must make."""
    takes: dict[int, list[Take]] = {}
    if not blocks_todo:
        return takes

    _check(cancel)
    synth = make_synthesizer(cfg, engine, log=log)
    synth.load()
    try:
        for position, (block, seeds) in enumerate(blocks_todo):
            made: list[Take] = []
            for i, seed in enumerate(seeds):
                _check(cancel)
                path = _take_path(cfg, block.text, seed, engine)
                human = f"Block {block.index + 1}/{total_blocks}"
                if progress:
                    progress(stage="voice", block=block.index + 1,
                             blocks=total_blocks, take=i + 1, takes=len(seeds),
                             cached=path.is_file(),
                             detail=f"Block {block.index + 1}/{total_blocks}: "
                                    f"take {i + 1}/{len(seeds)}")
                if path.is_file():
                    log.user(f"{human}: take {i + 1}/{len(seeds)} cache se mil gaya.")
                    import soundfile as sf

                    info = sf.info(str(path))
                    made.append(Take(seed, path, info.frames / info.samplerate))
                    continue

                log.user(f"{human}: awaaz ban rahi hai "
                         f"(take {i + 1}/{len(seeds)})")
                result = synth.synthesize(block.text, path, seed=seed)
                made.append(Take(seed, result.path, result.duration))
            takes[block.index] = made
    finally:
        # Always free the model, cancelled or not - this machine cannot
        # afford a leaked Chatterbox.
        synth.free()
    return takes


def _verify_round(takes: dict[int, list[Take]], blocks: dict[int, Block],
                  cfg: Config, engine: str, log) -> None:
    """PASS 2 shape: one verifier load covering every take it must score."""
    pending = [(bi, t) for bi, lst in takes.items() for t in lst
               if not t.words_ok and not t.transcript]
    if not pending:
        return

    verifier = make_verifier(cfg, engine, log=log)
    verifier.load()
    try:
        for block_index, take in pending:
            block = blocks[block_index]
            result = verifier.verify(take.path, block.text)
            take.similarity = result.similarity
            take.words_ok = result.words_ok
            take.transcript = result.transcript
            take.words = result.words
            log.debug("block %d seed %d -> sim=%s words_ok=%s",
                      block_index, take.seed, result.similarity, result.words_ok)
    finally:
        verifier.free()


def _pick(takes: list[Take]) -> tuple[Take | None, int]:
    """Best take whose words are correct; similarity breaks the tie."""
    valid = [t for t in takes if t.words_ok]
    if not valid:
        return None, 0
    best = max(valid, key=lambda t: (t.similarity if t.similarity is not None else 0.0))
    return best, takes.index(best) + 1


def run_voice_stage(blocks: list[Block], cfg: Config | None = None,
                    engine: str | None = None, best_of: int | None = None,
                    log=None, progress=None, cancel=None) -> list[BlockAudio]:
    cfg = cfg or Config.load()
    log = log or agent_log.get()
    engine = (engine or cfg.tts_engine).lower()
    n_takes = best_of if best_of is not None else cfg.best_of_n()
    cfg.tts_cache_dir.mkdir(parents=True, exist_ok=True)

    # The converter slot is honoured even though only "none" exists today.
    converter = make_converter(cfg, log=log)
    if converter is not None:
        converter.load()

    spoken = [b for b in blocks if not b.is_silent]
    silent = [b for b in blocks if b.is_silent]
    by_index = {b.index: b for b in blocks}

    log.user(f"Awaaz banane ka kaam shuru: {len(spoken)} block, "
             f"{n_takes} take har block ke liye (engine: {engine}).")
    if silent:
        log.user(f"{len(silent)} block khamosh rahenge (koi text nahi).")

    base_seed = cfg.tts_seed if cfg.tts_seed is not None else 0

    # ---- PASS 1 ----------------------------------------------------------
    todo = [(b, [base_seed + i for i in range(n_takes)]) for b in spoken]
    takes = _generate_round(todo, cfg, engine, log, len(spoken),
                            progress=progress, cancel=cancel)

    # ---- PASS 2 ----------------------------------------------------------
    _check(cancel)
    log.user("Har take ki quality check ho rahi hai...")
    if progress:
        progress(stage="verify", detail="Quality check ho raha hai...")
    _verify_round(takes, by_index, cfg, engine, log)

    # ---- PASS 3 ----------------------------------------------------------
    for round_number in range(1, cfg.tts_retry_rounds + 1):
        failing = [b for b in spoken if _pick(takes.get(b.index, []))[0] is None]
        if not failing:
            break
        log.user(f"{len(failing)} block ke alfaaz theek nahi aaye - "
                 f"dobara koshish ({round_number}/{cfg.tts_retry_rounds})...")
        offset = base_seed + 1000 * round_number
        retry = [(b, [offset + i for i in range(n_takes)]) for b in failing]
        extra = _generate_round(retry, cfg, engine, log, len(spoken),
                                progress=progress, cancel=cancel)
        for index, lst in extra.items():
            takes.setdefault(index, []).extend(lst)
        _verify_round(takes, by_index, cfg, engine, log)

    # ---- assemble --------------------------------------------------------
    out: list[BlockAudio] = []
    for block in blocks:
        if block.is_silent:
            out.append(BlockAudio(
                block_index=block.index, text="", scene_ids=block.scene_ids,
                scene_start=block.start, scene_end=block.end, wav=None,
                duration=0.0, similarity=None, words_ok=True, transcript="",
                words=[], seed=None, take_number=0, takes_tried=0, silent=True,
            ))
            continue

        block_takes = takes.get(block.index, [])
        best, take_number = _pick(block_takes)
        if best is None and block_takes:
            # Nothing passed the word check. Keep the closest-sounding take
            # rather than dropping the block, and flag it loudly.
            best = max(block_takes,
                       key=lambda t: (t.similarity if t.similarity is not None else 0.0))
            take_number = block_takes.index(best) + 1
            log.warning(
                f"Block {block.index + 1}: kisi bhi take ke alfaaz theek nahi - "
                f"sab se behtar take rakh liya, aap khud sun kar check karein."
            )

        if best is None:
            log.error(f"Block {block.index + 1}: koi take nahi bana.")
            out.append(BlockAudio(
                block_index=block.index, text=block.text,
                scene_ids=block.scene_ids, scene_start=block.start,
                scene_end=block.end, wav=None, duration=0.0, similarity=None,
                words_ok=False, transcript="", words=[], seed=None,
                take_number=0, takes_tried=len(block_takes),
            ))
            continue

        if converter is not None:
            converted = cfg.tts_cache_dir / f"{best.path.stem}_conv.wav"
            best.path = converter.convert(best.path, converted)

        reliable = best.duration >= cfg.min_similarity_seconds
        if not reliable and best.similarity is not None:
            log.debug("block %d is %.2fs: similarity %.1f%% not reliable",
                      block.index, best.duration, best.similarity)

        out.append(BlockAudio(
            block_index=block.index, text=block.text,
            scene_ids=block.scene_ids, scene_start=block.start,
            scene_end=block.end, wav=best.path, duration=best.duration,
            similarity=best.similarity, words_ok=best.words_ok,
            transcript=best.transcript, words=best.words, seed=best.seed,
            take_number=take_number, takes_tried=len(block_takes),
            similarity_reliable=reliable,
        ))

    if converter is not None:
        converter.free()

    good = sum(1 for b in out if b.words_ok and not b.silent)
    log.user(f"Awaaz tayyar: {good}/{len(spoken)} block ke alfaaz bilkul theek hain.")

    weak = [b for b in out if not b.silent and b.similarity_reliable
            and b.similarity is not None and b.similarity < cfg.min_similarity]
    if weak:
        log.warning(
            f"{len(weak)} block ki awaaz client se kaafi milti nahi "
            f"({cfg.min_similarity:.0f}% se kam) - sun kar check karein."
        )
    short = [b for b in out if not b.silent and not b.similarity_reliable]
    if short:
        log.debug("%d blocks shorter than %.1fs: similarity not gated",
                  len(short), cfg.min_similarity_seconds)
    return out


def save_report(results: list[BlockAudio], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([r.to_dict() for r in results], indent=2), encoding="utf-8"
    )
