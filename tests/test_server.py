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
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from agent import log as agent_log          # noqa: E402
from agent import voice_profiles            # noqa: E402
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

    # ---- 9. settings: grouped, with defaults and reset -------------------
    settings = client.get("/api/settings").json()
    groups = settings.get("groups", [])
    r.add("settings grouped", "5 sections", f"{len(groups)} sections",
          len(groups) == 5)
    every = [s for g in groups for s in g["settings"]]
    described = [s for s in every if s["help"] and s["label"]]
    r.add("settings explained", "all have help",
          f"{len(described)}/{len(every)}", len(described) == len(every))
    with_default = sum(1 for s in every if s["default"] is not None)
    r.add("settings carry defaults", "all have one",
          f"{with_default}/{len(every)}", with_default == len(every))

    saved = client.put("/api/settings", json={"values": {"NARRATION_WPS": 3.1}})
    r.add("setting saved", "200", str(saved.status_code),
          saved.status_code == 200)
    wps = next(s for s in
               [x for g in saved.json()["groups"] for x in g["settings"]]
               if s["key"] == "NARRATION_WPS")
    r.add("changed value marked", "differs from default",
          f"{wps['value']} vs {wps['default']}",
          wps["differs"] and abs(float(wps["value"]) - 3.1) < 1e-6)

    reset = client.put("/api/settings", json={"reset": ["NARRATION_WPS"]})
    wps = next(s for s in
               [x for g in reset.json()["groups"] for x in g["settings"]]
               if s["key"] == "NARRATION_WPS")
    r.add("setting reset to default", "2.5", str(wps["value"]),
          not wps["differs"] and abs(float(wps["value"]) - 2.5) < 1e-6)

    bad = client.put("/api/settings", json={"values": {"MAX_SPEEDUP": 9.0}})
    r.add("out-of-range refused", "400 + reason", str(bad.status_code),
          bad.status_code == 400 and "above" in bad.json()["detail"])

    bad = client.put("/api/settings", json={"values": {"OUTPUT_DIR": "/tmp"}})
    r.add("fixed setting refused", "400", str(bad.status_code),
          bad.status_code == 400)

    password = next(s for s in every if s["key"] == "APP_PASSWORD")
    r.add("password never returned", "a yes/no only", str(password["value"]),
          isinstance(password["value"], bool))

    # ---- 10. the pipeline's stage list and the job log -------------------
    config = client.get("/api/config").json()
    r.add("stage list served", "8+ stages", f"{len(config.get('stages', []))}",
          len(config.get("stages", [])) >= 8)

    job_log = client.get(f"/api/jobs/{job_id}/log").json()
    r.add("job log readable", "lines recorded",
          f"{len(job_log.get('lines', []))} lines",
          len(job_log.get("lines", [])) > 0)
    levels = {line["level"] for line in job_log.get("lines", [])}
    r.add("log lines are levelled", "known levels", ", ".join(sorted(levels))[:28],
          levels and levels <= {"debug", "progress", "warning", "error"})

    # ---- 11. voice profiles: rename and the active-profile guard ---------
    voices = PROJECT_ROOT / "voices"
    made = voices / "test_profile_tmp"
    made.mkdir(parents=True, exist_ok=True)
    (made / "ref_1.wav").write_bytes(b"RIFF0000WAVE")
    (made / "profile.json").write_text(json.dumps({
        "name": "test_profile_tmp", "created": "2026-01-01T00:00:00",
        "source": str(made / "source.wav"),
        "candidates": [{"name": "ref_1.wav", "path": str(made / "ref_1.wav"),
                        "start": 0.0, "end": 10.0, "score": 1.0,
                        "similarity": None, "sample": None}],
        "best": "ref_1.wav",
    }), encoding="utf-8")

    renamed = client.patch("/api/voice/test_profile_tmp",
                           json={"name": "test profile renamed"})
    r.add("voice renamed", "200", str(renamed.status_code),
          renamed.status_code == 200)
    moved = voices / "test profile renamed"
    inner = json.loads((moved / "profile.json").read_text(encoding="utf-8")) \
        if moved.is_dir() else {}
    r.add("rename rewrites paths", "points at the new folder",
          Path(inner.get("candidates", [{}])[0].get("path", "")).parent.name[:28],
          moved.is_dir()
          and Path(inner["candidates"][0]["path"]).parent == moved)

    bad = client.patch("/api/voice/test profile renamed", json={"name": "a/b"})
    r.add("bad name refused", "400", str(bad.status_code), bad.status_code == 400)

    active_before = voice_profiles.active_info()
    voice_profiles.set_active("test profile renamed", "ref_1.wav")
    guard = client.delete("/api/voice/test profile renamed")
    r.add("active voice not deletable", "400 + reason", str(guard.status_code),
          guard.status_code == 400 and "in use" in guard.json()["detail"])

    if active_before:
        voice_profiles.set_active(active_before["profile"],
                                  active_before["candidate"])
    else:
        voice_profiles.active_file().unlink(missing_ok=True)
    removed = client.delete("/api/voice/test profile renamed")
    r.add("voice deleted", "200 + folder gone", str(removed.status_code),
          removed.status_code == 200 and not moved.exists())

    # ---- 12. two threads writing one status.json -------------------------
    # The HTTP thread writes a job's status when it is submitted and
    # renumbered; the worker writes the same file as it reports progress.
    # They used to share one 'status.tmp', and on Windows the second
    # os.replace fails with WinError 5. That is not theoretical: it killed
    # a real run, the job came off the queue before it crashed, and it
    # then sat on "queued" forever with nothing left to run it.
    manager = server.manager
    racer = manager.read(job_id)
    errors: list[str] = []

    def hammer() -> None:
        for _ in range(60):
            try:
                manager.write(racer)
            except Exception as exc:        # noqa: BLE001 - that is the point
                errors.append(f"{type(exc).__name__}: {exc}")

    # Readers matter as much as writers. Python opens files on Windows
    # without FILE_SHARE_DELETE, so an open SSE stream reading status.json
    # is on its own enough to make the writer's os.replace fail - which is
    # exactly how this broke the second time, after unique temp names had
    # already fixed the writer-versus-writer half.
    def reader() -> None:
        for _ in range(60):
            try:
                manager.read(job_id)
            except Exception as exc:        # noqa: BLE001 - that is the point
                errors.append(f"read {type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=hammer) for _ in range(4)]
    threads += [threading.Thread(target=reader) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    r.add("concurrent status writes", "no errors",
          errors[0][:28] if errors else "240 writes, 240 reads", not errors)
    after = manager.read(job_id)
    r.add("status survives the race", "still valid json",
          after.state if after else "unreadable",
          after is not None and after.id == job_id)
    leftovers = list(manager.job_dir(job_id).glob("status.*.tmp"))
    r.add("no temp files left behind", "0 temps", f"{len(leftovers)} temps",
          not leftovers)

    # ---- 13. a crash in the worker does not strand the job ---------------
    # A job is popped off the queue before it runs, so if the run raises
    # there is nothing left to pick it up. It must say so rather than
    # sit on "queued", which is indistinguishable from a busy machine.
    stranded = manager.submit("crash test", {"kind": "video", "video": "",
                                             "script": ""})
    deadline = time.time() + 30
    state = ""
    while time.time() < deadline:
        current = manager.read(stranded)
        state = current.state if current else ""
        if state in ("failed", "done", "cancelled"):
            break
        time.sleep(0.2)
    r.add("crashed job reports failure", "failed", state or "still queued",
          state == "failed")
    r.add("worker survives a crash", "thread alive",
          "alive" if manager._worker and manager._worker.is_alive() else "dead",
          bool(manager._worker and manager._worker.is_alive()))

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
