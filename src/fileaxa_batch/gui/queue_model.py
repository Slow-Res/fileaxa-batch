from __future__ import annotations

from typing import List, Optional

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt

from ..core.models import DownloadJob, JobStatus


def _fmt_size(n: Optional[int]) -> str:
    if n is None or n < 0:
        return ""
    units = ("B", "KB", "MB", "GB", "TB")
    val = float(n)
    i = 0
    while val >= 1024 and i < len(units) - 1:
        val /= 1024
        i += 1
    if i == 0:
        return f"{int(val)} {units[i]}"
    return f"{val:.1f} {units[i]}"


class QueueModel(QAbstractTableModel):
    COL_URL = 0
    COL_NAME = 1
    COL_SIZE = 2
    COL_STATUS = 3
    COL_PROGRESS = 4

    HEADERS = ("URL", "Filename", "Size", "Status", "Progress")

    def __init__(self, jobs: List[DownloadJob], parent=None):
        super().__init__(parent)
        self.jobs = jobs

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.jobs)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        return section + 1

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        if role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return None
        job = self.jobs[index.row()]
        col = index.column()
        if col == self.COL_URL:
            return job.url
        if col == self.COL_NAME:
            return job.meta.name if job.meta and job.meta.name else ""
        if col == self.COL_SIZE:
            return _fmt_size(job.meta.size if job.meta else None)
        if col == self.COL_STATUS:
            if role == Qt.ItemDataRole.ToolTipRole and job.error:
                return job.error
            return job.status.value
        if col == self.COL_PROGRESS:
            if job.status == JobStatus.COMPLETED:
                return "done"
            if job.bytes_done > 0:
                return _fmt_size(job.bytes_done)
            return ""
        return None

    def add_job(self, job: DownloadJob) -> None:
        row = len(self.jobs)
        self.beginInsertRows(QModelIndex(), row, row)
        self.jobs.append(job)
        self.endInsertRows()

    def remove_finished(self) -> None:
        """Remove completed/cancelled/failed rows. Removes from end so indices
        stay stable during iteration."""
        terminal = (JobStatus.COMPLETED, JobStatus.CANCELLED, JobStatus.FAILED)
        for i in range(len(self.jobs) - 1, -1, -1):
            if self.jobs[i].status in terminal:
                self.beginRemoveRows(QModelIndex(), i, i)
                del self.jobs[i]
                self.endRemoveRows()

    def refresh_row(self, row: int) -> None:
        if 0 <= row < len(self.jobs):
            top = self.index(row, 0)
            bottom = self.index(row, self.columnCount() - 1)
            self.dataChanged.emit(top, bottom)
