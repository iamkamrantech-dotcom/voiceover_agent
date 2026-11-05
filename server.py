"""Voiceover Agent web app.

    venv\\Scripts\\python.exe server.py        (or just double-click start.bat)

Flow: upload a video and a script, get the fit check in seconds, fix the
script right there, then start the job. One job runs at a time; the rest
queue. Jobs survive a restart because every one owns a status.json.
"""

from __future__ import annotations

import json
import secrets
import shutil
import sys
import webbrowser
from pathlib import Path

from fastapi import (
    Depends, FastAPI, Form, HTTPException, Request, UploadFile, File,
)
from fastapi.responses import (
    FileResponse, HTMLResponse, JSONResponse, RedirectResponse,
)
from fastapi.staticfiles import StaticFiles

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from agent import log as agent_log  # noqa: E402
from agent.config import Config  # noqa: E402
from agent.jobs import JobManager, estimate_seconds  # noqa: E402
from agent.pipeline import check_script  # noqa: E402
from agent import voice_profiles  # noqa: E402

WEB = PROJECT_ROOT / "web"
UPLOADS = PROJECT_ROOT / "outputs" / "uploads"

cfg = Config.load()
cfg.ensure_dirs()
UPLOADS.mkdir(parents=True, exist_ok=True)

log = agent_log.setup(cfg.log_dir, cfg.log_level, run_name="server")
manager = JobManager(cfg, log=log)
manager.start()

app = FastAPI(title="Voiceover Agent")
_sessions: set[str] = set()


# --------------------------------------------------------------------------
# very small password gate
# --------------------------------------------------------------------------

def require_login(request: Request) -> None:
    if not cfg.app_password:
        return
    token = request.cookies.get("va_session", "")
    if token not in _sessions:
        raise HTTPException(status_code=401, detail="Pehle login karein.")


@app.get("/login", response_class=HTMLResponse)
def login_page() -> HTMLResponse:
    return HTMLResponse((WEB / "login.html").read_text(encoding="utf-8"))


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
    raise HTTPException(status_code=401, detail="Password ghalat hai.")


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    if cfg.app_password and request.cookies.get("va_session", "") not in _sessions:
        return HTMLResponse((WEB / "login.html").read_text(encoding="utf-8"))
    return HTMLResponse((WEB / "index.html").read_text(encoding="utf-8"))


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
                    detail=f"File bohat bari hai (hadd {cfg.max_upload_mb} MB).")
            handle.write(chunk)
    return target


@app.post("/api/upload", dependencies=[Depends(require_login)])
async def upload(video: UploadFile | None = File(None),
                 script: UploadFile | None = File(None),
                 music: UploadFile | None = File(None)) -> dict:
    folder = UPLOADS / secrets.token_hex(6)
    out: dict = {"id": folder.name}
    if video is not None:
        out["video"] = str(_save_upload(video, folder,
                                        Path(video.filename or "video.mp4").name))
    if script is not None:
        path = _save_upload(script, folder,
                            Path(script.filename or "script.txt").name)
        out["script"] = str(path)
        out["script_text"] = path.read_text(encoding="utf-8", errors="replace")
    if music is not None:
        out["music"] = str(_save_upload(music, folder,
                                        Path(music.filename or "music.mp3").name))
    return out


# --------------------------------------------------------------------------
# step A: the fast fit check
# --------------------------------------------------------------------------

@app.post("/api/check", dependencies=[Depends(require_login)])
async def check(video: str = Form(...), script_text: str = Form(...),
                engine: str = Form(""), draft: bool = Form(True)) -> dict:
    video_path = Path(video)
    if not video_path.is_file():
        raise HTTPException(404, "Video nahi mili.")

    folder = video_path.parent
    script_path = folder / "script.txt"
    script_path.write_text(script_text, encoding="utf-8")

    split, rows = check_script(video_path, script_path, cfg, log)

    words = sum(r.words for r in rows)
    engine_name = (engine or cfg.tts_engine).lower()
    takes = cfg.best_of_n(draft=draft)
    video_seconds = rows[-1].end if rows else 0.0
    estimate = estimate_seconds(cfg, manager.speed, words, takes,
                                engine_name, video_seconds)

    return {
        "method": split.method,
        "script_path": str(script_path),
        "warnings": split.warnings,
        "words": words,
        "estimate_seconds": round(estimate),
        "total_takes": sum(1 for r in rows if r.words) * takes,
        "takes": takes,
        "rows": [
            {
                "block": r.block, "scenes": r.scene_ids,
                "start": round(r.start, 2), "end": round(r.end, 2),
                "words": r.words,
                "estimated": round(r.estimated, 1),
                "available": round(r.available, 1),
                "extension": round(r.extension, 1),
                "cut_words": r.cut_words,
            }
            for r in rows
        ],
    }


