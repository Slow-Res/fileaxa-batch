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


def test_remove_finished(qapp):
    jobs = [
        DownloadJob(url="u1", file_code="c1", status=JobStatus.PENDING),
        DownloadJob(url="u2", file_code="c2", status=JobStatus.COMPLETED),
        DownloadJob(url="u3", file_code="c3", status=JobStatus.FAILED),
        DownloadJob(url="u4", file_code="c4", status=JobStatus.PENDING),
    ]
    model = QueueModel(jobs)
    model.remove_finished()
    assert model.rowCount() == 2
    assert all(j.status == JobStatus.PENDING for j in jobs)


def test_data_returns_filename_when_meta_set(qapp):
    job = DownloadJob(url="u1", file_code="c1")
    job.meta = FileMeta(file_code="c1", name="movie.mkv", size=1024 * 1024)
    model = QueueModel([job])
    name_idx = model.index(0, QueueModel.COL_NAME)
    size_idx = model.index(0, QueueModel.COL_SIZE)
    assert model.data(name_idx) == "movie.mkv"
    assert "MB" in model.data(size_idx)
