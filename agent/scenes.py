"""Find where each image starts and ends in a slideshow video.

Two passes, because one is not enough:

1. COARSE, ~10 fps. Each sampled frame is compared with a frame about 0.5 s
   EARLIER, not with its neighbour. Frame-to-frame difference misses slow
   fades entirely - across 40 ms a crossfade barely moves - but across 0.5 s
   it is obvious. The metric mixes a colour-histogram distance (blind to
   zoom and pan, so Ken Burns does not fire) with a coarse pixel difference
   (catches cuts between images of similar palette).

2. REFINE, full frame rate. For each candidate region take a clean frame
   just before (A) and just after (B), then measure how far every frame in
   between has travelled from A towards B:

       alpha = dot(frame - A, B - A) / |B - A|^2

   alpha runs 0 -> 1 across the transition. A hard cut jumps 0 -> 1 in a
   single frame; a crossfade climbs gradually. So the shape of alpha tells
   us the KIND of transition, and the frame where it crosses 0.5 is the
   midpoint, frame-accurate.

Each scene also records `hold_end`: the last clean frame before its outgoing
transition. When a scene has to be stretched to fit narration we freeze on
that frame, never on a half-blended fade frame.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.config import Config  # noqa: E402
from agent import log as agent_log  # noqa: E402

HIST_BINS = [8, 8, 8]
HIST_RANGES = [0, 180, 0, 256, 0, 256]
HIST_FRAME = (160, 90)
PIX_GAIN = 4.0          # scales the coarse pixel difference into 0..1
ALPHA_ENTER = 0.05      # alpha above this = the transition has begun
ALPHA_MID = 0.50
ALPHA_EXIT = 0.95       # alpha above this = the transition is over


@dataclass
class Transition:
    kind: str                  # "cut" | "fade"
    start_frame: int
    mid_frame: int
    end_frame: int
    start: float
    mid: float
    end: float

    @property
    def frames(self) -> int:
        return self.end_frame - self.start_frame


@dataclass
class Scene:
    index: int
    start_frame: int
    end_frame: int            # exclusive
    hold_end_frame: int       # last CLEAN frame, never a blended one
    start: float
    end: float
    hold_end: float
    transition_in: Transition | None = None
    transition_out: Transition | None = None
    keyframe: str | None = None

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def hold_duration(self) -> float:
        return self.hold_end - self.start


@dataclass
class Detection:
    video: str
    fps: float
    frame_count: int
    duration: float
    scenes: list[Scene] = field(default_factory=list)
    transitions: list[Transition] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "video": self.video,
            "fps": self.fps,
            "frame_count": self.frame_count,
            "duration": round(self.duration, 3),
            "transitions": [asdict(t) for t in self.transitions],
            "scenes": [asdict(s) for s in self.scenes],
        }


# --------------------------------------------------------------------------
# frame features
# --------------------------------------------------------------------------

def _small_gray(frame: np.ndarray, w: int, h: int) -> np.ndarray:
    small = cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    return gray.astype(np.float32).ravel() / 255.0


def _hist(frame: np.ndarray) -> np.ndarray:
    small = cv2.resize(frame, HIST_FRAME, interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    h = cv2.calcHist([hsv], [0, 1, 2], None, HIST_BINS, HIST_RANGES)
    cv2.normalize(h, h, 0, 1, cv2.NORM_MINMAX)
    return h.flatten()


def _distance(small_a: np.ndarray, small_b: np.ndarray,
              hist_a: np.ndarray, hist_b: np.ndarray,
              hist_weight: float) -> float:
    d_hist = float(cv2.compareHist(hist_a, hist_b, cv2.HISTCMP_BHATTACHARYYA))
    d_pix = min(1.0, float(np.mean(np.abs(small_a - small_b))) * PIX_GAIN)
    return hist_weight * d_hist + (1.0 - hist_weight) * d_pix


# --------------------------------------------------------------------------
# detection
# --------------------------------------------------------------------------

def _read_features(path: Path, cfg: Config, step: int):
    """One sequential pass: small grayscale for every frame, histogram only
    at the sampled frames (histograms are the expensive part)."""
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    smalls: list[np.ndarray] = []
    hists: dict[int, np.ndarray] = {}

    index = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        smalls.append(_small_gray(frame, cfg.scene_small_width,
                                  cfg.scene_small_height))
        if index % step == 0:
            hists[index] = _hist(frame)
        index += 1
    cap.release()
    return fps, smalls, hists


def _coarse_candidates(smalls, hists, step: int, lag_steps: int,
                       cfg: Config, log) -> list[tuple[int, int]]:
    """Returns (first_frame, last_frame) of each run that looks like change."""
    sample_indices = sorted(hists.keys())
    scores: list[tuple[int, float]] = []

    for pos in range(lag_steps, len(sample_indices)):
        now = sample_indices[pos]
        before = sample_indices[pos - lag_steps]
        score = _distance(smalls[now], smalls[before],
                          hists[now], hists[before], cfg.scene_hist_weight)
        scores.append((now, score))
        if score > cfg.scene_threshold * 0.6:
            log.debug("coarse frame=%d score=%.3f", now, score)

    runs: list[tuple[int, int]] = []
    current: list[int] = []
    for frame_index, score in scores:
        if score > cfg.scene_threshold:
            current.append(frame_index)
        elif current:
            runs.append((current[0], current[-1]))
            current = []
    if current:
        runs.append((current[0], current[-1]))

    log.debug("coarse runs: %s", runs)
    return runs


def _refine(smalls: list[np.ndarray], run: tuple[int, int], fps: float,
            lag_frames: int, margin: int, cfg: Config, log) -> Transition | None:
    """Turn a rough region into a frame-accurate cut or fade."""
    n = len(smalls)
    start = max(0, run[0] - lag_frames - margin)
    end = min(n - 1, run[1] + margin)
    if end - start < 2:
        return None

    a = smalls[start]
    b = smalls[end]
    direction = b - a
    denom = float(np.dot(direction, direction))
    if denom < 1e-6:
        log.debug("refine %s: A and B identical, discarding", run)
        return None

    alphas = np.array(
        [float(np.dot(smalls[i] - a, direction) / denom) for i in range(start, end + 1)]
    )

    mid_rel = next((i for i, v in enumerate(alphas) if v >= ALPHA_MID), None)
    if mid_rel is None:
        log.debug("refine %s: alpha never reached 0.5", run)
        return None

    enter_rel = next((i for i, v in enumerate(alphas) if v >= ALPHA_ENTER), mid_rel)
    exit_rel = next(
        (i for i in range(mid_rel, len(alphas)) if alphas[i] >= ALPHA_EXIT),
        len(alphas) - 1,
    )

    mid_frame = start + mid_rel
    enter_frame = start + enter_rel
    exit_frame = start + exit_rel
    span = exit_frame - enter_frame

    if span <= cfg.fade_min_frames:
        # One-frame jump from A to B: a hard cut. mid_frame is the first
        # frame that belongs to the NEW scene.
        return Transition("cut", mid_frame, mid_frame, mid_frame,
                          mid_frame / fps, mid_frame / fps, mid_frame / fps)

    return Transition("fade", enter_frame, mid_frame, exit_frame,
                      enter_frame / fps, mid_frame / fps, exit_frame / fps)


def _build_scenes(transitions: list[Transition], frame_count: int,
                  fps: float) -> list[Scene]:
    scenes: list[Scene] = []
    start_frame = 0
    incoming: Transition | None = None

    for t in transitions:
        # A cut ends the scene on the cut frame; a fade ends it at the
        # midpoint so scenes stay contiguous with no gap between them.
        end_frame = t.mid_frame
        hold_end_frame = (t.start_frame - 1) if t.kind == "fade" else (t.mid_frame - 1)
        hold_end_frame = max(start_frame, hold_end_frame)
        scenes.append(Scene(
            index=len(scenes),
            start_frame=start_frame,
            end_frame=end_frame,
            hold_end_frame=hold_end_frame,
            start=start_frame / fps,
            end=end_frame / fps,
            hold_end=hold_end_frame / fps,
            transition_in=incoming,
            transition_out=t,
        ))
        start_frame = t.mid_frame
        incoming = t

    scenes.append(Scene(
        index=len(scenes),
        start_frame=start_frame,
        end_frame=frame_count,
        hold_end_frame=max(start_frame, frame_count - 1),
        start=start_frame / fps,
        end=frame_count / fps,
        hold_end=max(start_frame, frame_count - 1) / fps,
        transition_in=incoming,
        transition_out=None,
    ))
    return scenes


def _merge_short(scenes: list[Scene], cfg: Config, log) -> list[Scene]:
    """Fold anything shorter than MIN_SCENE_SECONDS into its neighbour."""
    if len(scenes) <= 1:
        return scenes

    merged: list[Scene] = []
    for scene in scenes:
        if scene.duration < cfg.min_scene_seconds and merged:
            previous = merged[-1]
            log.debug("merging scene %.2f-%.2f (%.2fs) into previous",
                      scene.start, scene.end, scene.duration)
            previous.end_frame = scene.end_frame
            previous.end = scene.end
            previous.hold_end_frame = scene.hold_end_frame
            previous.hold_end = scene.hold_end
            previous.transition_out = scene.transition_out
        else:
            merged.append(scene)

    # A too-short FIRST scene has no previous, so fold it forward instead.
    while len(merged) > 1 and merged[0].duration < cfg.min_scene_seconds:
        second = merged[1]
        second.start_frame = merged[0].start_frame
        second.start = merged[0].start
        second.transition_in = merged[0].transition_in
        merged.pop(0)

    for i, scene in enumerate(merged):
        scene.index = i
    return merged


def _save_keyframes(path: Path, scenes: list[Scene], out_dir: Path, log) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(path))
    for scene in scenes:
        # Middle of the CLEAN part, so a keyframe is never a blended frame.
        target = (scene.start_frame + scene.hold_end_frame) // 2
        cap.set(cv2.CAP_PROP_POS_FRAMES, target)
        ok, frame = cap.read()
        if not ok:
            log.warning("could not read keyframe for scene %d", scene.index)
            continue
        dest = out_dir / f"scene_{scene.index:03d}.jpg"
        cv2.imwrite(str(dest), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
        scene.keyframe = str(dest)
    cap.release()


def detect_scenes(video: Path | str, cfg: Config | None = None,
                  keyframe_dir: Path | None = None,
                  log=None) -> Detection:
    cfg = cfg or Config.load()
    log = log or agent_log.get()
    path = Path(video)
    if not path.is_file():
        raise FileNotFoundError(f"Video not found: {path}")

    # Sampling step in frames, derived from the video's own fps.
    cap = cv2.VideoCapture(str(path))
    probe_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    cap.release()
    step = max(1, int(round(probe_fps / max(cfg.scene_sample_fps, 1.0))))
    lag_frames = max(1, int(round(cfg.scene_lag_seconds * probe_fps)))
    lag_steps = max(1, int(round(lag_frames / step)))

    log.user(f"Detecting scenes in {path.name}")
    log.debug("step=%d lag_frames=%d lag_steps=%d threshold=%.2f",
              step, lag_frames, lag_steps, cfg.scene_threshold)

    fps, smalls, hists = _read_features(path, cfg, step)
    frame_count = len(smalls)
    duration = frame_count / fps
    log.debug("fps=%.3f frames=%d duration=%.3f", fps, frame_count, duration)

    runs = _coarse_candidates(smalls, hists, step, lag_steps, cfg, log)
    log.user(f"{len(runs)} candidate transitions found, locating exact frames...")

    transitions: list[Transition] = []
    for run in runs:
        t = _refine(smalls, run, fps, lag_frames, step, cfg, log)
        if t is None:
            continue
        if transitions and t.mid_frame <= transitions[-1].end_frame:
            log.debug("dropping overlapping transition at %d", t.mid_frame)
            continue
        transitions.append(t)
        log.debug("%s frames %d-%d (mid %d)", t.kind, t.start_frame,
                  t.end_frame, t.mid_frame)

    scenes = _build_scenes(transitions, frame_count, fps)
    scenes = _merge_short(scenes, cfg, log)

    if keyframe_dir is not None:
        _save_keyframes(path, scenes, Path(keyframe_dir), log)

    cuts = sum(1 for t in transitions if t.kind == "cut")
    fades = sum(1 for t in transitions if t.kind == "fade")
    log.user(f"{len(scenes)} scenes found ({cuts} hard cuts, {fades} fades).")

    return Detection(path.name, fps, frame_count, duration, scenes, transitions)


def main(argv: list[str]) -> int:
    cfg = Config.load()
    log = agent_log.setup(cfg.log_dir, cfg.log_level, run_name="scenes")
    if len(argv) < 2:
        print(__doc__)
        return 2

    video = Path(argv[1])
    keyframes = cfg.output_dir / "scenes" / video.stem
    result = detect_scenes(video, cfg, keyframe_dir=keyframes, log=log)

    print()
    print(f"  {'#':<4}{'start':>8}{'end':>9}{'hold_end':>10}{'dur':>8}  in -> out")
    print("  " + "-" * 66)
    for s in result.scenes:
        t_in = s.transition_in.kind if s.transition_in else "start"
        t_out = s.transition_out.kind if s.transition_out else "end"
        print(f"  {s.index:<4}{s.start:8.2f}{s.end:9.2f}{s.hold_end:10.2f}"
              f"{s.duration:8.2f}  {t_in} -> {t_out}")

    out_json = cfg.output_dir / "scenes" / f"{video.stem}.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
    print(f"\n  JSON: {out_json}")
    print(f"  Keyframes: {keyframes}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
