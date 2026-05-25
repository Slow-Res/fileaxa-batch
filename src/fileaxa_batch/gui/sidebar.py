"""Left-rail status filter for the queue table.

A compact QListWidget with one row per JobStatus + an 'All' option,
each labelled with the live count of matching rows. Clicking a row
emits statusSelected(status_or_None) — the main window plugs that into
a QSortFilterProxyModel that hides everything else.
"""
from __future__ import annotations

from typing import List, Optional

from PyQt6.QtCore import Qt, QSortFilterProxyModel, pyqtSignal
from PyQt6.QtWidgets import QListWidget, QListWidgetItem

from ..core.models import DownloadJob, JobStatus


# Display order matches the typical lifecycle the user sees flow through.
_BUCKETS: List[tuple[Optional[JobStatus], str]] = [
    (None, "All"),
    (JobStatus.PENDING, "Pending"),
    (JobStatus.APPROVAL, "Approval"),
    (JobStatus.FETCHING_METADATA, "Fetching"),
    (JobStatus.NAVIGATING, "Navigating"),
    (JobStatus.WAITING_TIMER, "Waiting timer"),
    (JobStatus.WAITING_CAPTCHA, "Waiting CAPTCHA"),
    (JobStatus.DOWNLOADING, "Downloading"),
    (JobStatus.PAUSED, "Paused"),
    (JobStatus.COMPLETED, "Completed"),
    (JobStatus.FAILED, "Failed"),
    (JobStatus.CANCELLED, "Cancelled"),
]


class StatusSidebar(QListWidget):
    statusSelected = pyqtSignal(object)  # JobStatus | None

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(150)
        self.setMaximumWidth(220)
        for status, label in _BUCKETS:
            item = QListWidgetItem(f"{label} (0)")
            item.setData(Qt.ItemDataRole.UserRole, status)
            self.addItem(item)
        self.setCurrentRow(0)
        self.currentItemChanged.connect(self._on_item_changed)

    def _on_item_changed(self, current, _previous) -> None:
        if current is None:
            return
        self.statusSelected.emit(current.data(Qt.ItemDataRole.UserRole))

    def update_counts(self, jobs: List[DownloadJob]) -> None:
        by_status: dict[JobStatus, int] = {}
        for j in jobs:
            by_status[j.status] = by_status.get(j.status, 0) + 1
        for i, (status, label) in enumerate(_BUCKETS):
            n = len(jobs) if status is None else by_status.get(status, 0)
            self.item(i).setText(f"{label} ({n})")


class StatusFilterProxy(QSortFilterProxyModel):
    """Show only rows whose underlying DownloadJob matches the selected
    status. None means 'show everything'."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._status_filter: Optional[JobStatus] = None

    def set_status_filter(self, status: Optional[JobStatus]) -> None:
        self._status_filter = status
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent) -> bool:
        if self._status_filter is None:
            return True
        model = self.sourceModel()
        if model is None:
            return True
        try:
            job = model.jobs[source_row]
        except (AttributeError, IndexError):
            return True
        return job.status == self._status_filter
