"""Round-trip the queue through SQLite and verify crash-recovery semantics.

In-flight statuses get reset to PENDING on load because Fileaxa's one-time
download URLs make true partial-resume impossible — the only honest resume
is to re-run the flow from scratch."""
from pathlib import Path

import pytest

from fileaxa_batch.core.models import DownloadJob, FileMeta, JobStatus
from fileaxa_batch.core.queue_store import (
    ensure_user_db,
    load_queue,
    save_queue,
)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "queue.db"


def test_empty_when_db_missing(db: Path):
    assert load_queue(db) == []


def test_ensure_user_db_creates_schema(db: Path):
    """First-run path: no user DB yet, ensure_user_db must produce one with
    the `jobs` table ready to use."""
    ensure_user_db(db)
    assert db.exists()
    import sqlite3

    cx = sqlite3.connect(db)
    tables = [r[0] for r in cx.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )]
    cx.close()
    assert "jobs" in tables


def test_roundtrip_preserves_pending_and_metadata(db: Path):
    jobs = [
        DownloadJob(url="u1", file_code="c1"),
        DownloadJob(
            url="u2",
            file_code="c2",
            meta=FileMeta(file_code="c2", name="movie.mkv", size=1024),
        ),
    ]
    save_queue(jobs, db)
    loaded = load_queue(db)
    assert len(loaded) == 2
    assert loaded[0].url == "u1" and loaded[0].status == JobStatus.PENDING
    assert loaded[1].meta is not None
    assert loaded[1].meta.name == "movie.mkv"
    assert loaded[1].meta.size == 1024


def test_in_flight_statuses_reset_to_pending(db: Path):
    jobs = [
        DownloadJob(url=f"u{i}", file_code=f"c{i}", status=s)
        for i, s in enumerate(
            [
                JobStatus.NAVIGATING,
                JobStatus.WAITING_TIMER,
                JobStatus.WAITING_CAPTCHA,
                JobStatus.FETCHING_METADATA,
                JobStatus.DOWNLOADING,
            ]
        )
    ]
    save_queue(jobs, db)
    loaded = load_queue(db)
    assert all(j.status == JobStatus.PENDING for j in loaded)


def test_bytes_done_roundtrip(db: Path):
    """Partial-download offset must survive save/load so PAUSED rows can
    Range-resume after a restart."""
    jobs = [
        DownloadJob(
            url="u1",
            file_code="c1",
            status=JobStatus.PAUSED,
            dest_path=Path("/tmp/movie.rar"),
            bytes_done=123_456_789,
        ),
    ]
    save_queue(jobs, db)
    loaded = load_queue(db)
    assert loaded[0].bytes_done == 123_456_789
    assert loaded[0].status == JobStatus.PAUSED


def test_terminal_statuses_preserved(db: Path):
    jobs = [
        DownloadJob(
            url="u1",
            file_code="c1",
            status=JobStatus.COMPLETED,
            dest_path=Path("/tmp/file1"),
        ),
        DownloadJob(
            url="u2",
            file_code="c2",
            status=JobStatus.FAILED,
            error="boom",
        ),
        DownloadJob(url="u3", file_code="c3", status=JobStatus.CANCELLED),
    ]
    save_queue(jobs, db)
    loaded = load_queue(db)
    assert loaded[0].status == JobStatus.COMPLETED
    assert loaded[0].dest_path == Path("/tmp/file1")
    assert loaded[1].status == JobStatus.FAILED
    assert loaded[1].error == "boom"
    assert loaded[2].status == JobStatus.CANCELLED


def test_save_replaces_previous_state(db: Path):
    """save_queue is a checkpoint, not an append — passing fewer jobs should
    drop the ones no longer in memory."""
    save_queue([DownloadJob(url="a", file_code="ca"), DownloadJob(url="b", file_code="cb")], db)
    save_queue([DownloadJob(url="a", file_code="ca")], db)
    loaded = load_queue(db)
    assert len(loaded) == 1
    assert loaded[0].url == "a"


def test_corrupt_db_returns_empty(db: Path):
    db.write_text("not a sqlite file")
    assert load_queue(db) == []


def test_template_db_is_shipped():
    """If this fails after a fresh checkout, run: python scripts/build_template_db.py"""
    from importlib.resources import files
    res = files("fileaxa_batch").joinpath("data/queue_template.db")
    assert res.is_file()
