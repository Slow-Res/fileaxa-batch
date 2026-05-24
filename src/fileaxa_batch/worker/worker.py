from __future__ import annotations

import threading
from typing import List, Optional

from PyQt6.QtCore import QThread
from playwright.sync_api import sync_playwright

from ..api.client import FileaxaClient
from ..api.errors import ApiError, AuthError
from ..core.models import DownloadJob, FileMeta, JobStatus
from ..core.settings import AppSettings, Mode
from ..secrets import get_api_key
from .browser import CancelledError, download_one
from .signals import WorkerSignals


class JobClaimer:
    """Atomic 'find a PENDING job and mark it taken' across concurrent workers.

    Without this lock, two workers can both observe the same PENDING row in
    the gap before either flips its status, and end up downloading the same
    file twice. Flipping straight to NAVIGATING serves as the claim marker —
    the per-job status callbacks overwrite it within milliseconds.
    """

    def __init__(self, jobs: List[DownloadJob]) -> None:
        self._jobs = jobs
        self._lock = threading.Lock()

    def claim_next(self) -> Optional[int]:
        with self._lock:
            for i, job in enumerate(self._jobs):
                if job.status == JobStatus.PENDING:
                    job.status = JobStatus.NAVIGATING
                    return i
            return None


def _status_to_enum(s: str) -> JobStatus:
    s = s.lower()
    if "navigating" in s:
        return JobStatus.NAVIGATING
    if "timer" in s:
        return JobStatus.WAITING_TIMER
    if "captcha" in s:
        return JobStatus.WAITING_CAPTCHA
    if "saving" in s or "downloading" in s:
        return JobStatus.DOWNLOADING
    return JobStatus.NAVIGATING


class DownloadWorker(QThread):
    """Background thread that drives Playwright and processes DownloadJob items.

    The `jobs` list is shared with the GUI; the GUI appends and the worker
    reads + mutates per-job state. Mutation is single-writer (the worker is
    the only one who flips status away from PENDING).
    """

    def __init__(
        self,
        signals: WorkerSignals,
        jobs: List[DownloadJob],
        settings: AppSettings,
        claimer: JobClaimer,
        worker_id: int = 1,
        parent=None,
    ):
        super().__init__(parent)
        self.signals = signals
        self.jobs = jobs
        self.settings = settings
        self.claimer = claimer
        self.worker_id = worker_id
        self._stop = threading.Event()
        self._cancel_current = threading.Event()
        self._paused = threading.Event()

    # ---- control surface (called from GUI thread) ----

    def request_stop(self) -> None:
        self._stop.set()
        self._cancel_current.set()

    def cancel_current(self) -> None:
        self._cancel_current.set()

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    # ---- worker loop ----

    def run(self) -> None:
        api_client = self._open_api_client_if_enabled()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=self.settings.headless)
                context = browser.new_context(accept_downloads=True)
                page = context.new_page()
                try:
                    self._loop(page, api_client)
                finally:
                    try:
                        context.close()
                        browser.close()
                    except Exception:
                        pass
        except Exception as e:
            self.signals.worker_log.emit(f"worker fatal: {type(e).__name__}: {e}")
        finally:
            if api_client is not None:
                api_client.close()
            self.signals.worker_stopped.emit(self.worker_id)

    def _open_api_client_if_enabled(self) -> Optional[FileaxaClient]:
        if self.settings.mode != Mode.API:
            return None
        key = get_api_key()
        if not key:
            self.signals.worker_log.emit(
                "API mode selected but no key stored; metadata disabled"
            )
            return None
        try:
            client = FileaxaClient(key)
            info = client.get_account_info()
            self._emit_quota(info)
            return client
        except AuthError:
            self.signals.worker_log.emit(
                "API key rejected; metadata disabled for this run"
            )
            return None
        except ApiError as e:
            self.signals.worker_log.emit(f"API unavailable ({e}); metadata disabled")
            return None

    def _loop(self, page, api_client: Optional[FileaxaClient]) -> None:
        while not self._stop.is_set():
            if self._paused.is_set():
                if self._stop.wait(timeout=0.5):
                    break
                continue
            idx = self.claimer.claim_next()
            if idx is None:
                if self._stop.wait(timeout=0.5):
                    break
                continue
            self._cancel_current.clear()
            self._process(idx, page, api_client)

    def _emit_quota(self, info: dict) -> None:
        if not info:
            self.signals.quota_updated.emit("")
            return
        bits = []
        if info.get("email"):
            bits.append(str(info["email"]))
        if info.get("storage_used") is not None:
            bits.append(f"used: {info['storage_used']}")
        if info.get("storage_left") is not None:
            bits.append(f"left: {info['storage_left']}")
        if info.get("premium_expire"):
            bits.append(f"premium: {info['premium_expire']}")
        self.signals.quota_updated.emit("  ·  ".join(bits))

    def _process(self, idx: int, page, api_client: Optional[FileaxaClient]) -> None:
        job = self.jobs[idx]
        self.signals.job_started.emit(idx)

        if api_client is not None:
            job.status = JobStatus.FETCHING_METADATA
            self.signals.status_changed.emit(idx, job.status.value)
            try:
                meta: FileMeta = api_client.get_file_info(job.file_code)
                job.meta = meta
                self.signals.metadata_ready.emit(
                    idx,
                    meta.name or "",
                    meta.size if meta.size is not None else -1,
                )
            except ApiError as e:
                self.signals.worker_log.emit(
                    f"file_info failed for {job.file_code}: {e}"
                )

        try:
            path = download_one(
                page=page,
                url=job.url,
                dest_dir=self.settings.download_dir,
                free_timer_seconds=self.settings.free_timer_seconds,
                captcha_timeout_seconds=self.settings.captcha_timeout_seconds,
                cancel_check=self._cancel_current.is_set,
                on_status=lambda s, _idx=idx, _job=job: self._on_status(_idx, _job, s),
                on_progress=lambda done, total, _idx=idx, _job=job: self._on_progress(
                    _idx, _job, done, total
                ),
            )
            job.dest_path = path
            job.status = JobStatus.COMPLETED
            self.signals.job_completed.emit(idx, str(path))
        except CancelledError:
            job.status = JobStatus.CANCELLED
            self.signals.job_failed.emit(idx, "cancelled")
        except TimeoutError as e:
            job.status = JobStatus.FAILED
            job.error = str(e)
            self.signals.job_failed.emit(idx, str(e))
        except Exception as e:
            job.status = JobStatus.FAILED
            job.error = f"{type(e).__name__}: {e}"
            self.signals.job_failed.emit(idx, job.error)

    def _on_status(self, idx: int, job: DownloadJob, s: str) -> None:
        job.status = _status_to_enum(s)
        self.signals.status_changed.emit(idx, s)

    def _on_progress(self, idx: int, job: DownloadJob, done: int, total: int) -> None:
        job.bytes_done = done
        self.signals.progress.emit(idx, done, total)
