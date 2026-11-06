"""One job at a time, with a queue, live progress, cancel and resume.

This machine cannot run two jobs at once - a single Chatterbox load already
uses most of the RAM - so there is exactly one worker thread and everything
else waits in line with its position shown.

Every job owns a folder containing status.json. That file is the source of
truth, not memory, so:
  * finished jobs survive a server restart
  * a job interrupted by a crash or a reboot can be RESUMED

Resume costs almost nothing because the TTS cache is keyed on
text + reference + settings + seed: voice already generated is found in the
cache and never made again.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent import log as agent_log  # noqa: E402
from agent.config import Config  # noqa: E402
from agent.pipeline import run_job  # noqa: E402
from agent.voice_stage import JobCancelled  # noqa: E402

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"
INTERRUPTED = "interrupted"

STAGE_LABELS = {
    "scenes": "Detecting scenes",
    "script": "Splitting script",
    "voice": "Generating narration",
    "verify": "Checking narration quality",
    "align": "Planning timeline",
    "subtitles": "Writing subtitles",
    "audio": "Mastering audio",
    "render": "Rendering final video",
    "qc": "Running final checks",
}

# Rough starting points, replaced by this machine's measured speed as soon
# as it has produced one block of its own.
DEFAULT_SECONDS_PER_WORD = {"standard": 5.5, "turbo": 1.6, "mock": 0.01}
MODEL_LOAD_SECONDS = {"standard": 70.0, "turbo": 70.0, "mock": 0.0}


@dataclass
class JobStatus:
    id: str
    name: str
    state: str = QUEUED
    stage: str = ""
    detail: str = ""
    created: str = ""
    started: str = ""
    finished: str = ""
    position: int = 0
    block: int = 0
    blocks: int = 0
    take: int = 0
    takes: int = 0
    done_takes: int = 0
    total_takes: int = 0
    estimate_seconds: float = 0.0
    elapsed: float = 0.0
    remaining: float = 0.0
    error: str = ""
    params: dict = field(default_factory=dict)
    result: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class SpeedProfile:
    """What this machine actually manages, measured, not guessed."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict = {}
        if path.is_file():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                self.data = {}

    def seconds_per_word(self, engine: str) -> float:
        return float(self.data.get(engine, {}).get(
            "seconds_per_word", DEFAULT_SECONDS_PER_WORD.get(engine, 3.0)))

    def record(self, engine: str, words: int, takes: int,
               seconds: float) -> None:
        if words <= 0 or takes <= 0 or seconds <= 0:
            return
        observed = seconds / (words * takes)
        previous = self.data.get(engine, {}).get("seconds_per_word")
        # Smooth it: one odd run should not throw the estimate off.
        blended = observed if previous is None else 0.7 * previous + 0.3 * observed
        self.data.setdefault(engine, {})["seconds_per_word"] = round(blended, 4)
        self.data[engine]["samples"] = self.data[engine].get("samples", 0) + 1
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")


def estimate_seconds(cfg: Config, speed: SpeedProfile, words: int,
                     takes: int, engine: str, video_seconds: float) -> float:
    """A rough figure, clearly labelled as rough in the UI."""
    voice = words * takes * speed.seconds_per_word(engine)
    load = MODEL_LOAD_SECONDS.get(engine, 40.0)
    # Scene detection, mastering, render and QC all scale with the video.
    fixed = 8.0 + video_seconds * 1.1
    return voice + load + fixed