# --------------------------------------------------------------------------
# jobs
# --------------------------------------------------------------------------

@app.post("/api/jobs", dependencies=[Depends(require_login)])
async def create_job(video: str = Form(...), script: str = Form(...),
                     music: str = Form(""), engine: str = Form(""),
                     draft: bool = Form(True),
                     keep_original_audio: bool = Form(False),
                     burn_subs: bool = Form(False),
                     words: int = Form(0),
                     total_takes: int = Form(0),
                     estimate_seconds: float = Form(0.0)) -> dict:
    video_path = Path(video)
    script_path = Path(script)
    if not video_path.is_file():
        raise HTTPException(404, "Video nahi mili.")
    if not script_path.is_file():
        raise HTTPException(404, "Script nahi mili.")

    params = {
        "video": str(video_path), "script": str(script_path),
        "music": music or None, "engine": engine or cfg.tts_engine,
        "draft": draft, "keep_original_audio": keep_original_audio,
        "burn_subs": burn_subs, "words": words,
        "takes": cfg.best_of_n(draft=draft),
        "total_takes": total_takes,
        "estimate_seconds": estimate_seconds,
    }
    job_id = manager.submit(video_path.stem, params)
    return {"id": job_id}


@app.get("/api/jobs", dependencies=[Depends(require_login)])
def list_jobs() -> dict:
    return {
        "current": manager.current(),
        "queue": manager.queue_view(),
        "jobs": [status.to_dict() for status in manager.all_jobs()[:40]],
    }


@app.get("/api/jobs/{job_id}", dependencies=[Depends(require_login)])
def job_status(job_id: str) -> dict:
    status = manager.read(job_id)
    if status is None:
        raise HTTPException(404, "Job nahi mili.")
    return status.to_dict()


@app.post("/api/jobs/{job_id}/cancel", dependencies=[Depends(require_login)])
def cancel_job(job_id: str) -> dict:
    return {"ok": manager.cancel(job_id)}


@app.post("/api/jobs/{job_id}/resume", dependencies=[Depends(require_login)])
def resume_job(job_id: str) -> dict:
    return {"ok": manager.resume(job_id)}


@app.get("/api/jobs/{job_id}/timeline", dependencies=[Depends(require_login)])
def job_timeline(job_id: str) -> dict:
    path = manager.job_dir(job_id) / "timeline.json"
    if not path.is_file():
        raise HTTPException(404, "Timeline abhi tayyar nahi.")
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/api/jobs/{job_id}/file/{kind}", dependencies=[Depends(require_login)])
def job_file(job_id: str, kind: str):
    status = manager.read(job_id)
    if status is None:
        raise HTTPException(404, "Job nahi mili.")
    folder = manager.job_dir(job_id)

    choices = {
        "video": folder / "final.mp4",
        "subtitles": Path(status.result.get("subtitles") or ""),
        "report": Path(status.result.get("report") or ""),
        "timeline": folder / "timeline.json",
    }
    path = choices.get(kind)
    if path is None or not path.is_file():
        raise HTTPException(404, "File nahi mili.")

    media = "video/mp4" if kind == "video" else "application/octet-stream"
    inline = kind == "video"
    return FileResponse(path, media_type=media,
                        filename=None if inline else path.name)


# --------------------------------------------------------------------------
# voice profiles
# --------------------------------------------------------------------------

@app.get("/api/voice", dependencies=[Depends(require_login)])
def voice_state() -> dict:
    active = voice_profiles.active_info()
    return {
        "active": active,
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
        raise HTTPException(403, "Ye file serve nahi ki ja sakti.")
    if not target.is_file():
        raise HTTPException(404, "File nahi mili.")
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


if (WEB / "static").is_dir():
    app.mount("/static", StaticFiles(directory=WEB / "static"), name="static")


@app.middleware("http")
async def no_cache_static(request: Request, call_next):
    """Never let the browser cache the app's own JS/CSS.

    This is a local single-user app that gets edited often; a stale app.js
    silently serving old behaviour is far more expensive than re-fetching
    a few KB over localhost.
    """
    response = await call_next(request)
    if request.url.path.startswith("/static"):
        response.headers["Cache-Control"] = "no-store, must-revalidate"
    return response


@app.on_event("shutdown")
def _shutdown() -> None:
    manager.shutdown()


def main() -> int:
    import uvicorn

    url = f"http://{cfg.api_host}:{cfg.api_port}"
    log.user(f"Server chal raha hai: {url}")
    if "--no-browser" not in sys.argv:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    uvicorn.run(app, host=cfg.api_host, port=cfg.api_port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
