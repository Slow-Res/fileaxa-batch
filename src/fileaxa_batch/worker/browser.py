"""Per-job Playwright flow. Pure functions over a Playwright Page; no Qt here."""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable, Optional

import httpx
from playwright.sync_api import Download, Page, Request, TimeoutError as PWTimeout

from ..core.settings import DownloadMode


# Fileaxa free-tier flow uses an HTML form posted back to the same URL.
# The selectors below cover the common variants. If Fileaxa changes copy,
# add more entries here.
_FREE_BUTTON_SELECTORS = (
    'input[name="method_free"]',
    '#method_free',
    'button:has-text("Free Download")',
    'button:has-text("Slow Download")',
    'a:has-text("Free Download")',
    'a:has-text("Slow Download")',
    'input[type="submit"][value*="Free" i]',
    'input[type="submit"][value*="Slow" i]',
)

_CREATE_LINK_SELECTORS = (
    'button:has-text("Create Download Link")',
    'button:has-text("Get Download Link")',
    'a:has-text("Create Download Link")',
    'input[type="submit"][value*="Create" i]',
    'input[type="submit"][value*="Download" i]',
)


class CancelledError(RuntimeError):
    """Raised when a job is cancelled mid-flight."""


def _try_click(page: Page, selectors: tuple[str, ...]) -> bool:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() == 0:
                continue
            try:
                if not loc.is_visible():
                    continue
            except Exception:
                pass
            loc.click(timeout=2000)
            return True
        except Exception:
            continue
    return False


def _wait_with_cancel(
    seconds: int,
    cancel_check: Callable[[], bool],
    on_tick: Callable[[int], None],
) -> None:
    elapsed = 0.0
    last_reported = -1
    while elapsed < seconds:
        if cancel_check():
            raise CancelledError()
        remaining = int(seconds - elapsed)
        if remaining != last_reported:
            on_tick(remaining)
            last_reported = remaining
        time.sleep(0.5)
        elapsed += 0.5


def _unique_path(target: Path) -> Path:
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    i = 1
    while True:
        candidate = target.with_name(f"{stem} ({i}){suffix}")
        if not candidate.exists():
            return candidate
        i += 1


def _probe_total_size(page: Page, download_url: str) -> Optional[int]:
    """Issue a HEAD against the CDN URL to learn the file size before save_as
    starts. We replay the browser's cookies so the CDN treats it as the same
    session — otherwise Fileaxa would 403. Best-effort: any failure returns
    None and the progress UI falls back to 'speed only, no ETA'."""
    try:
        cookies = {
            c["name"]: c["value"] for c in page.context.cookies(download_url)
        }
        with httpx.Client(timeout=5.0, follow_redirects=True) as client:
            resp = client.head(download_url, cookies=cookies)
            cl = resp.headers.get("content-length")
            if cl and cl.isdigit():
                return int(cl)
    except (httpx.HTTPError, OSError, ValueError):
        pass
    return None


def _save_with_progress(
    download: Download,
    target: Path,
    total: Optional[int],
    cancel_check: Callable[[], bool],
    on_progress: Callable[[int, int, float, float], None],
    poll_interval: float = 0.5,
) -> None:
    """Run download.save_as in the calling thread, with a daemon poller in
    the background reporting target's growing size as speed + ETA.

    Playwright's sync API is NOT thread-safe — save_as must execute on the
    same thread that owns the playwright instance. The poller only touches
    target.stat() (pure OS) and the on_progress callback (Qt signals are
    thread-safe across QThreads), so it's free to run anywhere.
    """
    stop = threading.Event()

    def _poll() -> None:
        last_size = 0
        last_t = time.monotonic()
        while not stop.is_set():
            try:
                now_size = target.stat().st_size
            except OSError:
                now_size = last_size  # file not created yet
            now_t = time.monotonic()
            dt = now_t - last_t
            speed = (now_size - last_size) / dt if dt > 0 else 0.0
            eta = (total - now_size) / speed if (total and speed > 0) else 0.0
            on_progress(now_size, total or -1, speed, eta)
            last_size = now_size
            last_t = now_t
            stop.wait(poll_interval)

    poller = threading.Thread(target=_poll, daemon=True)
    poller.start()
    try:
        download.save_as(target)
    finally:
        stop.set()
        poller.join(timeout=2.0)


