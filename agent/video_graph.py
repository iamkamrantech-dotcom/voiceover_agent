"""The one place that turns a Timeline into an FFmpeg video filter graph.

Both the draft preview and the final render use this. They MUST re-time
identically - a draft that proves sync is worthless if the final render
re-times differently - and last phase produced two bugs from exactly this
kind of duplicated logic. So there is one builder, parameterised by output
scale and subtitles.

Per block, up to three chains:

    A     src_start .. split_at   optionally slowed (the scene's own motion
                                  lives here, so slowdown comes first)
    HOLD  the hold_end frame, repeated for freeze_seconds
    B     split_at .. src_end     the outgoing transition, normal speed

THE LIVING HOLD
---------------
A freeze held perfectly still reads as a stall, so once it is longer than
HOLD_MOTION_MIN_SECONDS the frozen frame gets a slow push-in of at most
HOLD_ZOOM_MAX, eased with smoothstep so it never starts or stops abruptly.

Where the zoom ends depends on what follows:
  * hard cut / end of video -> it can keep pushing in; the cut hides it.
  * FADE -> it must ease back to exactly 1.0 before the blend starts,
    otherwise the dissolve begins from a different framing and jumps.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.align import BlockPlan, Timeline  # noqa: E402
from agent.config import Config  # noqa: E402

BURN_STYLE = "FontSize=16,Outline=1,Shadow=0,MarginV=18"


def escape_for_filter(path: Path) -> str:
    """FFmpeg filter arguments need the Windows drive colon escaped."""
    text = str(path.resolve()).replace("\\", "/")
    return text.replace(":", r"\:")


def split_point(timeline: Timeline, block: BlockPlan) -> tuple[float, bool]:
    """Where the clean part of a block ends, and whether a separate tail
    chain is needed for the outgoing transition.

    `split_at` comes from the aligner (hold_end + 1 frame), so the renderer
    and the plan can never drift apart.

    A tail only exists when something follows hold_end inside this block -
    true for a FADE, false for a hard CUT, where hold_end is the last frame
    of the block. Retiming is applied to the clean part either way: tying
    it to the tail would silently drop the freeze on every hard cut.
    """
    frame = 1.0 / timeline.fps if timeline.fps else 0.04
    at = block.split_at or min(block.src_end, block.hold_end + frame)
    has_tail = at < block.src_end - 1e-6
    return at, has_tail


def wants_living_hold(block: BlockPlan, cfg: Config) -> bool:
    return (cfg.hold_motion == "zoom"
            and block.freeze_seconds > cfg.hold_motion_min_seconds
            and cfg.hold_zoom_max > 0)


def zoom_expression(block: BlockPlan, frames: int, cfg: Config) -> str:
    """Smoothstep push-in over `frames` output frames.

    u  = on/(frames-1)            progress through the hold, 0 -> 1
    s  = 3u^2 - 2u^3              smoothstep: zero slope at both ends, so
                                  the move eases in and eases out
    z  = 1 + MAX*s

    For a fade the move is triangular - out to MAX by halfway, back to
    exactly 1.0 at the end - because the dissolve has to begin from the
    same framing the frozen frame started at.
    """
    last = max(1, frames - 1)
    u = f"(on/{last})"
    if block.out_transition == "fade":
        # 0 -> 1 -> 0 : rise over the first half, return over the second
        tri = f"(if(lt({u},0.5),{u}*2,(1-{u})*2))"
        shaped = f"(3*{tri}*{tri}-2*{tri}*{tri}*{tri})"
    else:
        shaped = f"(3*{u}*{u}-2*{u}*{u}*{u})"
    return f"1+{cfg.hold_zoom_max:.4f}*{shaped}"


def _hold_chain(label_in: str, name: str, block: BlockPlan,
                timeline: Timeline, cfg: Config,
                size: tuple[int, int]) -> str:
    """One frame at hold_end, repeated - with or without the push-in."""
    fps = timeline.fps
    frame = 1.0 / fps if fps else 0.04
    frames = max(1, int(round(block.freeze_seconds * fps)))

    grab = (f"[{label_in}]trim=start={block.hold_end:.6f}:"
            f"end={block.hold_end + frame:.6f},setpts=PTS-STARTPTS")

    if not wants_living_hold(block, cfg):
        # Plain freeze: repeat the frame and leave it alone.
        return (f"{grab},loop=loop={frames - 1}:size=1:start=0,"
                f"setpts=N/({fps:.6f}*TB)[{name}]")

    width, height = size
    expr = zoom_expression(block, frames, cfg)
    return (
        f"{grab},loop=loop={frames - 1}:size=1:start=0,"
        f"setpts=N/({fps:.6f}*TB),"
        f"zoompan=z='{expr}':d=1:"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"s={width}x{height}:fps={fps:.6f}[{name}]"
    )


def chains_per_block(timeline: Timeline, block: BlockPlan, cfg: Config) -> int:
    count = 1
    if block.freeze_seconds > 0:
        count += 1
    if split_point(timeline, block)[1]:
        count += 1
    return count


def build_filter_graph(timeline: Timeline, cfg: Config, size: tuple[int, int],
                       subtitle_path: Path | None = None,
                       scale_height: int | None = None,
                       burn_style: str = BURN_STYLE) -> str:
    """`size` is the SOURCE frame size, needed by zoompan. `scale_height`
    downscales the finished video (preview only); None keeps source size."""
    blocks = timeline.blocks
    chains: list[str] = []

    copies = sum(chains_per_block(timeline, b, cfg) for b in blocks)
    split_labels = [f"s{i}" for i in range(copies)]
    chains.append("[0:v]split=" + str(copies)
                  + "".join(f"[{label}]" for label in split_labels))

    all_labels: list[str] = []
    cursor = 0

    for i, block in enumerate(blocks):
        at, has_tail = split_point(timeline, block)

        # ---- A: up to and including hold_end, optionally slowed ----------
        first = f"b{i}a"
        chain = (f"[{split_labels[cursor]}]trim=start={block.src_start:.6f}:"
                 f"end={at:.6f},setpts=PTS-STARTPTS")
        if block.video_slowdown > 1.0:
            chain += f",setpts={block.video_slowdown:.6f}*PTS"
        chains.append(chain + f"[{first}]")
        all_labels.append(first)
        cursor += 1

        # ---- HOLD: the frozen (or gently moving) hold_end frame ----------
        if block.freeze_seconds > 0:
            hold = f"b{i}h"
            chains.append(_hold_chain(split_labels[cursor], hold, block,
                                      timeline, cfg, size))
            all_labels.append(hold)
            cursor += 1

        # ---- B: the outgoing transition, always at normal speed ----------
        if has_tail:
            second = f"b{i}b"
            chains.append(
                f"[{split_labels[cursor]}]trim=start={at:.6f}:"
                f"end={block.src_end:.6f},setpts=PTS-STARTPTS[{second}]"
            )
            all_labels.append(second)
            cursor += 1

    concat_in = "".join(f"[{label}]" for label in all_labels)
    chains.append(f"{concat_in}concat=n={len(all_labels)}:v=1:a=0[joined]")

    tail = "[joined]"
    if scale_height:
        tail += f"scale=-2:{scale_height},"
    tail += f"fps={timeline.fps},setsar=1"
    if subtitle_path is not None:
        tail += (f",subtitles='{escape_for_filter(subtitle_path)}'"
                 f":force_style='{burn_style}'")
    tail += "[vout]"
    chains.append(tail)

    return ";".join(chains)


def needs_retiming(timeline: Timeline) -> bool:
    """True when any block is slowed or frozen. When nothing is re-timed and
    no subtitles are burned in, the video stream can simply be copied."""
    return any(b.video_slowdown > 1.0 or b.freeze_seconds > 0
               for b in timeline.blocks)
