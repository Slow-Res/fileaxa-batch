"""Lightweight tests for QueueModel. We don't spin up a QApplication — the
methods we exercise don't need an event loop."""
import os
import sys

import pytest

pytest.importorskip("PyQt6")

# Required on headless test envs; ignored on developer machines with a display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QCoreApplication  # noqa: E402

from fileaxa_batch.core.models import DownloadJob, FileMeta, JobStatus  # noqa: E402
from fileaxa_batch.gui.queue_model import QueueModel  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QCoreApplication.instance() or QCoreApplication(sys.argv)
    yield app


def test_add_and_count(qapp):
    model = QueueModel([])
    assert model.rowCount() == 0
    model.add_job(DownloadJob(url="https://fileaxa.com/abc12345", file_code="abc12345"))
    assert model.rowCount() == 1


def test_remove_completed_keeps_failed_and_cancelled(qapp):
    """FAILED and CANCELLED are recoverable via Retry — Clear completed
    must leave them alone."""
    jobs = [
        DownloadJob(url="u1", file_code="c1", status=JobStatus.PENDING),
        DownloadJob(url="u2", file_code="c2", status=JobStatus.COMPLETED),
        DownloadJob(url="u3", file_code="c3", status=JobStatus.FAILED),
        DownloadJob(url="u4", file_code="c4", status=JobStatus.CANCELLED),
        DownloadJob(url="u5", file_code="c5", status=JobStatus.PENDING),
    ]
    model = QueueModel(jobs)
    model.remove_completed()
    # u2 (COMPLETED) is swept; the rest stay.
    assert model.rowCount() == 4
    surviving_statuses = {j.status for j in jobs}
    assert JobStatus.COMPLETED not in surviving_statuses
    assert JobStatus.FAILED in surviving_statuses
    assert JobStatus.CANCELLED in surviving_statuses


def test_data_returns_filename_when_meta_set(qapp):
    job = DownloadJob(url="u1", file_code="c1")
    job.meta = FileMeta(file_code="c1", name="movie.mkv", size=1024 * 1024)
    model = QueueModel([job])
    name_idx = model.index(0, QueueModel.COL_NAME)
    size_idx = model.index(0, QueueModel.COL_SIZE)
    assert model.data(name_idx) == "movie.mkv"
    assert "MB" in model.data(size_idx)
