from __future__ import annotations

import subprocess
import sys
from typing import List

from PyQt6.QtCore import QModelIndex, Qt
from PyQt6.QtGui import QAction, QKeySequence
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

from ..core.dedup import delete_duplicates, find_duplicates
from ..core.models import DownloadJob, FileMeta, JobStatus
from ..core.queue_store import load_queue, save_queue
from ..core.settings import AppSettings, Mode
from ..core.urls import extract_urls, parse_file_code
from ..worker.signals import WorkerSignals
from ..worker.worker import DownloadWorker, JobClaimer
from .sidebar import StatusFilterProxy, StatusSidebar
from .queue_model import QueueModel
from .settings_dialog import SettingsDialog
from .widgets import UrlInput

MAX_WORKERS = 4
# When workers run headed (either because headless is off, or because there
# are redirector URLs in the queue and the worker auto-overrode), each
# Chromium eats ~40-60 X11 client slots. The default X server cap is ~256,
# so four headed Chromiums plus the main app routinely exhaust it. Cap
# concurrent headed workers conservatively.
MAX_HEADED_WORKERS = 2


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
        # Less-frequent actions live on the menu bar only — the toolbar
        # keeps the five most-common buttons.
        for b in (
            self._add_btn,
            self._start_btn,
            self._resume_btn,
            self._spawn_btn,
            self._pause_btn,
        ):
            bar.addWidget(b)
        bar.addStretch(1)
        root.addLayout(bar)

        self._build_menu_bar()

        # Split: URL input / queue table / log
        split = QSplitter(Qt.Orientation.Vertical)

        self._url_input = UrlInput()
        split.addWidget(self._url_input)

        # Sidebar (left) + table (right), horizontally split. The proxy
        # model in between filters by the sidebar's selected status; the
        # underlying QueueModel still holds every job.
        self._sidebar = StatusSidebar()
        self._proxy = StatusFilterProxy()
        self._proxy.setSourceModel(self._model)
        self._sidebar.statusSelected.connect(self._proxy.set_status_filter)
        # Recompute the sidebar's per-status counts whenever the underlying
        # model changes — rows added, removed, or status-touched via
        # refresh_row(). All three signals route through the source model.
        self._model.dataChanged.connect(self._refresh_sidebar_counts)
        self._model.rowsInserted.connect(self._refresh_sidebar_counts)
        self._model.rowsRemoved.connect(self._refresh_sidebar_counts)
        self._sidebar.update_counts(self._jobs)

        self._table = QTableView()
        self._table.setModel(self._proxy)
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_row_context_menu)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(QueueModel.COL_URL, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(QueueModel.COL_NAME, QHeaderView.ResizeMode.Stretch)
        self._table.setColumnWidth(QueueModel.COL_URL, 220)

        table_split = QSplitter(Qt.Orientation.Horizontal)
        table_split.addWidget(self._sidebar)
        table_split.addWidget(self._table)
        table_split.setStretchFactor(0, 0)
        table_split.setStretchFactor(1, 1)
        table_split.setSizes([170, 800])
        split.addWidget(table_split)

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

        self._refresh_running_state()

    def _build_menu_bar(self) -> None:
        """Group every action into File / Edit / Settings menus. The toolbar
        keeps the most-frequent five buttons; everything else lives here.
        Menu items share handlers with the toolbar so keyboard shortcuts
        work identically."""
        mb = self.menuBar()

        # ---- File ----------------------------------------------------------
        file_menu = mb.addMenu("&File")

        act = QAction("&Add to queue", self)
        act.setShortcut(QKeySequence("Ctrl+L"))
        act.triggered.connect(self._on_add)
        file_menu.addAction(act)

        act = QAction("&Open downloads folder", self)
        act.setShortcut(QKeySequence("Ctrl+O"))
        act.triggered.connect(self._on_open_downloads)
        file_menu.addAction(act)

        file_menu.addSeparator()
        act = QAction("&Quit", self)
        act.setShortcut(QKeySequence.StandardKey.Quit)
        act.triggered.connect(self.close)
        file_menu.addAction(act)

        # ---- Edit ----------------------------------------------------------
        edit_menu = mb.addMenu("&Edit")

        act = QAction("&Pause workers", self)
        act.setShortcut(QKeySequence("Ctrl+P"))
        act.triggered.connect(self._on_pause)
        edit_menu.addAction(act)

        act = QAction("&Cancel current jobs", self)
        act.triggered.connect(self._on_cancel)
        edit_menu.addAction(act)

        edit_menu.addSeparator()
        act = QAction("Clear &completed", self)
        act.triggered.connect(self._on_clear)
        edit_menu.addAction(act)

        act = QAction("Clear &duplicate files on disk…", self)
        act.triggered.connect(self._on_clear_duplicate_files)
        edit_menu.addAction(act)

        # ---- Settings ------------------------------------------------------
        settings_menu = mb.addMenu("&Settings")
        act = QAction("&Preferences…", self)
        act.setShortcut(QKeySequence("Ctrl+,"))
        act.triggered.connect(self._on_settings)
        settings_menu.addAction(act)

    def _wire_signals(self) -> None:
        s = self._signals
        s.job_started.connect(self._on_job_started)
        s.metadata_ready.connect(self._on_metadata_ready)
        s.status_changed.connect(self._on_status_changed)
        s.progress.connect(self._on_progress)
        s.job_completed.connect(self._on_job_completed)
        s.job_failed.connect(self._on_job_failed)
        s.job_paused.connect(self._on_job_paused)
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
        # Use the tolerant URL extractor so the same paste workflow handles
        # plain one-per-line input, JSON arrays, markdown lists, comma-
        # separated dumps, etc. — strips surrounding punctuation before
        # validation.
        urls = extract_urls(text)
        # Track tokens we've seen this paste to count "skipped" only for
        # genuinely unparseable input, not for parseable-but-already-queued.
        seen_codes: set[str] = set()
        for url in urls:
            code = parse_file_code(url)
            if not code:
                skipped += 1
                continue
            if code in seen_codes:
                # The same URL appeared twice in the SAME paste — silently
                # collapse instead of producing two APPROVAL rows.
                continue
            seen_codes.add(code)
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
        # Promote PAUSED rows back to PENDING so JobClaimer can pick them
        # up. The worker detects job.dest_path on disk and Range-resumes.
        resumed = 0
        for job in self._jobs:
            if job.status == JobStatus.PAUSED:
                job.status = JobStatus.PENDING
                resumed += 1
        if resumed:
            self._append_log(f"resuming {resumed} paused row(s)")
            self._persist_queue()
        if not any(j.status == JobStatus.PENDING for j in self._jobs):
            QMessageBox.information(
                self, "Nothing to do", "Queue is empty or has no pending items."
            )
            return
        # After my pause-exits-worker change, hitting Pause kills the worker
        # thread; the workers list may be empty even though there are rows
        # to process. Spawn a fresh one. If workers ARE still running
        # (e.g. some were never paused), this branch is skipped and the
        # claimer just hands them the resumed PENDING rows.
        if not self._workers:
            self._spawn_worker()
        self._refresh_running_state()

    def _on_spawn(self) -> None:
        if len(self._workers) >= self._effective_max_workers():
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

        - Start  for PENDING / FAILED / CANCELLED / APPROVAL — flip to
                 PENDING (if needed) AND spawn a worker if none are running
        - Retry  for FAILED / CANCELLED / APPROVAL — flip to PENDING only
        - Override for APPROVAL — supersede the original, flip to PENDING
        - Cancel for APPROVAL — drop this row from the queue
        """
        startable_rows = self._selected_rows_with_status(
            JobStatus.PENDING,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
            JobStatus.APPROVAL,
            JobStatus.PAUSED,
        )
        retryable_rows = self._selected_rows_with_status(
            JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.APPROVAL
        )
        approval_rows = self._selected_rows_with_status(JobStatus.APPROVAL)
        paused_rows = self._selected_rows_with_status(JobStatus.PAUSED)

        menu = QMenu(self._table)

        start_action = QAction(
            f"Start ({len(startable_rows)})" if startable_rows else "Start",
            self._table,
        )
        start_action.setEnabled(bool(startable_rows))
        start_action.triggered.connect(lambda: self._start_rows(startable_rows))
        menu.addAction(start_action)

        resume_action = QAction(
            f"Resume ({len(paused_rows)})" if paused_rows else "Resume",
            self._table,
        )
        resume_action.setEnabled(bool(paused_rows))
        resume_action.triggered.connect(lambda: self._start_rows(paused_rows))
        menu.addAction(resume_action)

        retry_action = QAction(
            f"Retry ({len(retryable_rows)})" if retryable_rows else "Retry",
            self._table,
        )
        retry_action.setEnabled(bool(retryable_rows))
        retry_action.triggered.connect(lambda: self._retry_rows(retryable_rows))
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

        menu.addSeparator()

        # Utility actions — apply to any selection, regardless of status.
        all_selected = [
            self._proxy.mapToSource(i).row()
            for i in self._table.selectionModel().selectedRows()
        ]
        all_selected = [r for r in all_selected if 0 <= r < len(self._jobs)]

        open_action = QAction("Open containing folder", self._table)
        open_action.setEnabled(bool(all_selected))
        open_action.triggered.connect(
            lambda: self._open_containing_folder(all_selected)
        )
        menu.addAction(open_action)

        delete_action = QAction(
            f"Delete record ({len(all_selected)})"
            if all_selected
            else "Delete record",
            self._table,
        )
        delete_action.setEnabled(bool(all_selected))
        delete_action.triggered.connect(lambda: self._delete_rows(all_selected))
        menu.addAction(delete_action)

        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _selected_rows_with_status(self, *allowed: JobStatus) -> List[int]:
        # selectedRows() returns proxy indices when a filter is active; map
        # back to the source model so we operate on the real self._jobs list.
        sel = self._table.selectionModel().selectedRows()
        out: List[int] = []
        for proxy_idx in sel:
            src_row = self._proxy.mapToSource(proxy_idx).row()
            if 0 <= src_row < len(self._jobs) and self._jobs[src_row].status in allowed:
                out.append(src_row)
        return out

    def _start_rows(self, rows: List[int]) -> None:
        """Make these rows happen now: flip non-PENDING rows to PENDING, then
        spawn a worker if none are running (unless we're at MAX_WORKERS, in
        which case the busy workers will claim these rows when they free up)."""
        if not rows:
            return
        for row in rows:
            job = self._jobs[row]
            if job.status != JobStatus.PENDING:
                was_paused = job.status == JobStatus.PAUSED
                job.status = JobStatus.PENDING
                job.error = None
                # PAUSED rows keep their byte/speed stats so the table
                # doesn't briefly show 0 before the worker's first emit.
                # Retry/approval flips reset everything for a fresh start.
                if not was_paused:
                    job.bytes_done = 0
                    job.total_bytes = 0
                    job.speed_bps = 0.0
                    job.eta_s = 0.0
                self._model.refresh_row(row)
        if not self._workers and len(self._workers) < MAX_WORKERS:
            self._spawn_worker()
        self._append_log(f"started {len(rows)} row(s)")
        self._persist_queue()
        self._refresh_running_state()

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

    def _open_containing_folder(self, rows: List[int]) -> None:
        """Open the first selected row's containing folder in the system
        file manager. macOS and Windows additionally select the file; on
        Linux there's no portable per-file-manager 'select' syntax, so we
        just open the parent directory."""
        if not rows:
            return
        job = self._jobs[rows[0]]
        target = (
            job.dest_path
            if job.dest_path is not None and job.dest_path.exists()
            else self._settings.download_dir
        )
        folder = target.parent if target.is_file() else target
        folder.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == "darwin" and target.is_file():
                subprocess.Popen(["open", "-R", str(target)])
            elif sys.platform.startswith("win") and target.is_file():
                subprocess.Popen(["explorer", f"/select,{target}"])
            elif sys.platform.startswith("linux"):
                subprocess.Popen(["xdg-open", str(folder)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["explorer", str(folder)])
        except OSError as e:
            QMessageBox.warning(self, "Open folder failed", str(e))

    def _delete_rows(self, rows: List[int]) -> None:
        """Remove rows from the queue. Files on disk are left alone — if
        the user wants those gone they can use Clear duplicate files or
        delete manually."""
        if not rows:
            return
        ans = QMessageBox.question(
            self,
            "Delete record?",
            f"Remove {len(rows)} row(s) from the queue?\n\n"
            "Files already downloaded to disk will NOT be deleted.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        # Remove from end so earlier indices stay valid.
        for row in sorted(rows, reverse=True):
            self._model.beginRemoveRows(QModelIndex(), row, row)
            del self._jobs[row]
            self._model.endRemoveRows()
        self._append_log(f"deleted {len(rows)} row(s) from queue")
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

    def _on_clear_duplicate_files(self) -> None:
        """Sweep 'foo (1).rar' style leftovers next to their unsuffixed base
        in the download directory. Asks for confirmation, shows the count,
        and lists the files in the detail panel so nothing is deleted
        without the user seeing exactly what's going."""
        groups = find_duplicates(self._settings.download_dir)
        if not groups:
            QMessageBox.information(
                self,
                "No duplicates found",
                f"No duplicate files in {self._settings.download_dir}.",
            )
            return
        total_to_drop = sum(len(g.drop) for g in groups)
        detail_lines = []
        for g in groups:
            detail_lines.append(f"keep:  {g.keep.name}")
            for p in g.drop:
                detail_lines.append(f"  drop: {p.name}")
            detail_lines.append("")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Clear duplicate files?")
        box.setText(
            f"Delete {total_to_drop} duplicate file(s) across "
            f"{len(groups)} group(s)?"
        )
        box.setInformativeText(
            f"Folder: {self._settings.download_dir}"
        )
        box.setDetailedText("\n".join(detail_lines))
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        box.setDefaultButton(QMessageBox.StandardButton.No)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        deleted = delete_duplicates(groups)
        self._append_log(f"removed {len(deleted)} duplicate file(s) from disk")

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
        proxy_idx = self._proxy.mapFromSource(self._model.index(idx, 0))
        if proxy_idx.isValid():
            self._table.selectRow(proxy_idx.row())
            self._table.scrollTo(proxy_idx)

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

    def _on_job_paused(self, idx: int, offset: int) -> None:
        self._model.refresh_row(idx)
        self._append_log(f"⏸ row {idx + 1} paused at {offset} bytes")
        self._persist_queue()
        self._refresh_running_state()

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
        # PAUSED rows also count as "things the user can start" — Start
        # flips them back to PENDING en masse, so the toolbar should stay
        # active until everything is truly resolved.
        has_actionable = any(
            j.status in (JobStatus.PENDING, JobStatus.PAUSED)
            for j in self._jobs
        )
        self._start_btn.setEnabled(not running and has_actionable)
        self._resume_btn.setEnabled(not running and has_actionable)
        self._pause_btn.setEnabled(running)
        effective_max = self._effective_max_workers()
        self._spawn_btn.setText(
            self._spawn_btn_label(len(self._workers), effective_max)
        )
        self._spawn_btn.setEnabled(
            len(self._workers) < effective_max and has_actionable
        )
        self._sidebar.update_counts(self._jobs)

    def _effective_max_workers(self) -> int:
        """Cap concurrent workers at MAX_HEADED_WORKERS when any worker
        would run headed — either because the user disabled headless
        globally, or because there's a redirector URL pending and the
        worker auto-overrides to headed. Avoids X11-client exhaustion."""
        any_headed = not self._settings.headless or any(
            j.status == JobStatus.PENDING and "fileaxa.com" not in j.url
            for j in self._jobs
        )
        return MAX_HEADED_WORKERS if any_headed else MAX_WORKERS

    @staticmethod
    def _spawn_btn_label(count: int, cap: int = MAX_WORKERS) -> str:
        return f"+ Worker ({count}/{cap})"

    def _refresh_sidebar_counts(self, *_args) -> None:
        self._sidebar.update_counts(self._jobs)

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
                f"{len(running)} download worker(s) in progress. "
                "Pause and exit?\n\n"
                "In-flight downloads will be saved as PAUSED with their "
                "current partial files preserved on disk. Relaunch and "
                "click Start to resume from where you left off.",
            )
            if ans != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            # pause() also sets _stop so each worker exits after marking its
            # current job PAUSED. Wait up to 10s — enough for a chunk loop
            # tick + Playwright context teardown.
            for w in running:
                w.pause()
            for w in running:
                w.wait(10000)
        event.accept()
