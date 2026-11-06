"""Per-block revision tests, mock engine, against a generated test video.

The claim this file exists to prove is the expensive one: changing one
block must not regenerate the others. On this CPU a block is minutes, so a
revision that quietly re-synthesised everything would be no better than
re-running the job - and the difference is invisible from the outside,
because both produce a correct video.

So the test watches the TTS cache itself. Every wav's modification time is
recorded before the change and compared after: choosing a different take
must touch nothing at all, and editing one block's text must add exactly
the takes for that block and leave every other file byte-identical.

    venv\\Scripts\\python.exe tests\\test_revise.py
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient      # noqa: E402

from agent import log as agent_log             # noqa: E402
from agent import versions                     # noqa: E402
from agent.config import Config                # noqa: E402
from agent.jobs import JobManager              # noqa: E402

MEDIA = PROJECT_ROOT / "tests" / "media"

SCRIPT = (
    "Welcome back to the channel today. "
    "This first place is somewhere almost nobody has ever visited. "
    "The second location is stranger still and far harder to reach. "
    "Our third stop sits high above the clouds all year round."
)
NEW_TEXT = "A completely different sentence now sits on this picture instead."


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
        time.sleep(0.3)
    return last


def cache_state(folder: Path) -> dict[str, int]:
    """Every cached take and when it was last written."""
    return {p.name: p.stat().st_mtime_ns for p in folder.glob("*.wav")}


def main() -> int:
    cfg = Config.load()
    log = agent_log.setup(cfg.log_dir, cfg.log_level, run_name="test_revise")

    if not (MEDIA / "cuts.mp4").is_file():
        log.user("Test videos not found. Run tests/make_test_videos.py first.")
        return 1

    sandbox = PROJECT_ROOT / "outputs" / "_revise_test"
    if sandbox.exists():
        shutil.rmtree(sandbox, ignore_errors=True)
    sandbox.mkdir(parents=True, exist_ok=True)

    import server

    server.cfg.output_dir = sandbox
    server.cfg.work_dir = sandbox / "_work"
    server.cfg.tts_cache_dir = sandbox / "_tts_cache"
    server.cfg.tts_engine = "mock"
    # Three takes, so there is something to choose between.
    server.cfg.tts_best_of_n = 3
    server.cfg.tts_best_of_n_draft = 3
    server.cfg.ensure_dirs()
    server.manager.shutdown(wait=2)
    server.manager = JobManager(server.cfg, log=log)
    server.manager.start()
    server.UPLOADS = sandbox / "uploads"
    cache = server.cfg.tts_cache_dir

    r = Results()
    client = TestClient(server.app)

    # ---- 1. a finished job to revise ------------------------------------
    with (MEDIA / "cuts.mp4").open("rb") as handle:
        video = client.post(
            "/api/upload",
            files={"video": ("cuts.mp4", handle, "video/mp4")}
        ).json()["video"]

    created = client.post("/api/jobs", json={
        "video": video, "script_text": SCRIPT, "draft": True,
        "blocks": [{"scene": i, "text": part.strip() + "."}
                   for i, part in enumerate(SCRIPT.split(". ")) if part.strip()],
    })
    job_id = created.json()["id"]
    done = wait_for(client, job_id, ("done", "failed"))
    r.add("first job completes", "done", done["state"], done["state"] == "done")

    job_dir = server.manager.job_dir(job_id)
    r.add("scenes saved", "scenes.json", "yes" if (job_dir / "scenes.json").is_file()
          else "missing", (job_dir / "scenes.json").is_file())
    r.add("split saved", "split.json", "yes" if (job_dir / "split.json").is_file()
          else "missing", (job_dir / "split.json").is_file())

    blocks = client.get(f"/api/jobs/{job_id}/blocks").json()
    spoken = [b for b in blocks["blocks"] if not b["silent"]]
    r.add("takes recorded", "every take kept",
          f"{len(spoken[0]['takes'])} per block",
          bool(spoken) and len(spoken[0]["takes"]) == 3)

    # The timeline must not point at files the job deleted on its way out.
    timeline = client.get(f"/api/jobs/{job_id}/timeline").json()
    wavs = [Path(b["wav"]) for b in timeline["blocks"] if b.get("wav")]
    alive = [p for p in wavs if p.is_file()]
    r.add("timeline audio survives", f"{len(wavs)} present",
          f"{len(alive)} present", len(alive) == len(wavs) and bool(wavs))

    r.add("waveform measured", "peaks present",
          "yes" if client.get(f"/api/jobs/{job_id}/waveform").status_code == 200
          else "no",
          client.get(f"/api/jobs/{job_id}/waveform").status_code == 200)

    first_versions = client.get(f"/api/jobs/{job_id}/versions").json()["versions"]
    r.add("v1 recorded", "1 version", f"{len(first_versions)} version(s)",
          len(first_versions) == 1 and first_versions[0]["version"] == 1)

    # ---- 2. choose a different take: nothing may be synthesised ----------
    target = spoken[0]
    chosen_seed = target["seed"]
    other = next((t for t in target["takes"] if t["seed"] != chosen_seed), None)
    r.add("another take exists", "a second take",
          f"seed {other['seed']}" if other else "none", other is not None)

    before = cache_state(cache)
    plan = client.post(f"/api/jobs/{job_id}/revise/plan", json={
        "block": target["block"], "action": "choose_take", "seed": other["seed"],
    }).json()
    r.add("plan: no generation", "0 takes to make",
          f"{plan.get('takes_to_make')} takes", plan.get("takes_to_make") == 0)

    res = client.post(f"/api/jobs/{job_id}/revise", json={
        "block": target["block"], "action": "choose_take", "seed": other["seed"],
    })
    r.add("revision accepted", "200", str(res.status_code), res.status_code == 200)
    state = wait_for(client, job_id, ("done", "failed"))
    r.add("revision completes", "done", state["state"], state["state"] == "done")

    after = cache_state(cache)
    r.add("choosing a take adds nothing", "0 new files",
          f"{len(set(after) - set(before))} new",
          set(after) == set(before))
    r.add("choosing a take touches nothing", "all untouched",
          f"{sum(1 for k in before if after.get(k) != before[k])} touched",
          all(after.get(k) == v for k, v in before.items()))

    blocks2 = client.get(f"/api/jobs/{job_id}/blocks").json()["blocks"]
    now = next(b for b in blocks2 if b["block"] == target["block"])
    r.add("chosen take changed", f"seed {other['seed']}", f"seed {now['seed']}",
          now["seed"] == other["seed"])

    v2 = client.get(f"/api/jobs/{job_id}/versions").json()["versions"]
    r.add("v2 created", "2 versions", f"{len(v2)} versions", len(v2) == 2)
    r.add("v2 has its own video", "playable",
          "yes" if client.get(f"/api/jobs/{job_id}/file/video?v=2").status_code == 200
          else "no",
          client.get(f"/api/jobs/{job_id}/file/video?v=2").status_code == 200)
    r.add("v1 still playable", "200",
          str(client.get(f"/api/jobs/{job_id}/file/video?v=1").status_code),
          client.get(f"/api/jobs/{job_id}/file/video?v=1").status_code == 200)
    r.add("v2 notes the change", "mentions the block",
          (v2[1]["note"] or "")[:28],
          f"Block {target['block'] + 1}" in (v2[1]["note"] or ""))
    r.add("v2 passed quality checks", "passed",
          str(v2[1]["qc_passed"]), bool(v2[1]["qc_passed"]))

    # ---- 3. edit one block's text: only that block is generated ----------
    before = cache_state(cache)
    res = client.post(f"/api/jobs/{job_id}/revise", json={
        "block": target["block"], "action": "edit_text", "text": NEW_TEXT,
    })
    r.add("text edit accepted", "200", str(res.status_code), res.status_code == 200)
    state = wait_for(client, job_id, ("done", "failed"))
    r.add("text edit completes", "done", state["state"], state["state"] == "done")

    after = cache_state(cache)
    added = set(after) - set(before)
    r.add("only the new text is made", "3 new takes", f"{len(added)} new",
          len(added) == 3)
    r.add("other blocks untouched", "all untouched",
          f"{sum(1 for k in before if after.get(k) != before[k])} touched",
          all(after.get(k) == v for k, v in before.items()))

    blocks3 = client.get(f"/api/jobs/{job_id}/blocks").json()["blocks"]
    edited = next(b for b in blocks3 if b["block"] == target["block"])
    r.add("block text updated", "new text", edited["text"][:28],
          edited["text"] == NEW_TEXT)

    v3 = client.get(f"/api/jobs/{job_id}/versions").json()["versions"]
    r.add("v3 created", "3 versions", f"{len(v3)} versions", len(v3) == 3)
    r.add("every version playable", "3 files",
          f"{sum(1 for v in v3 if (job_dir / (v['video'] or 'x')).is_file())} files",
          all((job_dir / (v["video"] or "x")).is_file() for v in v3))
    r.add("every version has a report", "3 reports",
          f"{sum(1 for v in v3 if v['report'])} reports",
          all(v["report"] for v in v3))

    qc = client.get(f"/api/jobs/{job_id}/qc").json()
    r.add("v3 checks still pass", "passed", str(qc.get("passed")),
          bool(qc.get("passed")))
    r.add("v3 duration matches plan", "within a frame",
          f"{abs(qc['duration_actual'] - qc['duration_expected']):.3f}s",
          abs(qc["duration_actual"] - qc["duration_expected"]) < 0.08)

    # The split must now agree with the audio, or the NEXT revision would
    # start from text that was never spoken.
    split = json.loads((job_dir / "split.json").read_text(encoding="utf-8"))
    stored = next(b["text"] for b in split["blocks"]
                  if b["index"] == target["block"])
    r.add("script record updated", "new text", stored[:28], stored == NEW_TEXT)

    # ---- 4. refusals say why --------------------------------------------
    bad = client.post(f"/api/jobs/{job_id}/revise", json={
        "block": target["block"], "action": "edit_text", "text": "   ",
    })
    r.add("empty text refused", "400 + reason", str(bad.status_code),
          bad.status_code == 400 and "empty" in bad.json()["detail"].lower())

    bad = client.post(f"/api/jobs/{job_id}/revise", json={
        "block": 999, "action": "choose_take", "seed": 1,
    })
    r.add("unknown block refused", "400", str(bad.status_code),
          bad.status_code == 400)

    bad = client.post(f"/api/jobs/{job_id}/revise", json={
        "block": target["block"], "action": "choose_take", "seed": 999999,
    })
    r.add("unknown take refused", "400", str(bad.status_code),
          bad.status_code == 400)

    # ---- 5. versions can be removed, but never the last -----------------
    gone = client.delete(f"/api/jobs/{job_id}/versions/2")
    r.add("version deleted", "200", str(gone.status_code), gone.status_code == 200)
    left = client.get(f"/api/jobs/{job_id}/versions").json()["versions"]
    r.add("history shrinks", "2 versions", f"{len(left)} versions", len(left) == 2)
    kept = versions.version_dir(job_dir, 3) / "final.mp4"
    r.add("other versions survive", "v3 intact",
          "yes" if kept.is_file() else "gone", kept.is_file())
    refused = client.delete(f"/api/jobs/{job_id}/versions/1")
    r.add("v1 cannot be deleted", "400", str(refused.status_code),
          refused.status_code == 400)

    # ---- 6. a take can be played back ------------------------------------
    audio = client.get(f"/api/jobs/{job_id}/take",
                       params={"block": target["block"], "seed": edited["seed"]})
    r.add("take plays back", "wav returned", f"{len(audio.content)} bytes",
          audio.status_code == 200 and len(audio.content) > 100)
    stray = client.get(f"/api/jobs/{job_id}/take",
                       params={"block": target["block"], "seed": -1})
    r.add("unknown take not served", "404", str(stray.status_code),
          stray.status_code == 404)

    server.manager.shutdown(wait=3)

    print()
    print("=" * 84)
    print("  REVISION TEST")
    print("=" * 84)
    ok = r.report()
    log.user("All tests passed." if ok else "Some tests failed.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