def _httpx_download(
    target: Path,
    download_url: str,
    headers: dict,
    cookies: dict,
    total: Optional[int],
    cancel_check: Callable[[], bool],
    on_progress: Callable[[int, int, float, float], None],
) -> int:
    """Stream the file via httpx with the browser's headers + cookies copied
    over. Returns the final byte count. Bypasses Playwright entirely for the
    bytes, which is the only way to surface real-time progress."""
    timeout = httpx.Timeout(connect=30.0, read=600.0, write=60.0, pool=60.0)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        with client.stream(
            "GET", download_url, headers=headers, cookies=cookies
        ) as resp:
            resp.raise_for_status()
            cl = resp.headers.get("content-length")
            actual_total = total or (int(cl) if cl and cl.isdigit() else None)

            done = 0
            last_emit_t = time.monotonic()
            last_emit_done = 0

            with target.open("wb") as f:
                for chunk in resp.iter_bytes(chunk_size=64 * 1024):
                    if cancel_check():
                        try:
                            target.unlink()
                        except OSError:
                            pass
                        raise CancelledError()
                    f.write(chunk)
                    done += len(chunk)
                    now = time.monotonic()
                    if now - last_emit_t >= 0.5:
                        dt = now - last_emit_t
                        speed = (done - last_emit_done) / dt
                        eta = (
                            (actual_total - done) / speed
                            if (actual_total and speed > 0)
                            else 0.0
                        )
                        on_progress(done, actual_total or -1, speed, eta)
                        last_emit_t = now
                        last_emit_done = done
            on_progress(done, done, 0.0, 0.0)
            return done


