from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal


class WorkerSignals(QObject):
    """Signals emitted by the download worker. Lives as its own QObject so it
    can be constructed in the GUI thread and the worker emits across threads
    (Qt's queued connections handle the marshalling automatically).
    """

    job_started = pyqtSignal(int)               # row index
    metadata_ready = pyqtSignal(int, str, int)  # row index, filename ("" if unknown), size (-1 if unknown)
    status_changed = pyqtSignal(int, str)       # row index, display status string
    progress = pyqtSignal(int, int, int, float, float)  # row, bytes_done, bytes_total (-1 if unknown), speed_bps, eta_s
    job_completed = pyqtSignal(int, str)        # row index, dest path
    job_failed = pyqtSignal(int, str)           # row index, error message

    quota_updated = pyqtSignal(str)             # human-readable quota line
    worker_log = pyqtSignal(str)
    worker_stopped = pyqtSignal(int)            # worker_id of the thread that just exited
