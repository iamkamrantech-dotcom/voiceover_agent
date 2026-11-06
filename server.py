"""Voiceover Agent web app.

    venv\\Scripts\\python.exe server.py        (or double-click start.bat)

Flow: upload a video, review the storyboard scene by scene, fix the script
where it does not fit, then start the job. One job runs at a time; the rest
queue. Jobs survive a restart because every one owns a status.json.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from contextlib import asynccontextmanager
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

from fastapi import (
    Depends, FastAPI, Form, HTTPException, Request, UploadFile, File,
)
from fastapi.responses import (
    FileResponse, HTMLResponse, JSONResponse, StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from starlette.requests import ClientDisconnect

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from agent import log as agent_log  # noqa: E402
from agent import voice_profiles  # noqa: E402
from agent.config import Config  # noqa: E402
from agent.jobs import JobManager, estimate_seconds  # noqa: E402
from agent.pipeline import check_script, fit_row, script_check_table  # noqa: E402
from agent.scenes import detect_scenes  # noqa: E402
from agent.script_parser import parse_script  # noqa: E402
from agent.stats import collect_stats, machine_info  # noqa: E402

WEB = PROJECT_ROOT / "web"
DIST = WEB / "dist"
UPLOADS = PROJECT_ROOT / "outputs" / "uploads"

cfg = Config.load()
cfg.ensure_dirs()
UPLOADS.mkdir(parents=True, exist_ok=True)

log = agent_log.setup(cfg.log_dir, cfg.log_level, run_name="server")
manager = JobManager(cfg, log=log)
manager.start()

app = FastAPI(title="Voiceover Agent")
_sessions: set[str] = set()

# Settings the dashboard is allowed to write back to .env. Anything that
# could break a running job or point the app at missing files stays out.
WRITABLE_SETTINGS = {
    "TTS_ENGINE": str, "TTS_BEST_OF_N": int, "TTS_BEST_OF_N_DRAFT": int,
    "MUSIC_DB": float, "HOLD_MOTION": str, "MIN_SIMILARITY": float,
    "NARRATION_WPS": float, "PREVIEW_SUBTITLES": bool,
}


# --------------------------------------------------------------------------
# password gate
# --------------------------------------------------------------------------

def require_login(request: Request) -> None:
    if not cfg.app_password:
        return
    if request.cookies.get("va_session", "") not in _sessions:
        raise HTTPException(status_code=401, detail="Sign in first.")


def logged_in(request: Request) -> bool:
    return (not cfg.app_password
            or request.cookies.get("va_session", "") in _sessions)


@app.post("/api/login")
def login(password: str = Form(...)) -> JSONResponse:
    if not cfg.app_password or secrets.compare_digest(password,
                                                      cfg.app_password):
        token = secrets.token_urlsafe(24)
        _sessions.add(token)
        response = JSONResponse({"ok": True})
        response.set_cookie("va_session", token, httponly=True,
                            samesite="lax", max_age=60 * 60 * 24 * 14)
        return response
    raise HTTPException(status_code=401, detail="Wrong password.")


@app.get("/api/session")
def session(request: Request) -> dict:
    return {"required": bool(cfg.app_password), "signed_in": logged_in(request)}


# --------------------------------------------------------------------------
# uploads
# --------------------------------------------------------------------------

def _save_upload(upload: UploadFile, folder: Path, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    limit = cfg.max_upload_mb * 1024 * 1024
    written = 0
    with target.open("wb") as handle:
        while True:
            chunk = upload.file.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > limit:
                handle.close()
                target.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=413,
                    detail=f"File is too large (limit {cfg.max_upload_mb} MB).")
            handle.write(chunk)
    return target


def video_meta(path: Path) -> dict:
    proc = subprocess.run(
        [cfg.ffprobe_bin, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate",
         "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    try:
        data = json.loads(proc.stdout)
        stream = (data.get("streams") or [{}])[0]
        num, _, den = (stream.get("r_frame_rate") or "0/1").partition("/")
        fps = float(num) / float(den or 1)
        return {
            "width": stream.get("width"),
            "height": stream.get("height"),
            "fps": round(fps, 3),
            "duration": round(float(data.get("format", {}).get("duration", 0)), 2),
        }
    except Exception:
        return {}


@app.post("/api/upload", dependencies=[Depends(require_login)])
async def upload(video: UploadFile | None = File(None),
                 script: UploadFile | None = File(None),
                 music: UploadFile | None = File(None)) -> dict:
    folder = UPLOADS / secrets.token_hex(6)
    out: dict = {"id": folder.name}
    if video is not None:
        path = _save_upload(video, folder,
                            Path(video.filename or "video.mp4").name)
        out["video"] = str(path)
        out["name"] = path.name
        out["meta"] = video_meta(path)
    if script is not None:
        path = _save_upload(script, folder,
                            Path(script.filename or "script.txt").name)
        out["script"] = str(path)
        out["script_text"] = path.read_text(encoding="utf-8", errors="replace")
    if music is not None:
        path = _save_upload(music, folder,
                            Path(music.filename or "music.mp3").name)
        out["music"] = str(path)
        out["music_name"] = path.name
    return out


# --------------------------------------------------------------------------
# storyboard
# --------------------------------------------------------------------------

def _upload_folder(video: Path) -> Path:
    return video.parent


def _scene_cache(video: Path) -> Path:
    return _upload_folder(video) / "scenes.json"


@app.post("/api/storyboard", dependencies=[Depends(require_login)])
async def storyboard(video: str = Form(...),
                     script_text: str = Form("")) -> dict:
    video_path = Path(video)
    if not video_path.is_file():
        raise HTTPException(404, "Video not found.")

    folder = _upload_folder(video_path)
    keyframes = folder / "keyframes"
    detection = detect_scenes(video_path, cfg, keyframe_dir=keyframes, log=log)
    _scene_cache(video_path).write_text(
        json.dumps(detection.to_dict(), indent=2), encoding="utf-8")

    # Split whatever script we were given across the detected scenes, so the
    # storyboard opens already filled in rather than empty.
    texts = ["" for _ in detection.scenes]
    method = "none"
    warnings: list[str] = []
    if script_text.strip():
        split = parse_script(script_text, detection.scenes, cfg, log)
        method = split.method
        warnings = split.warnings
        for block in split.blocks:
            for scene_id in block.scene_ids:
                if 0 <= scene_id < len(texts):
                    # A block covering several scenes puts its text on the
                    # first one; the rest stay silent and the voice carries.
                    texts[scene_id] = block.text if scene_id == block.scene_ids[0] else ""

    return {
        "video": str(video_path),
        "method": method,
        "warnings": warnings,
        "duration": round(detection.duration, 2),
        "fps": detection.fps,
        "scenes": [
            {
                "index": scene.index,
                "start": round(scene.start, 2),
                "end": round(scene.end, 2),
                "duration": round(scene.duration, 2),
                "hold_end": round(scene.hold_end, 2),
                "transition_in": scene.transition_in.kind if scene.transition_in else "start",
                "transition_out": scene.transition_out.kind if scene.transition_out else "end",
                "keyframe": f"/api/storyboard/keyframe?video={video_path}&scene={scene.index}",
                "text": texts[scene.index] if scene.index < len(texts) else "",
            }
            for scene in detection.scenes
        ],
    }


@app.get("/api/storyboard/keyframe", dependencies=[Depends(require_login)])
def storyboard_keyframe(video: str, scene: int):
    folder = _upload_folder(Path(video)) / "keyframes"
    path = folder / f"scene_{scene:03d}.jpg"
    if not path.is_file():
        raise HTTPException(404, "Keyframe not found.")
    return FileResponse(path, media_type="image/jpeg")


@app.post("/api/storyboard/check", dependencies=[Depends(require_login)])
async def storyboard_check(request: Request) -> dict:
    """Fit for each scene, recalculated as the script is edited."""
    body = await _body(request)
    video_path = Path(body.get("video", ""))
    blocks = body.get("blocks") or []
    draft = bool(body.get("draft", True))

    cache = _scene_cache(video_path)
    if not cache.is_file():
        raise HTTPException(400, "Run the storyboard first.")
    scenes = json.loads(cache.read_text(encoding="utf-8"))["scenes"]

    spoken = [b for b in blocks if (b.get("text") or "").strip()]
    rows = []
    total_words = 0
    for position, block in enumerate(blocks):
        index = int(block.get("scene", position))
        if index >= len(scenes):
            continue
        scene = scenes[index]
        words = len((block.get("text") or "").split())
        total_words += words
        row = fit_row(index, [index], float(scene["start"]), float(scene["end"]),
                      words, position == len(blocks) - 1, cfg)
        rows.append({
            "scene": index,
            "words": words,
            "estimated": round(row.estimated, 2),
            "available": round(row.available, 2),
            "extension": round(row.extension, 2),
            "cut_words": row.cut_words,
            "state": ("empty" if words == 0 else
                      "over" if row.cut_words else
                      "tight" if row.extension > 0.05 else "fits"),
        })

    takes = cfg.best_of_n(draft=draft)
    duration = float(scenes[-1]["end"]) if scenes else 0.0
    return {
        "rows": rows,
        "words": total_words,
        "takes": takes,
        "total_takes": len(spoken) * takes,
        "estimate_seconds": round(estimate_seconds(
            cfg, manager.speed, total_words, takes, cfg.tts_engine, duration)),
    }


def write_marker_script(video: Path, blocks: list[dict]) -> Path:
    """Turn the storyboard into a script the splitter reproduces exactly.

    Scene markers are numbered from 1, and the marker number maps straight
    back to the scene index, so a scene left empty simply has no marker and
    the narration carries over it - which is what the storyboard showed.
    """
    lines = []
    for block in blocks:
        text = (block.get("text") or "").strip()
        if not text:
            continue
        lines.append(f"Scene {int(block['scene']) + 1}: {text}")
    path = _upload_folder(video) / "script.txt"
    path.write_text("\n\n".join(lines) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# the older one-shot check, still used by the CLI-style flow
# --------------------------------------------------------------------------

@app.post("/api/check", dependencies=[Depends(require_login)])
async def check(video: str = Form(...), script_text: str = Form(...),
                engine: str = Form(""), draft: bool = Form(True)) -> dict:
    video_path = Path(video)
    if not video_path.is_file():
        raise HTTPException(404, "Video not found.")

    script_path = _upload_folder(video_path) / "script.txt"
    script_path.write_text(script_text, encoding="utf-8")
    split, rows = check_script(video_path, script_path, cfg, log)

    words = sum(r.words for r in rows)
    takes = cfg.best_of_n(draft=draft)
    duration = rows[-1].end if rows else 0.0
    return {
        "method": split.method,
        "script_path": str(script_path),
        "warnings": split.warnings,
        "words": words,
        "estimate_seconds": round(estimate_seconds(
            cfg, manager.speed, words, takes,
            (engine or cfg.tts_engine).lower(), duration)),
        "total_takes": sum(1 for r in rows if r.words) * takes,
        "takes": takes,
        "rows": [
            {"block": r.block, "scenes": r.scene_ids,
             "start": round(r.start, 2), "end": round(r.end, 2),
             "words": r.words, "estimated": round(r.estimated, 1),
             "available": round(r.available, 1),
             "extension": round(r.extension, 1), "cut_words": r.cut_words}
            for r in rows
        ],
    }


# --------------------------------------------------------------------------
# jobs
# --------------------------------------------------------------------------

async def _body(request: Request) -> dict:
    """Accept JSON or form data.

    The dashboard posts JSON (it sends nested blocks); the original API
    took form fields and callers of it must keep working.
    """
    try:
        if "application/json" in (request.headers.get("content-type") or ""):
            return await request.json()
        form = await request.form()
    except ClientDisconnect:
        # The dashboard aborts in-flight fit checks as you keep typing.
        # That is normal, not an error worth a stack trace.
        raise HTTPException(status_code=499, detail="Client disconnected.")
    out: dict = {}
    for key, value in form.multi_items():
        if isinstance(value, str) and value.lower() in ("true", "false"):
            out[key] = value.lower() == "true"
        else:
            out[key] = value
    return out


@app.post("/api/jobs", dependencies=[Depends(require_login)])
async def create_job(request: Request) -> dict:
    body = await _body(request)
    video_path = Path(body.get("video", ""))
    if not video_path.is_file():
        raise HTTPException(404, "Video not found.")

    blocks = body.get("blocks")
    if blocks:
        script_path = write_marker_script(video_path, blocks)
    else:
        script_path = Path(body.get("script", ""))
    if not script_path.is_file():
        raise HTTPException(404, "Script not found.")

    draft = bool(body.get("draft", True))
    params = {
        "video": str(video_path),
        "script": str(script_path),
        "music": body.get("music") or None,
        "engine": (body.get("engine") or cfg.tts_engine).lower(),
        "draft": draft,
        "keep_original_audio": bool(body.get("keep_original_audio")),
        "burn_subs": bool(body.get("burn_subs")),
        "words": int(body.get("words") or 0),
        "takes": cfg.best_of_n(draft=draft),
        "total_takes": int(body.get("total_takes") or 0),
        "estimate_seconds": float(body.get("estimate_seconds") or 0.0),
    }
    return {"id": manager.submit(video_path.stem, params)}


@app.get("/api/jobs", dependencies=[Depends(require_login)])
def list_jobs() -> dict:
    return {**manager.snapshot(),
            "jobs": [status.to_dict() for status in manager.all_jobs()[:100]]}


@app.get("/api/jobs/{job_id}", dependencies=[Depends(require_login)])
def job_status(job_id: str) -> dict:
    status = manager.read(job_id)
    if status is None:
        raise HTTPException(404, "Job not found.")
    return status.to_dict()


@app.post("/api/jobs/{job_id}/cancel", dependencies=[Depends(require_login)])
def cancel_job(job_id: str) -> dict:
    return {"ok": manager.cancel(job_id)}


@app.post("/api/jobs/{job_id}/resume", dependencies=[Depends(require_login)])
def resume_job(job_id: str) -> dict:
    return {"ok": manager.resume(job_id)}


@app.post("/api/jobs/{job_id}/duplicate", dependencies=[Depends(require_login)])
def duplicate_job(job_id: str) -> dict:
    status = manager.read(job_id)
    if status is None:
        raise HTTPException(404, "Job not found.")
    params = dict(status.params)
    if not Path(params.get("video", "")).is_file():
        raise HTTPException(400, "The original video is no longer on disk.")
    return {"id": manager.submit(status.name, params)}


@app.delete("/api/jobs/{job_id}", dependencies=[Depends(require_login)])
def delete_job(job_id: str) -> dict:
    status = manager.read(job_id)
    if status is None:
        raise HTTPException(404, "Job not found.")
    if status.state in ("running", "queued"):
        manager.cancel(job_id)
    shutil.rmtree(manager.job_dir(job_id), ignore_errors=True)
    return {"ok": True}


@app.get("/api/jobs/{job_id}/timeline", dependencies=[Depends(require_login)])
def job_timeline(job_id: str) -> dict:
    path = manager.job_dir(job_id) / "timeline.json"
    if not path.is_file():
        raise HTTPException(404, "The timeline is not ready yet.")
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/api/jobs/{job_id}/file/{kind}", dependencies=[Depends(require_login)])
def job_file(job_id: str, kind: str):
    status = manager.read(job_id)
    if status is None:
        raise HTTPException(404, "Job not found.")
    folder = manager.job_dir(job_id)
    choices = {
        "video": folder / "final.mp4",
        "subtitles": Path(status.result.get("subtitles") or ""),
        "report": Path(status.result.get("report") or ""),
        "timeline": folder / "timeline.json",
    }
    path = choices.get(kind)
    if path is None or not path.is_file():
        raise HTTPException(404, "File not found.")
    media = "video/mp4" if kind == "video" else "application/octet-stream"
    return FileResponse(path, media_type=media,
                        filename=None if kind == "video" else path.name)


# --------------------------------------------------------------------------
# live updates
# --------------------------------------------------------------------------

async def _event_stream(request: Request, job_id: str | None):
    listener = manager.subscribe()
    try:
        first = {"type": "snapshot", **manager.snapshot()}
        if job_id:
            status = manager.read(job_id)
            first["job"] = status.to_dict() if status else None
        yield f"data: {json.dumps(first)}\n\n"

        while True:
            if await request.is_disconnected():
                break
            try:
                payload = listener.get(timeout=0.5)
            except Exception:
                # Nothing happened; a comment keeps the connection warm
                # without pretending there was an update.
                yield ": keepalive\n\n"
                await asyncio.sleep(0)
                continue
            if job_id and payload.get("job", {}).get("id") != job_id:
                # Still send queue counts so the top bar stays right.
                payload = {"type": "snapshot", **manager.snapshot()}
            yield f"data: {json.dumps(payload)}\n\n"
            await asyncio.sleep(0)
    finally:
        manager.unsubscribe(listener)


@app.get("/api/events")
async def events(request: Request):
    if not logged_in(request):
        raise HTTPException(401, "Sign in first.")
    return StreamingResponse(
        _event_stream(request, None), media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request):
    if not logged_in(request):
        raise HTTPException(401, "Sign in first.")
    return StreamingResponse(
        _event_stream(request, job_id), media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


# --------------------------------------------------------------------------
# overview
# --------------------------------------------------------------------------

@app.get("/api/stats", dependencies=[Depends(require_login)])
def stats() -> dict:
    return collect_stats(cfg).to_dict()


@app.get("/api/machine", dependencies=[Depends(require_login)])
def machine() -> dict:
    return machine_info(cfg)


# --------------------------------------------------------------------------
# voice profiles
# --------------------------------------------------------------------------

@app.get("/api/voice", dependencies=[Depends(require_login)])
def voice_state() -> dict:
    return {
        "active": voice_profiles.active_info(),
        "fallback": str(cfg.voice_sample),
        "profiles": [p.to_dict() for p in voice_profiles.list_profiles()],
    }


@app.get("/api/voice/audio", dependencies=[Depends(require_login)])
def voice_audio(path: str):
    target = Path(path).resolve()
    allowed = [(PROJECT_ROOT / "voices").resolve(),
               (PROJECT_ROOT / "input").resolve()]
    # Only ever serve audio from the voice folders, never an arbitrary path.
    if not any(str(target).startswith(str(root)) for root in allowed):
        raise HTTPException(403, "That file cannot be served.")
    if not target.is_file():
        raise HTTPException(404, "File not found.")
    return FileResponse(target)


@app.post("/api/voice/prepare", dependencies=[Depends(require_login)])
async def voice_prepare(recording: UploadFile = File(...),
                        name: str = Form(""),
                        engine: str = Form("")) -> dict:
    folder = UPLOADS / f"voice_{secrets.token_hex(4)}"
    saved = _save_upload(recording, folder,
                         Path(recording.filename or "voice.wav").name)
    try:
        profile = voice_profiles.prepare_profile(
            saved, cfg, name=name or None, engine=engine or cfg.tts_engine,
            log=log)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return profile.to_dict()


@app.post("/api/voice/activate", dependencies=[Depends(require_login)])
def voice_activate(profile: str = Form(...),
                   candidate: str = Form(...)) -> dict:
    try:
        data = voice_profiles.set_active(profile, candidate, log=log)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
    global cfg
    cfg = Config.load()  # pick the new reference up immediately
    manager.cfg = cfg
    return data


# --------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------

@app.get("/api/settings", dependencies=[Depends(require_login)])
def get_settings() -> dict:
    return {
        "writable": {
            "TTS_ENGINE": cfg.tts_engine,
            "TTS_BEST_OF_N": cfg.tts_best_of_n,
            "TTS_BEST_OF_N_DRAFT": cfg.tts_best_of_n_draft,
            "MUSIC_DB": cfg.music_db,
            "HOLD_MOTION": cfg.hold_motion,
            "MIN_SIMILARITY": cfg.min_similarity,
            "NARRATION_WPS": cfg.narration_wps,
            "PREVIEW_SUBTITLES": cfg.preview_subtitles,
        },
        "readonly": {
            "voice_sample": str(cfg.voice_sample),
            "voice_target": str(cfg.voice_target),
            "device": cfg.device,
            "whisper_model": cfg.whisper_model,
            "target_lufs": cfg.target_lufs,
            "max_speedup": cfg.max_speedup,
            "max_video_slowdown": cfg.max_video_slowdown,
            "min_scene_seconds": cfg.min_scene_seconds,
            "max_upload_mb": cfg.max_upload_mb,
            "output_dir": str(cfg.output_dir),
        },
    }


@app.put("/api/settings", dependencies=[Depends(require_login)])
async def put_settings(request: Request) -> dict:
    body = await request.json()
    env_path = PROJECT_ROOT / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines() \
        if env_path.is_file() else []

    for key, raw in body.items():
        if key not in WRITABLE_SETTINGS:
            raise HTTPException(400, f"{key} cannot be changed here.")
        kind = WRITABLE_SETTINGS[key]
        try:
            value = ("true" if bool(raw) else "false") if kind is bool \
                else str(kind(raw))
        except (TypeError, ValueError):
            raise HTTPException(400, f"{key} has an invalid value.")

        for i, line in enumerate(lines):
            if line.split("=", 1)[0].strip() == key:
                lines[i] = f"{key}={value}"
                break
        else:
            lines.append(f"{key}={value}")
        import os

        os.environ[key] = value

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    global cfg
    cfg = Config.load()
    manager.cfg = cfg
    return get_settings()


@app.get("/api/config", dependencies=[Depends(require_login)])
def config_view() -> dict:
    return {
        "engine": cfg.tts_engine,
        "best_of_n": cfg.tts_best_of_n,
        "best_of_n_draft": cfg.tts_best_of_n_draft,
        "voice_sample": str(cfg.voice_sample),
        "max_upload_mb": cfg.max_upload_mb,
        "device": cfg.device,
    }


# --------------------------------------------------------------------------
# static front end
# --------------------------------------------------------------------------

if (DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")


@app.middleware("http")
async def no_cache_app(request: Request, call_next):
    """Never cache the app shell or its bundle.

    This is a local, single-user app that gets rebuilt often; a stale
    bundle quietly running old behaviour costs far more than refetching
    a few hundred KB over localhost.
    """
    response = await call_next(request)
    if request.url.path.startswith(("/assets", "/static")) \
            or request.url.path == "/":
        response.headers["Cache-Control"] = "no-store, must-revalidate"
    return response


@app.get("/{full_path:path}", include_in_schema=False)
def spa(full_path: str):
    """Serve the built app, letting the router own every non-API route."""
    if full_path.startswith("api/"):
        raise HTTPException(404, "Not found.")

    candidate = DIST / full_path
    if full_path and candidate.is_file():
        return FileResponse(candidate)

    index = DIST / "index.html"
    if index.is_file():
        return FileResponse(index, media_type="text/html")
    return HTMLResponse(
        "<h1>Voiceover Agent</h1><p>The dashboard has not been built yet. "
        "Run: <code>tools\\node\\npm.cmd --prefix web-ui run build</code></p>",
        status_code=200,
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    manager.shutdown()


app.router.lifespan_context = lifespan


def main() -> int:
    import uvicorn

    url = f"http://{cfg.api_host}:{cfg.api_port}"
    log.user(f"Server running at {url}")
    if "--no-browser" not in sys.argv:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    uvicorn.run(app, host=cfg.api_host, port=cfg.api_port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
