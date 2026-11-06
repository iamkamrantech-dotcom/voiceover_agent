"""Web app tests, mock engine, against the generated test videos.

Covers the things that are easy to get wrong and expensive to discover
later: the fast check loop, a real job through the queue, cancel, and -
the important one - killing the manager mid-job and resuming it WITHOUT
regenerating voice that is already in the TTS cache.

    venv\\Scripts\\python.exe tests\\test_server.py
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from agent import log as agent_log          # noqa: E402
from agent.config import Config             # noqa: E402
from agent.jobs import JobManager           # noqa: E402

MEDIA = PROJECT_ROOT / "tests" / "media"

SHORT_SCRIPT = (
    "Welcome back to the channel. "
    "This first place is somewhere almost nobody has visited. "
    "The second location is stranger still and hard to reach. "
    "Our third stop sits high above the clouds all year round."
)
LONG_SCRIPT = (
    "Welcome back to the channel and thank you so much for joining me here "
    "again today for another one of these long rambling introductions that "
    "simply will not fit. "
    "This first location is a place that almost nobody in the entire world "
    "has ever actually managed to visit in person before now. "
    "The second spot on our list today is even stranger than the first one "
    "and it is considerably harder to reach by any normal means at all. "
    "Our third and final stop sits high above the clouds all year round."
)


class Results:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str, bool]] = []

    def add(self, name: str, expected: str, got: str, ok: bool) -> None:
        self.rows.append((name, expected, got, ok))

    def report(self) -> bool:
        w = max(len(r[0]) for r in self.rows)
        print(f"  {'check':<{w + 2}}{'expected':<26}{'got':<30}result")
        print("  " + "-" * (w + 60))
        for name, expected, got, ok in self.rows:
            print(f"  {name:<{w + 2}}{expected:<26}{got:<30}"
                  f"{'PASS' if ok else 'FAIL'}")
        failed = sum(1 for r in self.rows if not r[3])
        print(f"\n  {len(self.rows) - failed}/{len(self.rows)} checks passed")
        return failed == 0


def wait_for(client: TestClient, job_id: str, states, timeout: float = 240.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = client.get(f"/api/jobs/{job_id}").json()
        if last["state"] in states:
            return last
        time.sleep(0.4)
    return last


def upload_video(client: TestClient, name: str) -> str:
    with (MEDIA / name).open("rb") as handle:
        res = client.post("/api/upload",
                          files={"video": (name, handle, "video/mp4")})
    res.raise_for_status()
    return res.json()["video"]


def do_check(client: TestClient, video: str, text: str) -> dict:
    res = client.post("/api/check",
                      data={"video": video, "script_text": text,
                            "draft": "true"})
    res.raise_for_status()
    return res.json()


def main() -> int:
    cfg = Config.load()
    log = agent_log.setup(cfg.log_dir, cfg.log_level, run_name="test_server")

    if not (MEDIA / "cuts.mp4").is_file():
        log.user("Test videos not found. Run tests/make_test_videos.py first.")
        return 1

    # Keep this suite's jobs and cache out of the real output folders.
    sandbox = PROJECT_ROOT / "outputs" / "_server_test"
    if sandbox.exists():
        shutil.rmtree(sandbox, ignore_errors=True)
    sandbox.mkdir(parents=True, exist_ok=True)

    import server

    server.cfg.output_dir = sandbox
    server.cfg.work_dir = sandbox / "_work"
    server.cfg.tts_cache_dir = sandbox / "_tts_cache"
    server.cfg.tts_engine = "mock"
    server.cfg.ensure_dirs()
    server.manager.shutdown(wait=2)
    server.manager = JobManager(server.cfg, log=log)
    server.manager.start()
    server.UPLOADS = sandbox / "uploads"

    r = Results()
    client = TestClient(server.app)

    # ---- 1. upload + check ----------------------------------------------
    video = upload_video(client, "cuts.mp4")
    r.add("upload video", "path returned", Path(video).name, bool(video))

    first = do_check(client, video, LONG_SCRIPT)
    needs_cut = sum(1 for row in first["rows"] if row["cut_words"])
    r.add("check runs", "rows returned", f"{len(first['rows'])} rows",
          len(first["rows"]) > 0)
    r.add("check flags long script", ">0 blocks need cuts",
          f"{needs_cut} blocks", needs_cut > 0)
    r.add("check gives estimate", ">0s",
          f"{first['estimate_seconds']}s", first["estimate_seconds"] > 0)

    # ---- 2. edit the script and re-check ---------------------------------
    second = do_check(client, video, SHORT_SCRIPT)
    needs_cut_2 = sum(1 for row in second["rows"] if row["cut_words"])
    r.add("re-check after edit", "fewer blocks need cuts",
          f"{needs_cut} -> {needs_cut_2}", needs_cut_2 < needs_cut)

    # ---- 3. run a job ----------------------------------------------------
    started = client.post("/api/jobs", data={
        "video": video, "script": second["script_path"],
        "draft": "true", "engine": "mock",
        "words": second["words"], "total_takes": second["total_takes"],
        "estimate_seconds": second["estimate_seconds"],
    })
    job_id = started.json()["id"]
    status = wait_for(client, job_id, {"done", "failed", "cancelled"})
    r.add("job completes", "done", status["state"], status["state"] == "done")
    r.add("job reports stages", "non-empty stage", status.get("stage", ""),
          bool(status.get("stage")))

    # ---- 4. downloads ----------------------------------------------------
    for kind, minimum in (("video", 10_000), ("subtitles", 20), ("report", 50)):
        res = client.get(f"/api/jobs/{job_id}/file/{kind}")
        size = len(res.content) if res.status_code == 200 else 0
        r.add(f"download {kind}", f">{minimum} bytes", f"{size} bytes",
              res.status_code == 200 and size > minimum)

    timeline = client.get(f"/api/jobs/{job_id}/timeline")
    r.add("timeline json", "has blocks",
          f"{len(timeline.json().get('blocks', []))} blocks",
          timeline.status_code == 200 and timeline.json().get("blocks"))

    # ---- 5. cancel -------------------------------------------------------
    cancel_check = do_check(client, video, LONG_SCRIPT)
    to_cancel = client.post("/api/jobs", data={
        "video": video, "script": cancel_check["script_path"],
        "draft": "true", "engine": "mock",
        "words": cancel_check["words"],
        "total_takes": cancel_check["total_takes"],
    }).json()["id"]
    client.post(f"/api/jobs/{to_cancel}/cancel")
    cancelled = wait_for(client, to_cancel, {"cancelled", "done", "failed"}, 60)
    r.add("cancel works", "cancelled", cancelled["state"],
          cancelled["state"] == "cancelled")

    # ---- 6. queue of two -------------------------------------------------
    queue_ids = []
    for _ in range(2):
        queue_ids.append(client.post("/api/jobs", data={
            "video": video, "script": second["script_path"],
            "draft": "true", "engine": "mock",
            "words": second["words"],
            "total_takes": second["total_takes"],
        }).json()["id"])

    seen_queued = False
    deadline = time.time() + 20
    while time.time() < deadline and not seen_queued:
        states = [client.get(f"/api/jobs/{j}").json() for j in queue_ids]
        if any(s["state"] == "queued" for s in states):
            seen_queued = True
            positions = [s["position"] for s in states if s["state"] == "queued"]
            r.add("queue shows position", ">=1", str(positions),
                  all(p >= 1 for p in positions))
        if all(s["state"] in ("done", "failed") for s in states):
            break
        time.sleep(0.2)
    r.add("second job queued", "one waits",
          "yes" if seen_queued else "both ran at once", seen_queued)

    for job in queue_ids:
        wait_for(client, job, {"done", "failed", "cancelled"})
    running_at_once = sum(
        1 for j in queue_ids
        if client.get(f"/api/jobs/{j}").json()["state"] == "running")
    r.add("one at a time", "0 still running", str(running_at_once),
          running_at_once == 0)

    # ---- 7. restart mid-job, then resume ---------------------------------
    restart_check = do_check(client, video, LONG_SCRIPT)
    resume_id = client.post("/api/jobs", data={
        "video": video, "script": restart_check["script_path"],
        "draft": "true", "engine": "mock",
        "words": restart_check["words"],
        "total_takes": restart_check["total_takes"],
    }).json()["id"]

    # Let it get into the voice stage, then kill the manager the way a
    # crash or a reboot would.
    deadline = time.time() + 30
    while time.time() < deadline:
        if client.get(f"/api/jobs/{resume_id}").json()["state"] == "running":
            break
        time.sleep(0.1)
    server.manager.shutdown(wait=5)

    cache_before = {p.name: p.stat().st_mtime_ns
                    for p in server.cfg.tts_cache_dir.glob("*.wav")}
    r.add("cache written before crash", ">0 files",
          f"{len(cache_before)} files", len(cache_before) > 0)

    # A brand new manager, exactly as a fresh server start would build it.
    server.manager = JobManager(server.cfg, log=log)
    reread = server.manager.read(resume_id)
    r.add("interrupted job detected", "interrupted",
          reread.state if reread else "missing",
          bool(reread) and reread.state == "interrupted")

    server.manager.start()
    accepted = server.manager.resume(resume_id)
    r.add("resume accepted", "True", str(accepted), accepted)

    final = wait_for(client, resume_id, {"done", "failed", "cancelled"})
    r.add("resumed job finishes", "done", final["state"],
          final["state"] == "done")

    cache_after = {p.name: p.stat().st_mtime_ns
                   for p in server.cfg.tts_cache_dir.glob("*.wav")}
    reused = [n for n in cache_before if n in cache_after
              and cache_after[n] == cache_before[n]]
    r.add("resume reuses TTS cache", f"{len(cache_before)} untouched",
          f"{len(reused)} untouched", len(reused) == len(cache_before))

    # ---- 8. upload limit + voice endpoint --------------------------------
    server.cfg.max_upload_mb = 0
    res = client.post("/api/upload",
                      files={"video": ("big.mp4", b"x" * 4096, "video/mp4")})
    r.add("upload limit enforced", "413", str(res.status_code),
          res.status_code == 413)
    server.cfg.max_upload_mb = 2048

    voice = client.get("/api/voice")
    r.add("voice endpoint", "200", str(voice.status_code),
          voice.status_code == 200)

    page = client.get("/")
    r.add("page loads", "200 + html", f"{page.status_code}",
          page.status_code == 200 and "Voiceover Agent" in page.text)

    server.manager.shutdown(wait=3)

    print()
    print("=" * 84)
    print("  WEB APP TEST")
    print("=" * 84)
    ok = r.report()
    print()
    log.user("All tests passed." if ok else "Some tests failed.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
