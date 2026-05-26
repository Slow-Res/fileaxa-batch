from __future__ import annotations

import threading
from typing import List, Optional

from PyQt6.QtCore import QThread
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from ..api.client import FileaxaClient
from ..api.errors import ApiError, AuthError
from ..core.models import DownloadJob, FileMeta, JobStatus
from ..core.settings import AppSettings, Mode
from ..core.urls import parse_file_code
from ..secrets import get_api_key
from .browser import CancelledError, PausedAtOffset, download_one
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
        # Pause is a clean shutdown: tell the in-flight chunk loop to stop
        # (cancel_current) while also setting _paused so the loop saves
        # partial state as PAUSED rather than CANCELLED. Then _stop fires
        # so the worker thread exits after the current job is marked —
        # frees the Chromium browser and the thread itself. To resume, the
        # GUI spawns a fresh worker which claims the PAUSED-then-flipped-to
        # -PENDING row.
        self._paused.set()
        self._cancel_current.set()
        self._stop.set()

    def resume(self) -> None:
        # Kept for API symmetry, though after the pause-stops-worker change
        # the worker may have already exited. The GUI's resume path is to
        # spawn a new worker via _spawn_worker(), not to "unpause" a dead
        # thread.
        self._paused.clear()

    # ---- worker loop ----

    def _effective_headless(self) -> bool:
        """Override headless=True when the queue contains any non-fileaxa
        URL. Redirector pages may require user interaction (captcha,
        click-through, browser fingerprint checks) that headless modes
        often fail. Detected by URL alone — JobClaimer only ever serves
        URLs that parse_file_code accepted, so a non-fileaxa pending URL
        is by definition a redirector."""
        if not self.settings.headless:
            return False
        for job in self.jobs:
            if job.status == JobStatus.PENDING and "fileaxa.com" not in job.url:
                return False
        return True

    def run(self) -> None:
        api_client = self._open_api_client_if_enabled()
        try:
            with sync_playwright() as p:
                headless = self._effective_headless()
                if self.settings.headless and not headless:
                    self.signals.worker_log.emit(
                        f"worker {self.worker_id}: redirector URL detected, "
                        f"running headed"
                    )
                # No special launch args — the --disable-gpu / --no-zygote
                # X-savings combo was tripping Cloudflare's bot scoring
                # (WebGL reports SwiftShader, the process tree looks
                # automated). Without these flags the Chromium fingerprint
                # matches a real desktop browser. If you hit X11 client
                # exhaustion again, close some Chrome tabs or raise
                # MaxClients — see bin/diagnose-x.
                browser = p.chromium.launch(headless=headless)
                context = browser.new_context(accept_downloads=True)
                page = context.new_page()
                # Patch the page to evade bot detection. Using the
                # library's defaults — earlier we tried chrome_runtime=True
                # for Cloudflare specifically but it appears to break
                # page rendering, leaving the browser stuck on a blank /
                # half-loaded challenge page. The safer defaults still
                # handle navigator.webdriver, plugin list, WebGL vendor
                # etc., which is the bulk of bot signals anyway.
                try:
                    Stealth().apply_stealth_sync(page)
                except Exception as e:
                    self.signals.worker_log.emit(
                        f"worker {self.worker_id}: stealth setup failed "
                        f"({type(e).__name__}: {e}); continuing without it"
                    )
                try:
                    self._loop(page, api_client)
                finally:
                    try:
                        context.close()
                        browser.close()
                    except Exception:
                        pass
        except Exception as e:
            msg = str(e)
            # Translate the noisy Playwright/Chromium launch failure into a
            # one-liner that points at the actual cause.
            if "Maximum number of clients reached" in msg or "Missing X server" in msg:
                self.signals.worker_log.emit(
                    f"worker {self.worker_id} fatal: X11 is out of client "
                    "slots. Each headed Chromium uses ~50 slots and the "
                    "default cap is ~256. Pause some workers (or close "
                    "other apps) and try again."
                )
            else:
                self.signals.worker_log.emit(
                    f"worker {self.worker_id} fatal: {type(e).__name__}: {e}"
                )
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
                # Queue drained from THIS worker's perspective. Exit the
                # loop so Playwright tears the Chromium down and the
                # thread terminates. User clicks Start to spawn a fresh
                # worker when they have new URLs. Other concurrent workers
                # keep running on their own claimed jobs — A exiting
                # doesn't disturb B or C.
                self.signals.worker_log.emit(
                    f"worker {self.worker_id}: queue drained, exiting"
                )
                break
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

        # API-mode early-skip: if we already know the filename AND its size
        # matches the file already on disk, mark COMPLETED and skip the
        # whole timer/captcha round trip. If size DOESN'T match, the file
        # is partial — fall through and let download_one handle it (HTTPX
        # mode will Range-resume; Playwright will overwrite).
        if job.meta and job.meta.name:
            existing = self.settings.download_dir / job.meta.name
            if existing.exists():
                try:
                    on_disk = existing.stat().st_size
                except OSError:
                    on_disk = 0
                expected = job.meta.size or 0
                if expected > 0 and on_disk != expected:
                    job.dest_path = existing
                    job.bytes_done = on_disk
                    self.signals.worker_log.emit(
                        f"partial on disk ({on_disk}/{expected}); "
                        f"will resume {job.meta.name}"
                    )
                    # Fall through. download_one detects job.dest_path on
                    # disk and dispatches to the resume path.
                else:
                    job.dest_path = existing
                    job.status = JobStatus.COMPLETED
                    self.signals.worker_log.emit(
                        f"already on disk; skipped: {existing}"
                    )
                    self.signals.job_completed.emit(idx, str(existing))
                    return

        # Resume detection: a job whose dest_path now exists on disk —
        # either from a PAUSED row, or from the partial-mismatch branch
        # above — gets resume_target set so download_one can Range-resume.
        resume_target = (
            job.dest_path
            if job.dest_path is not None and job.dest_path.exists()
            else None
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
                on_progress=lambda done, total, spd, eta, _idx=idx, _job=job: self._on_progress(
                    _idx, _job, done, total, spd, eta
                ),
                on_metadata=lambda name, size, _idx=idx: self.signals.metadata_ready.emit(
                    _idx, name or "", size if size is not None else -1
                ),
                download_mode=self.settings.download_mode,
                on_log=self.signals.worker_log.emit,
                pause_check=self._paused.is_set,
                resume_target=resume_target,
                on_resolved_url=lambda canonical, _idx=idx, _job=job: self._on_resolved_url(
                    _idx, _job, canonical
                ),
            )
            job.dest_path = path
            job.status = JobStatus.COMPLETED
            self.signals.job_completed.emit(idx, str(path))
        except PausedAtOffset as e:
            # User clicked Pause mid-download. Keep dest_path and the
            # partial file on disk; a future Resume will pick it up via
            # the resume_target path above.
            job.bytes_done = e.offset
            job.status = JobStatus.PAUSED
            self.signals.job_paused.emit(idx, e.offset)
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

    def _on_resolved_url(
        self, idx: int, job: DownloadJob, canonical: str
    ) -> None:
        """Replace the redirector URL with the canonical fileaxa URL once
        the page exposes it. Also re-derive file_code so dedup recognises
        future pastes of the same URL via either source."""
        if job.url == canonical:
            return
        self.signals.worker_log.emit(
            f"resolved: {job.url} -> {canonical}"
        )
        job.url = canonical
        new_code = parse_file_code(canonical)
        if new_code:
            job.file_code = new_code
        # Bounce a status_changed so the GUI table refreshes the URL cell.
        self.signals.status_changed.emit(idx, job.status.value)

    def _on_status(self, idx: int, job: DownloadJob, s: str) -> None:
        job.status = _status_to_enum(s)
        self.signals.status_changed.emit(idx, s)

    def _on_progress(
        self,
        idx: int,
        job: DownloadJob,
        done: int,
        total: int,
        speed: float,
        eta: float,
    ) -> None:
        job.bytes_done = done
        job.total_bytes = total if total > 0 else 0
        job.speed_bps = speed
        job.eta_s = eta
        self.signals.progress.emit(idx, done, total, speed, eta)
