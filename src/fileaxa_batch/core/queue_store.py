"""SQLite-backed persistence for the download queue.

We ship a pre-initialized template database (`data/queue_template.db`) inside
the package. On first launch we copy it to the user's config directory as
their own working instance, then mutate that copy. The template stays
untouched and lets the schema travel with the wheel without any DDL
migration logic at runtime.

In-flight statuses (NAVIGATING, DOWNLOADING, etc.) are reset to PENDING on
load: Fileaxa's free-tier downloads use one-time URLs, so a partial file
from a previous run can't be appended to. The only honest resume is to
re-claim the row and run the flow from scratch.
"""
from __future__ import annotations

import shutil
import sqlite3
from contextlib import closing
from importlib.resources import as_file, files
from pathlib import Path
from typing import List, Optional

from .models import DownloadJob, FileMeta, JobStatus
from .settings import config_dir

_INTERRUPTED = {
    JobStatus.NAVIGATING.value,
    JobStatus.WAITING_TIMER.value,
    JobStatus.WAITING_CAPTCHA.value,
    JobStatus.FETCHING_METADATA.value,
    JobStatus.DOWNLOADING.value,
}

# Schema kept here as the source of truth. The shipped template.db is
# generated from this string (see scripts/build_template_db.py); we also
# run it as CREATE IF NOT EXISTS on every connect as a defensive fallback
# in case the template is missing or out of date.
SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    url         TEXT    NOT NULL,
    file_code   TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'pending',
    dest_path   TEXT,
    error       TEXT,
    meta_name   TEXT,
    meta_size   INTEGER,
    created_at  TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def queue_db_path() -> Path:
    return config_dir() / "queue.db"


def _template_path() -> Optional[Path]:
    """Path to the shipped template DB inside the installed package, or None
    if it's missing (e.g. running from a source checkout that hasn't built
    the template yet)."""
    try:
        res = files("fileaxa_batch").joinpath("data/queue_template.db")
        with as_file(res) as p:
            return p if p.exists() else None
    except (ModuleNotFoundError, FileNotFoundError):
        return None


def ensure_user_db(db_path: Optional[Path] = None) -> Path:
    """Make sure the user-side DB exists. Copies from the shipped template
    on first launch; falls back to running the schema inline if no template
    is bundled."""
    db_path = db_path or queue_db_path()
    if db_path.exists():
        return db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    template = _template_path()
    if template is not None:
        shutil.copyfile(template, db_path)
    else:
        with closing(sqlite3.connect(db_path)) as cx:
            cx.executescript(SCHEMA)
            cx.commit()
    return db_path


def _connect(db_path: Path) -> sqlite3.Connection:
    cx = sqlite3.connect(db_path)
    cx.row_factory = sqlite3.Row
    cx.executescript(SCHEMA)  # idempotent; protects against drift
    return cx


def save_queue(jobs: List[DownloadJob], db_path: Optional[Path] = None) -> None:
    db_path = ensure_user_db(db_path)
    rows = [_job_to_row(j) for j in jobs]
    with closing(_connect(db_path)) as cx:
        with cx:  # transaction: commit on success, rollback on exception
            cx.execute("DELETE FROM jobs")
            if rows:
                cx.executemany(
                    "INSERT INTO jobs "
                    "(url, file_code, status, dest_path, error, meta_name, meta_size) "
                    "VALUES (:url, :file_code, :status, :dest_path, :error, :meta_name, :meta_size)",
                    rows,
                )


def load_queue(db_path: Optional[Path] = None) -> List[DownloadJob]:
    db_path = db_path or queue_db_path()
    if not db_path.exists():
        return []
    try:
        with closing(_connect(db_path)) as cx:
            rows = cx.execute(
                "SELECT url, file_code, status, dest_path, error, meta_name, meta_size "
                "FROM jobs ORDER BY id"
            ).fetchall()
    except sqlite3.DatabaseError:
        return []
    return [_row_to_job(r) for r in rows]


def _job_to_row(j: DownloadJob) -> dict:
    return {
        "url": j.url,
        "file_code": j.file_code,
        "status": j.status.value,
        "dest_path": str(j.dest_path) if j.dest_path is not None else None,
        "error": j.error,
        "meta_name": j.meta.name if j.meta else None,
        "meta_size": j.meta.size if j.meta else None,
    }


def _row_to_job(r: sqlite3.Row) -> DownloadJob:
    status_str = r["status"] or JobStatus.PENDING.value
    if status_str in _INTERRUPTED:
        status = JobStatus.PENDING
    else:
        try:
            status = JobStatus(status_str)
        except ValueError:
            status = JobStatus.PENDING
    meta = None
    if r["meta_name"] is not None or r["meta_size"] is not None:
        meta = FileMeta(
            file_code=r["file_code"],
            name=r["meta_name"],
            size=r["meta_size"],
        )
    dest_path = Path(r["dest_path"]) if r["dest_path"] else None
    return DownloadJob(
        url=r["url"],
        file_code=r["file_code"],
        status=status,
        meta=meta,
        dest_path=dest_path,
        error=r["error"],
    )