class JobManager:
    def __init__(self, cfg: Config, log=None) -> None:
        self.cfg = cfg
        self.log = log or agent_log.get()
        self.root = cfg.output_dir / "jobs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.speed = SpeedProfile(cfg.output_dir / "speed_profile.json")

        self._lock = threading.RLock()
        self._queue: list[str] = []
        self._current: str | None = None
        self._cancel = threading.Event()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None

        self._recover()

    # -- persistence -------------------------------------------------------

    def job_dir(self, job_id: str) -> Path:
        return self.root / job_id

    def _status_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "status.json"

    def read(self, job_id: str) -> JobStatus | None:
        path = self._status_path(job_id)
        if not path.is_file():
            return None
        try:
            return JobStatus(**json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            return None

    def write(self, status: JobStatus) -> None:
        path = self._status_path(status.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write then move, so a crash mid-write cannot corrupt the file.
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(status.to_dict(), indent=2), encoding="utf-8")
        temp.replace(path)

    def all_jobs(self) -> list[JobStatus]:
        out = []
        for folder in sorted(self.root.iterdir(), reverse=True):
            if not folder.is_dir():
                continue
            status = self.read(folder.name)
            if status:
                out.append(status)
        return out

    def _recover(self) -> None:
        """Anything left RUNNING was interrupted by a crash or a restart."""
        for status in self.all_jobs():
            if status.state == RUNNING:
                status.state = INTERRUPTED
                status.detail = ("The server stopped, so this job can be "
                                 "resumed.")
                self.write(status)
                self.log.user(f"Job {status.id} was interrupted, "
                              f"it can be resumed.")
            elif status.state == QUEUED:
                # It never started, so put it back in the queue.
                self._queue.append(status.id)

    # -- worker ------------------------------------------------------------

    def start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stop.clear()
        self._worker = threading.Thread(target=self._loop, daemon=True,
                                        name="job-worker")
        self._worker.start()

    def shutdown(self, wait: float = 0.0) -> None:
        # _stop before _cancel: the running job checks _stop to decide
        # whether it was interrupted or genuinely cancelled.
        self._stop.set()
        self._cancel.set()
        self._wake.set()
        if self._worker and wait:
            self._worker.join(timeout=wait)

    def _loop(self) -> None:
        while not self._stop.is_set():
            job_id = None
            with self._lock:
                if self._queue:
                    job_id = self._queue.pop(0)
                    self._current = job_id
                    self._cancel.clear()
            if job_id is None:
                self._wake.wait(timeout=0.25)
                self._wake.clear()
                continue
            try:
                self._run(job_id)
            except Exception:
                self.log.error(f"job {job_id} crashed:\n{traceback.format_exc()}")
            finally:
                with self._lock:
                    self._current = None
                self._renumber()

    def _renumber(self) -> None:
        with self._lock:
            for position, job_id in enumerate(self._queue, start=1):
                status = self.read(job_id)
                if status and status.state == QUEUED:
                    status.position = position
                    self.write(status)

    # -- public API --------------------------------------------------------

    def submit(self, name: str, params: dict, job_id: str | None = None) -> str:
        job_id = job_id or f"{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}"
        status = JobStatus(
            id=job_id, name=name, state=QUEUED,
            created=datetime.now().isoformat(timespec="seconds"),
            params=params,
            estimate_seconds=params.get("estimate_seconds", 0.0),
            total_takes=params.get("total_takes", 0),
        )
        self.job_dir(job_id).mkdir(parents=True, exist_ok=True)
        self.write(status)
        with self._lock:
            self._queue.append(job_id)
        self._renumber()
        self._wake.set()
        self.start()
        return job_id

    def resume(self, job_id: str) -> bool:
        status = self.read(job_id)
        if not status or status.state not in (INTERRUPTED, FAILED, CANCELLED):
            return False
        status.state = QUEUED
        status.error = ""
        status.detail = "Queued again."
        self.write(status)
        with self._lock:
            if job_id not in self._queue:
                self._queue.append(job_id)
        self._renumber()
        self._wake.set()
        self.start()
        return True

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            if job_id in self._queue:
                self._queue.remove(job_id)
                status = self.read(job_id)
                if status:
                    status.state = CANCELLED
                    status.detail = "Removed from the queue."
                    self.write(status)
                self._renumber()
                return True
            if self._current == job_id:
                self._cancel.set()
                return True
        return False

    def is_cancelled(self) -> bool:
        return self._cancel.is_set()

    def queue_view(self) -> list[str]:
        with self._lock:
            return list(self._queue)

    def current(self) -> str | None:
        with self._lock:
            return self._current

    # -- the run itself ----------------------------------------------------

    def _run(self, job_id: str) -> None:
        status = self.read(job_id)
        if status is None:
            return

        params = status.params
        job_dir = self.job_dir(job_id)
        started_at = time.perf_counter()

        status.state = RUNNING
        status.started = datetime.now().isoformat(timespec="seconds")
        status.position = 0
        status.detail = "Starting..."
        self.write(status)

        engine = params.get("engine") or self.cfg.tts_engine
        words = int(params.get("words", 0))
        takes = int(params.get("takes", 1))

        state = {"done_takes": 0}

        def progress(**kwargs) -> None:
            current = self.read(job_id) or status
            stage = kwargs.get("stage", current.stage)
            current.stage = stage
            current.detail = kwargs.get("detail") or STAGE_LABELS.get(stage, stage)
            if "block" in kwargs:
                current.block = kwargs["block"]
                current.blocks = kwargs.get("blocks", current.blocks)
                current.take = kwargs.get("take", 0)
                current.takes = kwargs.get("takes", 0)
                state["done_takes"] += 1
                current.done_takes = state["done_takes"]
            current.state = RUNNING
            current.elapsed = time.perf_counter() - started_at

            # Estimate from THIS run's measured pace once there is enough
            # of it to mean anything; fall back to the stored profile.
            if current.done_takes >= 2 and current.total_takes:
                per_take = current.elapsed / current.done_takes
                left = max(0, current.total_takes - current.done_takes)
                current.remaining = max(0.0, per_take * left)
            elif current.estimate_seconds:
                current.remaining = max(
                    0.0, current.estimate_seconds - current.elapsed)
            self.write(current)

        log = agent_log.setup(self.cfg.log_dir, self.cfg.log_level,
                              run_name=f"job_{job_id}")
        try:
            result = run_job(
                Path(params["video"]), Path(params["script"]), self.cfg, log,
                music=Path(params["music"]) if params.get("music") else None,
                keep_original_audio=bool(params.get("keep_original_audio")),
                burn_subs=bool(params.get("burn_subs")),
                draft=bool(params.get("draft")),
                engine=params.get("engine"),
                job_dir=job_dir,
                out_path=job_dir / "final.mp4",
                progress=progress,
                cancel=self.is_cancelled,
            )
        except JobCancelled:
            status = self.read(job_id) or status
            # A job stopped because the SERVER is going down was not
            # abandoned by anyone - it is unfinished work, and saying
            # "cancelled" would hide the fact that it can be resumed.
            if self._stop.is_set():
                status.state = INTERRUPTED
                status.detail = ("The server stopped, so this job can be "
                                 "resumed.")
                self.log.user(f"Job {job_id} interrupted (server stopped).")
            else:
                status.state = CANCELLED
                status.detail = "Job cancelled."
                status.finished = datetime.now().isoformat(timespec="seconds")
                self.log.user(f"Job {job_id} cancelled.")
            self.write(status)
            return
        except Exception as exc:
            status = self.read(job_id) or status
            status.state = FAILED
            status.error = f"{type(exc).__name__}: {exc}"
            status.detail = "Job failed."
            status.finished = datetime.now().isoformat(timespec="seconds")
            self.write(status)
            self.log.error(f"job {job_id} failed:\n{traceback.format_exc()}")
            return

        elapsed = time.perf_counter() - started_at
        if words and takes:
            self.speed.record(engine, words, takes, elapsed)

        status = self.read(job_id) or status
        status.state = DONE
        status.stage = "done"
        status.detail = "Complete."
        status.finished = datetime.now().isoformat(timespec="seconds")
        status.elapsed = elapsed
        status.remaining = 0.0
        status.result = {
            "video": str(result.video) if result.video else None,
            "subtitles": str(result.subtitles) if result.subtitles else None,
            "timeline": str(result.timeline_json) if result.timeline_json else None,
            "report": str(job_dir / f"{Path(params['video']).stem}_report.json"),
            "qc_passed": bool(result.report.passed) if result.report else None,
            "listen_to": result.report.listen_to if result.report else [],
            "warnings": result.warnings,
        }
        self.write(status)
        self.log.user(f"Job {job_id} complete ({elapsed:.0f}s).")
