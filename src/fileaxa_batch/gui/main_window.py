from __future__ import annotations

import subprocess
import sys
from typing import List

from PyQt6.QtCore import QModelIndex, Qt
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ..core.models import DownloadJob, FileMeta, JobStatus
from ..core.queue_store import load_queue, save_queue
from ..core.settings import AppSettings, Mode
from ..core.urls import parse_file_code
from ..worker.signals import WorkerSignals
from ..worker.worker import DownloadWorker, JobClaimer
from .queue_model import QueueModel
from .settings_dialog import SettingsDialog
from .widgets import UrlInput

MAX_WORKERS = 4


class MainWindow(QMainWindow):
    def __init__(self, settings: AppSettings):
        super().__init__()
        self.setWindowTitle("fileaxa-batch")
        self.resize(980, 660)

        self._settings = settings
        self._jobs: List[DownloadJob] = load_queue()
        restored_pending = sum(1 for j in self._jobs if j.status == JobStatus.PENDING)
        self._model = QueueModel(self._jobs)
        self._signals = WorkerSignals()
        self._workers: List[DownloadWorker] = []
        self._claimer = JobClaimer(self._jobs)
        self._next_worker_id = 1

        self._build_ui()
        self._wire_signals()

        if self._jobs:
            self._append_log(
                f"loaded {len(self._jobs)} job(s) from previous session "
                f"({restored_pending} pending)"
            )

    # ---------- UI ----------

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)

        # Toolbar row
        bar = QHBoxLayout()
        self._add_btn = QPushButton("Add to queue")
        self._start_btn = QPushButton("Start")
        self._resume_btn = QPushButton("Resume")
        self._spawn_btn = QPushButton(self._spawn_btn_label(0))
        self._pause_btn = QPushButton("Pause")
        self._cancel_btn = QPushButton("Cancel current")
        self._clear_btn = QPushButton("Clear completed")
        self._open_btn = QPushButton("Open downloads")
        self._settings_btn = QPushButton("Settings…")
        for b in (
            self._add_btn,
            self._start_btn,
            self._resume_btn,
            self._spawn_btn,
            self._pause_btn,
            self._cancel_btn,
            self._clear_btn,
            self._open_btn,
            self._settings_btn,
        ):
            bar.addWidget(b)
        bar.addStretch(1)
        root.addLayout(bar)

        # Split: URL input / queue table / log
        split = QSplitter(Qt.Orientation.Vertical)

        self._url_input = UrlInput()
        split.addWidget(self._url_input)

        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_row_context_menu)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(QueueModel.COL_URL, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(QueueModel.COL_NAME, QHeaderView.ResizeMode.Stretch)
        self._table.setColumnWidth(QueueModel.COL_URL, 220)
        split.addWidget(self._table)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(500)
        split.addWidget(self._log)

        split.setSizes([120, 400, 120])
        root.addWidget(split, 1)

        # Status bar
        sb = QStatusBar()
        self.setStatusBar(sb)
        self._mode_label = QLabel()
        self._quota_label = QLabel()
        sb.addWidget(self._mode_label)
        sb.addPermanentWidget(self._quota_label)
        self._refresh_mode_label()

        self.setCentralWidget(central)

        self._add_btn.clicked.connect(self._on_add)
        self._start_btn.clicked.connect(self._on_start)
        self._resume_btn.clicked.connect(self._on_start)
        self._spawn_btn.clicked.connect(self._on_spawn)
        self._pause_btn.clicked.connect(self._on_pause)
        self._cancel_btn.clicked.connect(self._on_cancel)
        self._clear_btn.clicked.connect(self._on_clear)
        self._open_btn.clicked.connect(self._on_open_downloads)
        self._settings_btn.clicked.connect(self._on_settings)

        self._refresh_running_state()

    def _wire_signals(self) -> None:
        s = self._signals
        s.job_started.connect(self._on_job_started)
        s.metadata_ready.connect(self._on_metadata_ready)
        s.status_changed.connect(self._on_status_changed)
        s.progress.connect(self._on_progress)
        s.job_completed.connect(self._on_job_completed)
        s.job_failed.connect(self._on_job_failed)
        s.quota_updated.connect(self._on_quota_updated)
        s.worker_log.connect(self._append_log)
        s.worker_stopped.connect(self._on_worker_stopped)

    # ---------- Buttons ----------

    def _on_add(self) -> None:
        text = self._url_input.toPlainText().strip()
        if not text:
            return
        added = 0
        approval = 0
        skipped = 0
        for line in text.splitlines():
            url = line.strip()
            if not url or url.startswith("#"):
                continue
            code = parse_file_code(url)
            if not code:
                skipped += 1
                continue
            # Queue-side dedup: if the file_code is already enqueued in any
            # status, the new row enters as APPROVAL — the user decides
            # Retry / Override / Cancel via the row context menu.
            existing = next(
                (i for i, j in enumerate(self._jobs) if j.file_code == code),
                None,
            )
            if existing is not None:
                self._model.add_job(
                    DownloadJob(
                        url=url,
                        file_code=code,
                        status=JobStatus.APPROVAL,
                        error=f"duplicate of row {existing + 1}",
                    )
                )
                approval += 1
            else:
                self._model.add_job(DownloadJob(url=url, file_code=code))
                added += 1
        self._url_input.clear()
        parts = [f"queued {added} URL(s)"]
        if approval:
            parts.append(f"{approval} duplicate(s) need approval")
        if skipped:
            parts.append(f"skipped {skipped} invalid")
        self._append_log("; ".join(parts))
        if added or approval:
            self._persist_queue()
            self._refresh_running_state()

    def _on_start(self) -> None:
        if self._workers:
            for w in self._workers:
                w.resume()
            self._refresh_running_state()
            self._append_log("resumed")
            return
        if not any(j.status == JobStatus.PENDING for j in self._jobs):
            QMessageBox.information(
                self, "Nothing to do", "Queue is empty or has no pending items."
            )
            return
        self._spawn_worker()

    def _on_spawn(self) -> None:
        if len(self._workers) >= MAX_WORKERS:
            return
        if not any(j.status == JobStatus.PENDING for j in self._jobs):
            QMessageBox.information(
                self,
                "Nothing to do",
                "No pending jobs left for a new worker to claim.",
            )
            return
        self._spawn_worker()

    def _spawn_worker(self) -> None:
        worker_id = self._next_worker_id
        self._next_worker_id += 1
        worker = DownloadWorker(
            self._signals,
            self._jobs,
            self._settings,
            self._claimer,
            worker_id=worker_id,
        )
        self._workers.append(worker)
        worker.start()
        self._append_log(f"worker {worker_id} started")
        self._refresh_running_state()

    def _on_pause(self) -> None:
        if not self._workers:
            return
        for w in self._workers:
            w.pause()
        self._append_log("paused — workers will stop after their current jobs")

    def _on_cancel(self) -> None:
        if not self._workers:
            return
        for w in self._workers:
            w.cancel_current()
        self._append_log("cancel requested for in-flight jobs")

    def _on_clear(self) -> None:
        self._model.remove_completed()
        self._persist_queue()
        self._refresh_running_state()

    # ---------- Row context menu ----------

    def _on_row_context_menu(self, pos) -> None:
        """Right-click menu on the queue table.

        - Retry for FAILED / CANCELLED rows (existing behavior)
        - Retry / Override / Cancel for APPROVAL rows (duplicate-URL flow)
        """
        retryable_rows = self._selected_rows_with_status(
            JobStatus.FAILED, JobStatus.CANCELLED
        )
        approval_rows = self._selected_rows_with_status(JobStatus.APPROVAL)

        menu = QMenu(self._table)

        # Retry: applies to FAILED/CANCELLED *and* APPROVAL (in APPROVAL the
        # original stays in the queue; the new row just flips to PENDING).
        retry_targets = retryable_rows + approval_rows
        retry_action = QAction(
            f"Retry ({len(retry_targets)})" if retry_targets else "Retry",
            self._table,
        )
        retry_action.setEnabled(bool(retry_targets))
        retry_action.triggered.connect(lambda: self._retry_rows(retry_targets))
        menu.addAction(retry_action)

        # Override / Cancel only make sense on APPROVAL rows.
        override_action = QAction(
            f"Override ({len(approval_rows)})" if approval_rows else "Override",
            self._table,
        )
        override_action.setEnabled(bool(approval_rows))
        override_action.triggered.connect(
            lambda: self._override_rows(approval_rows)
        )
        menu.addAction(override_action)

        cancel_action = QAction(
            f"Cancel ({len(approval_rows)})" if approval_rows else "Cancel",
            self._table,
        )
        cancel_action.setEnabled(bool(approval_rows))
        cancel_action.triggered.connect(
            lambda: self._cancel_approval_rows(approval_rows)
        )
        menu.addAction(cancel_action)

        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _selected_rows_with_status(self, *allowed: JobStatus) -> List[int]:
        sel = self._table.selectionModel().selectedRows()
        return [
            idx.row()
            for idx in sel
            if 0 <= idx.row() < len(self._jobs)
            and self._jobs[idx.row()].status in allowed
        ]

    def _retry_rows(self, rows: List[int]) -> None:
        if not rows:
            return
        for row in rows:
            job = self._jobs[row]
            job.status = JobStatus.PENDING
            job.error = None
            job.bytes_done = 0
            job.total_bytes = 0
            job.speed_bps = 0.0
            job.eta_s = 0.0
            self._model.refresh_row(row)
        self._append_log(f"retry queued for {len(rows)} row(s)")
        self._persist_queue()
        self._refresh_running_state()

    def _override_rows(self, rows: List[int]) -> None:
        """For each APPROVAL row, mark every OTHER row with the same
        file_code as CANCELLED (superseded), then flip this row to PENDING.
        Active downloads of the original are left alone — overriding a row
        that's currently mid-flight only takes effect after that worker
        finishes; the existing in-flight state is logged."""
        if not rows:
            return
        active = {
            JobStatus.FETCHING_METADATA,
            JobStatus.NAVIGATING,
            JobStatus.WAITING_TIMER,
            JobStatus.WAITING_CAPTCHA,
            JobStatus.DOWNLOADING,
        }
        overridden = 0
        for row in rows:
            job = self._jobs[row]
            for i, other in enumerate(self._jobs):
                if i == row or other.file_code != job.file_code:
                    continue
                if other.status in active:
                    self._append_log(
                        f"row {i + 1} is currently active; "
                        "override will only apply after it completes"
                    )
                    continue
                other.status = JobStatus.CANCELLED
                other.error = f"superseded by row {row + 1}"
                self._model.refresh_row(i)
            job.status = JobStatus.PENDING
            job.error = None
            self._model.refresh_row(row)
            overridden += 1
        self._append_log(f"override applied to {overridden} row(s)")
        self._persist_queue()
        self._refresh_running_state()

    def _cancel_approval_rows(self, rows: List[int]) -> None:
        """Drop APPROVAL rows from the queue entirely. Safe across workers
        because APPROVAL rows are never claimed (JobClaimer only takes
        PENDING)."""
        if not rows:
            return
        # Remove from end to keep earlier indices stable mid-iteration.
        for row in sorted(rows, reverse=True):
            self._model.beginRemoveRows(QModelIndex(), row, row)
            del self._jobs[row]
            self._model.endRemoveRows()
        self._append_log(f"cancelled {len(rows)} approval row(s)")
        self._persist_queue()
        self._refresh_running_state()

    def _on_open_downloads(self) -> None:
        d = self._settings.download_dir
        d.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform.startswith("linux"):
                subprocess.Popen(["xdg-open", str(d)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(d)])
            elif sys.platform.startswith("win"):
                subprocess.Popen(["explorer", str(d)])
        except OSError as e:
            QMessageBox.warning(self, "Open folder failed", str(e))

    def _on_settings(self) -> None:
        dlg = SettingsDialog(self._settings, self)
        if dlg.exec():
            self._refresh_mode_label()

    # ---------- Worker signals ----------

    def _on_job_started(self, idx: int) -> None:
        self._model.refresh_row(idx)
        self._table.selectRow(idx)
        self._table.scrollTo(self._model.index(idx, 0))

    def _on_metadata_ready(self, idx: int, name: str, size: int) -> None:
        if 0 <= idx < len(self._jobs):
            self._jobs[idx].meta = FileMeta(
                file_code=self._jobs[idx].file_code,
                name=name or None,
                size=size if size >= 0 else None,
            )
            self._model.refresh_row(idx)
            self._persist_queue()

    def _on_status_changed(self, idx: int, status: str) -> None:
        if 0 <= idx < len(self._jobs):
            self._model.refresh_row(idx)
        if "captcha" in status.lower():
            self.statusBar().showMessage(
                "⏳ Solve CAPTCHA in the Chromium window", 0
            )
        elif "timer" in status.lower():
            self.statusBar().showMessage(f"⌛ {status}", 0)
        else:
            self.statusBar().showMessage(status, 0)

    def _on_progress(
        self, idx: int, done: int, total: int, speed: float, eta: float
    ) -> None:
        if 0 <= idx < len(self._jobs):
            job = self._jobs[idx]
            job.bytes_done = done
            job.total_bytes = total if total > 0 else 0
            job.speed_bps = speed
            job.eta_s = eta
            self._model.refresh_row(idx)

    def _on_job_completed(self, idx: int, path: str) -> None:
        self._model.refresh_row(idx)
        self._append_log(f"✓ {path}")
        self._persist_queue()

    def _on_job_failed(self, idx: int, err: str) -> None:
        self._model.refresh_row(idx)
        self._append_log(f"✗ row {idx + 1}: {err}")
        self._persist_queue()

    def _on_quota_updated(self, text: str) -> None:
        self._quota_label.setText(text)

    def _on_worker_stopped(self, worker_id: int) -> None:
        self._append_log(f"worker {worker_id} stopped")
        self._workers = [w for w in self._workers if w.worker_id != worker_id]
        self._refresh_running_state()

    # ---------- helpers ----------

    def _append_log(self, line: str) -> None:
        self._log.appendPlainText(line)

    def _refresh_mode_label(self) -> None:
        m = "Anonymous" if self._settings.mode == Mode.ANONYMOUS else "API key"
        self._mode_label.setText(
            f"Mode: {m}  ·  Dir: {self._settings.download_dir}"
        )

    def _refresh_running_state(self) -> None:
        running = len(self._workers) > 0
        has_pending = any(j.status == JobStatus.PENDING for j in self._jobs)
        self._start_btn.setEnabled(not running and has_pending)
        self._resume_btn.setEnabled(not running and has_pending)
        self._pause_btn.setEnabled(running)
        self._cancel_btn.setEnabled(running)
        self._spawn_btn.setText(self._spawn_btn_label(len(self._workers)))
        self._spawn_btn.setEnabled(
            len(self._workers) < MAX_WORKERS and has_pending
        )

    @staticmethod
    def _spawn_btn_label(count: int) -> str:
        return f"+ Worker ({count}/{MAX_WORKERS})"

    def _persist_queue(self) -> None:
        try:
            save_queue(self._jobs)
        except OSError as e:
            self._append_log(f"queue save failed: {e}")

    # ---------- close ----------

    def closeEvent(self, event):
        running = [w for w in self._workers if w.isRunning()]
        if running:
            ans = QMessageBox.question(
                self,
                "Workers running",
                f"{len(running)} download worker(s) in progress. Stop and exit?",
            )
            if ans != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            for w in running:
                w.request_stop()
            for w in running:
                w.wait(5000)
        event.accept()