def download_one(
    page: Page,
    url: str,
    dest_dir: Path,
    free_timer_seconds: int,
    captcha_timeout_seconds: int,
    cancel_check: Callable[[], bool],
    on_status: Callable[[str], None],
    on_progress: Callable[[int, int, float, float], None],
    on_metadata: Optional[Callable[[str, Optional[int]], None]] = None,
    download_mode: DownloadMode = DownloadMode.PLAYWRIGHT,
    on_log: Optional[Callable[[str], None]] = None,
) -> Path:
    """Drive one Fileaxa free-tier download. Returns the saved path.

    Flow (as observed against fileaxa.com 2026-05):
      1. Navigate to the file page (it 302s to /download).
      2. Click "Free Download" — POST loads the timer page.
      3. Wait out the on-page countdown (free_timer_seconds).
      4. Click "Create download link" inside an expect_download() block.
         This POST → 302 → CDN URL; the browser fetches the file and Playwright
         fires the download event.
      5. Save the file.

    If a CAPTCHA does appear (Fileaxa may add one for some accounts/IPs/files),
    the worker times out after captcha_timeout_seconds — at which point the
    user can solve it in the visible Chromium window and retry.

    Raises:
        CancelledError if cancel_check() ever returns True.
        TimeoutError if no download starts within captcha_timeout_seconds
            (typically means a CAPTCHA needs solving or the page changed).
        RuntimeError on navigation / unknown failures.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)

    on_status("navigating")
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    except PWTimeout as e:
        raise RuntimeError(f"navigation timeout: {e}")
    if cancel_check():
        raise CancelledError()

    on_status("clicking free button")
    clicked = _try_click(page, _FREE_BUTTON_SELECTORS)
    if not clicked:
        page.wait_for_timeout(1500)
        clicked = _try_click(page, _FREE_BUTTON_SELECTORS)
    if not clicked:
        on_status("free button not found; click it manually in the browser")

    # Wait for the page POST to settle and the countdown to be present.
    try:
        page.wait_for_load_state("domcontentloaded", timeout=10_000)
    except PWTimeout:
        pass

    _wait_with_cancel(
        free_timer_seconds,
        cancel_check,
        lambda remaining: on_status(f"timer {remaining}s"),
    )

    # If we're in HTTPX mode, stash every outgoing request during the click
    # so we can later find the one matching download.url and replay its
    # headers exactly. Setting this up before the click captures the
    # redirect chain and the final CDN request.
    captured_requests: list[Request] = []
    request_listener = None
    if download_mode == DownloadMode.HTTPX:
        def _on_request(req: Request) -> None:
            if req.method == "GET":
                captured_requests.append(req)
        request_listener = _on_request
        page.on("request", request_listener)

    on_status("clicking create-download-link")
    try:
        with page.expect_download(timeout=captcha_timeout_seconds * 1000) as dl_info:
            # The countdown button is the same element that, when enabled,
            # submits the second form and triggers the 302 → CDN download.
            clicked_create = _try_click(page, _CREATE_LINK_SELECTORS)
            if not clicked_create:
                # Fallback: any visible <button> in the second form. The countdown
                # button has a long inner-text including "Wait" / "size:" so we
                # match conservatively.
                try:
                    page.locator(
                        'form button:not([disabled])'
                    ).last.click(timeout=3000)
                except Exception:
                    on_status(
                        "create-link button not found; click it manually in the browser"
                    )
        download: Download = dl_info.value
    except PWTimeout:
        if request_listener is not None:
            page.remove_listener("request", request_listener)
        raise TimeoutError(
            f"no download started within {captcha_timeout_seconds}s "
            "(may indicate CAPTCHA, page change, or rate-limit)"
        )
    finally:
        if request_listener is not None:
            # Detach listener now that we have what we need. We'll still read
            # captured_requests after this block — Python closures keep them
            # alive even after the listener is removed.
            try:
                page.remove_listener("request", request_listener)
            except Exception:
                pass

    if cancel_check():
        try:
            download.cancel()
        except Exception:
            pass
        raise CancelledError()

    suggested = download.suggested_filename or f"fileaxa-{int(time.time())}.bin"
    existing = dest_dir / suggested

    # Disk-side dedup: if a file with the suggested name is already in the
    # download directory we abort Chromium's in-flight transfer and return
    # the existing path. download.cancel() (NOT download.delete() — that
    # blocks until the transfer finishes, defeating the whole point) tells
    # the browser to stop mid-bytes.
    if existing.exists():
        on_status("already on disk; skipping save")
        # Emit metadata first so the table populates even if cancel() lags.
        try:
            existing_size = existing.stat().st_size
        except OSError:
            existing_size = 0
        if on_metadata is not None:
            on_metadata(existing.name, existing_size or None)
        on_progress(existing_size, existing_size, 0.0, 0.0)
        try:
            download.cancel()
        except Exception:
            pass
        return existing

    target = _unique_path(dest_dir / suggested)

    total = _probe_total_size(page, download.url)

    # Emit name + (possibly) total size before bytes start flowing so the table
    # populates immediately, even in headless mode where there's no Chromium UI.
    if on_metadata is not None:
        on_metadata(target.name, total)

    on_status("saving file")
    if download_mode == DownloadMode.HTTPX:
        # Find the captured GET that matches the download URL and replay
        # its headers + the matching cookies via httpx. If no match (race
        # or filter miss), fall back to save_as rather than failing — the
        # only cost is losing Speed/ETA on that one row.
        matching = next(
            (r for r in captured_requests if r.url == download.url),
            None,
        )
        if matching is None:
            if on_log:
                on_log(
                    "httpx: no captured request matched download.url; "
                    "falling back to Playwright save_as for this row"
                )
            _save_with_progress(download, target, total, cancel_check, on_progress)
        else:
            try:
                headers = matching.all_headers()
            except Exception:
                headers = dict(matching.headers)
            # httpx manages Host / Connection / Content-Length itself.
            for h in ("host", "connection", "content-length"):
                headers.pop(h, None)
            cookies = {
                c["name"]: c["value"]
                for c in page.context.cookies(download.url)
            }
            if on_log:
                on_log(
                    f"httpx GET {download.url} "
                    f"headers={sorted(headers.keys())} "
                    f"cookies={sorted(cookies.keys())}"
                )
            try:
                download.cancel()  # stop Playwright's duplicate transfer
            except Exception:
                pass
            try:
                _httpx_download(
                    target,
                    download.url,
                    headers,
                    cookies,
                    total,
                    cancel_check,
                    on_progress,
                )
            except httpx.HTTPStatusError as e:
                raise RuntimeError(
                    f"httpx download rejected by CDN "
                    f"({e.response.status_code}); the captured headers "
                    "may not match what Chromium actually sent"
                ) from e
            except httpx.HTTPError as e:
                raise RuntimeError(
                    f"httpx download failed: {type(e).__name__}: {e}"
                ) from e
    else:
        _save_with_progress(download, target, total, cancel_check, on_progress)

    if cancel_check():
        raise CancelledError()

    final: Optional[int] = None
    try:
        final = target.stat().st_size
        on_progress(final, final, 0.0, 0.0)
    except OSError:
        pass

    if on_metadata is not None:
        on_metadata(target.name, final)

    return target
