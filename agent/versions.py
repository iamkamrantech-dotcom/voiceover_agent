"""Version history for a job: v1, v2, v3, each playable and downloadable.

A revision changes one block and rebuilds the video. The old video is not
thrown away - comparing the new take against the old one by ear is the whole
reason for making the change, and you cannot do that if v1 is gone.

Layout, chosen so that nothing already on disk has to move:

    outputs/jobs/<id>/
        final.mp4  timeline.json  <stem>.srt  <stem>_report.json   <- v1
        versions.json
        versions/v2/{final.mp4, timeline.json, subtitles.srt, report.json}

A job made before this module existed has no versions.json, so one v1 entry
is reconstructed from the files that are there. Every job already on disk
therefore gains a history without being touched.

Paths are stored RELATIVE to the job folder, so the outputs directory can be
moved or copied without breaking every link in it.
"""

from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

VERSIONS_FILE = "versions.json"

# What a caller may ask for, and the file name each version stores it under.
KINDS = ("video", "subtitles", "timeline", "report", "waveform")


@dataclass
class Version:
    version: int
    created: str = ""
    note: str = ""
    changes: list[dict] = field(default_factory=list)
    video: str | None = None
    subtitles: str | None = None
    timeline: str | None = None
    report: str | None = None
    waveform: str | None = None
    qc_passed: bool | None = None
    duration: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def version_dir(job_dir: Path, version: int) -> Path:
    """v1 lives at the top of the job folder; later versions get their own."""
    job_dir = Path(job_dir)
    return job_dir if version <= 1 else job_dir / "versions" / f"v{version}"


def work_dir(job_dir: Path, version: int) -> Path:
    """Scratch space for one version.

    plan_timeline names its files by block index, so two versions sharing a
    work directory would overwrite each other's audio.
    """
    return Path(job_dir) / "_work" / f"v{version}"


def _relative(job_dir: Path, path: Path | str | None) -> str | None:
    if not path:
        return None
    path = Path(path)
    try:
        return path.relative_to(job_dir).as_posix()
    except ValueError:
        return path.as_posix()


def _legacy_version(job_dir: Path) -> Version | None:
    """Reconstruct v1 for a job that finished before versions existed."""
    video = job_dir / "final.mp4"
    if not video.is_file():
        video = next(iter(sorted(job_dir.glob("*_final.mp4"))), None)
    if video is None or not video.is_file():
        return None

    srt = next(iter(sorted(job_dir.glob("*.srt"))), None)
    report = next(iter(sorted(job_dir.glob("*_report.json"))), None)
    timeline = job_dir / "timeline.json"
    waveform = job_dir / "waveform.json"

    passed = None
    if report is not None and report.is_file():
        try:
            passed = bool(json.loads(report.read_text(encoding="utf-8")).get("passed"))
        except Exception:
            passed = None

    return Version(
        version=1,
        created=datetime.fromtimestamp(video.stat().st_mtime).isoformat(timespec="seconds"),
        note="First render",
        video=_relative(job_dir, video),
        subtitles=_relative(job_dir, srt),
        timeline=_relative(job_dir, timeline) if timeline.is_file() else None,
        report=_relative(job_dir, report),
        waveform=_relative(job_dir, waveform) if waveform.is_file() else None,
        qc_passed=passed,
    )


def list_versions(job_dir: Path) -> list[Version]:
    job_dir = Path(job_dir)
    path = job_dir / VERSIONS_FILE
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return [Version(**item) for item in raw]
        except Exception:
            pass  # fall through and rebuild what we can see

    legacy = _legacy_version(job_dir)
    return [legacy] if legacy else []


def save_versions(job_dir: Path, versions: list[Version]) -> None:
    path = Path(job_dir) / VERSIONS_FILE
    path.write_text(
        json.dumps([v.to_dict() for v in versions], indent=2), encoding="utf-8")


def latest(job_dir: Path) -> Version | None:
    versions = list_versions(job_dir)
    return versions[-1] if versions else None


def next_number(job_dir: Path) -> int:
    versions = list_versions(job_dir)
    return (max(v.version for v in versions) + 1) if versions else 1


def record(job_dir: Path, version: int, note: str, changes: list[dict],
           video: Path, subtitles: Path | None, timeline: Path | None,
           report: Path | None, waveform: Path | None,
           qc_passed: bool | None, duration: float) -> Version:
    """Add (or replace) one entry in the history."""
    job_dir = Path(job_dir)
    versions = [v for v in list_versions(job_dir) if v.version != version]
    entry = Version(
        version=version,
        created=datetime.now().isoformat(timespec="seconds"),
        note=note,
        changes=changes,
        video=_relative(job_dir, video),
        subtitles=_relative(job_dir, subtitles),
        timeline=_relative(job_dir, timeline),
        report=_relative(job_dir, report),
        waveform=_relative(job_dir, waveform),
        qc_passed=qc_passed,
        duration=round(duration, 3),
    )
    versions.append(entry)
    versions.sort(key=lambda v: v.version)
    save_versions(job_dir, versions)
    return entry


def find(job_dir: Path, version: int | None) -> Version | None:
    """A specific version, or the newest when none is asked for."""
    versions = list_versions(job_dir)
    if not versions:
        return None
    if version is None:
        return versions[-1]
    return next((v for v in versions if v.version == version), None)


def file_for(job_dir: Path, kind: str, version: int | None = None) -> Path | None:
    """Resolve one deliverable of one version to a real file."""
    if kind not in KINDS:
        return None
    entry = find(job_dir, version)
    if entry is None:
        return None
    relative = getattr(entry, kind, None)
    if not relative:
        return None
    path = Path(job_dir) / relative
    return path if path.is_file() else None


def delete(job_dir: Path, version: int) -> tuple[bool, str]:
    """Remove one version. Never the last one, and never v1 in place -
    deleting v1 would take the job's own folder with it."""
    job_dir = Path(job_dir)
    versions = list_versions(job_dir)
    if len(versions) <= 1:
        return False, "This is the only version, so it cannot be deleted."
    entry = next((v for v in versions if v.version == version), None)
    if entry is None:
        return False, f"Version {version} does not exist."
    if version <= 1:
        return False, ("Version 1 is the original render and stays with the "
                       "job. Delete the whole job instead.")

    shutil.rmtree(version_dir(job_dir, version), ignore_errors=True)
    shutil.rmtree(work_dir(job_dir, version), ignore_errors=True)
    save_versions(job_dir, [v for v in versions if v.version != version])
    return True, f"Version {version} deleted."
