"""Aggregate numbers for the dashboard, and what this machine is.

Everything here is read from what previous jobs actually produced - the QC
report and the voice stage report in each job folder - rather than kept in
a separate counter that could drift from reality.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.config import Config  # noqa: E402


@dataclass
class Stats:
    videos: int = 0
    minutes: float = 0.0
    avg_similarity: float | None = None
    avg_drift: float | None = None
    qc_passed: int = 0
    qc_failed: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def collect_stats(cfg: Config) -> Stats:
    stats = Stats()
    root = cfg.output_dir / "jobs"
    if not root.is_dir():
        return stats

    similarities: list[float] = []
    drifts: list[float] = []

    for folder in root.iterdir():
        if not folder.is_dir():
            continue
        status = _read_json(folder / "status.json")
        if not status or status.get("state") != "done":
            continue

        stats.videos += 1

        report = next((p for p in folder.glob("*_report.json")), None)
        qc = _read_json(report) if report else None
        if qc:
            stats.minutes += float(qc.get("duration_actual") or 0.0) / 60.0
            if qc.get("passed"):
                stats.qc_passed += 1
            else:
                stats.qc_failed += 1
            if qc.get("drift_applicable") and qc.get("drift_median") is not None:
                drifts.append(float(qc["drift_median"]))

        voice = _read_json(folder / "voice_stage.json")
        if isinstance(voice, list):
            for block in voice:
                # Only blocks long enough for ECAPA to mean anything.
                if block.get("similarity") is not None \
                        and block.get("similarity_reliable"):
                    similarities.append(float(block["similarity"]))

    stats.minutes = round(stats.minutes, 1)
    if similarities:
        stats.avg_similarity = round(sum(similarities) / len(similarities), 1)
    if drifts:
        stats.avg_drift = round(sum(drifts) / len(drifts), 3)
    return stats


def _ram() -> tuple[float, float]:
    """(total GB, free GB). Windows first, then a POSIX fallback."""
    try:
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(MemoryStatus)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        return (round(status.ullTotalPhys / 2**30, 1),
                round(status.ullAvailPhys / 2**30, 1))
    except Exception:
        pass
    try:
        total = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
        free = os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
        return round(total / 2**30, 1), round(free / 2**30, 1)
    except Exception:
        return 0.0, 0.0


_model_cache: tuple[float, list[dict]] | None = None
_MODEL_CACHE_TTL = 120.0


def _cached_models() -> list[dict]:
    """Which model weights are already on disk, so nothing has to be
    re-downloaded. The Turbo download alone took 47 minutes once.

    Sizing this means walking several gigabytes of files, which is far too
    slow to do on every dashboard refresh, so the answer is held for a
    couple of minutes. Model caches do not change often.
    """
    global _model_cache
    import time

    now = time.time()
    if _model_cache and now - _model_cache[0] < _MODEL_CACHE_TTL:
        return _model_cache[1]

    cache = Path.home() / ".cache" / "huggingface" / "hub"
    out: list[dict] = []
    if not cache.is_dir():
        _model_cache = (now, out)
        return out
    for folder in cache.iterdir():
        if not folder.is_dir() or not folder.name.startswith("models--"):
            continue
        size = 0
        for item in folder.rglob("*"):
            if item.is_file():
                try:
                    size += item.stat().st_size
                except OSError:
                    pass
        out.append({
            "name": folder.name.replace("models--", "").replace("--", "/"),
            "gb": round(size / 2**30, 2),
        })
    ecapa = PROJECT_ROOT / "models" / "ecapa"
    if ecapa.is_dir():
        size = sum(f.stat().st_size for f in ecapa.rglob("*") if f.is_file())
        out.append({"name": "speechbrain/spkrec-ecapa-voxceleb",
                    "gb": round(size / 2**30, 2)})

    out = sorted(out, key=lambda m: m["gb"], reverse=True)
    _model_cache = (now, out)
    return out


def machine_info(cfg: Config) -> dict:
    total_ram, free_ram = _ram()

    gpu = None
    try:
        import torch

        if torch.cuda.is_available():
            gpu = torch.cuda.get_device_name(0)
    except Exception:
        pass

    ffmpeg_version = None
    try:
        proc = subprocess.run([cfg.ffmpeg_bin, "-version"],
                              capture_output=True, text=True, timeout=10)
        first = proc.stdout.splitlines()[0] if proc.stdout else ""
        full = first.split(" Copyright")[0].replace("ffmpeg version ", "")
        # Just the version. The build suffix ("-full_build-www.gyan.dev")
        # is noise in a dashboard row.
        ffmpeg_version = full.split("-")[0].strip()
    except Exception:
        pass

    disk_free = None
    try:
        disk_free = round(shutil.disk_usage(PROJECT_ROOT).free / 2**30, 1)
    except Exception:
        pass

    return {
        "device": cfg.device,
        "accelerator": gpu or "CPU only",
        "has_gpu": gpu is not None,
        "cpu_count": os.cpu_count(),
        "threads": cfg.num_threads,
        "ram_total_gb": total_ram,
        "ram_free_gb": free_ram,
        "disk_free_gb": disk_free,
        "ffmpeg": ffmpeg_version,
        "python": sys.version.split()[0],
        "engine": cfg.tts_engine,
        "models": _cached_models(),
    }
