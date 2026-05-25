# fileaxa-batch v0.3.0

**If you're on v0.2.0, upgrade.** v0.2.0 ships a streaming-progress
threading bug that makes Chromium download the file fine but the app
mark every job FAILED. This release fixes that, plus adds retry,
install/update shell tooling, and a friendlier launcher alias.

## Fixes

- **Downloads no longer FAIL when they succeed.** v0.2.0 ran
  `download.save_as()` inside a daemon thread to free up the worker for
  progress polling — but Playwright's sync API is not thread-safe, so
  every save_as call threw a thread-affinity error. The job was marked
  FAILED even though Chromium had downloaded the bytes. Now save_as runs
  on the Playwright-owning thread and the polling moves to a daemon
  instead. Speed/ETA columns work exactly the same; only the threading
  topology changed.

## What's new

- **Right-click Retry** on the queue table. Highlights any FAILED or
  CANCELLED rows in your selection (multi-select supported), flips them
  back to PENDING, and clears stale error / progress stats. Idle workers
  pick them up automatically.
- **`bin/install`** — idempotent setup script. Works as a `curl`-able
  one-liner for fresh installs or as `./bin/install` from an existing
  checkout. Clones the repo, makes a `.venv`, installs Playwright
  Chromium, symlinks the launcher into `~/.local/bin`. Detects
  apt/dnf/brew so it can print the right one-liner for distros that
  need extra system packages.
- **`bin/update`** — fast-forward pull from `origin/main` + `pip install
  -e .` to pick up any pyproject changes. Refuses to run with
  uncommitted edits so a stray pull can't clobber your work.
- **`bin/start`** — friendlier alias for `bin/fileaxa-batch`.
- **`bin/fileaxa-batch`** — symlink-aware launcher that runs the project
  with its local `.venv`. Suitable for symlinking into `~/.local/bin`.
- **App icon** — bundled SVG (rounded square + download arrow) replaces
  the generic Qt window icon. Shows in title bar, taskbar, and alt-tab.
  Placeholder design — easy to swap by replacing
  `src/fileaxa_batch/data/icon.svg`. Falls back to the system `download`
  theme icon if the SVG can't be loaded.

## Upgrade path

From a checkout:
```bash
cd ~/fileaxa-batch && bin/update
```

Fresh install on a new machine:
```bash
curl -fsSL https://raw.githubusercontent.com/Slow-Res/fileaxa-batch/main/bin/install | bash
```

Your `queue.db` is forward-compatible — no migration. Any rows stuck at
FAILED from v0.2.0 can be right-click → Retry'd in this version.

---

# fileaxa-batch v0.2.0

Quality-of-life release focused on the queue table — it now actually shows
you what's happening during a download instead of going dark until the file
arrives.

## What's new

- **Live Speed and ETA columns** — Playwright's `save_as` now runs in a
  background thread while the worker polls the target file every 500ms
  and emits `(bytes_done, total, speed_bps, eta_s)`. The table updates
  twice a second.
- **Filename in anonymous mode** — `download.suggested_filename` is now
  surfaced via the `metadata_ready` signal the moment the download event
  fires, so the Filename column populates without needing an API key.
- **Total size via HEAD probe** — before bytes start flowing, the worker
  sends a HEAD to the CDN URL (using the browser's cookies) to learn
  `Content-Length`. The Size column populates immediately and ETA works
  on the first poll instead of having to estimate.
- **Headless mode is genuinely usable** — with the streaming progress
  columns, you can run with Settings → Headless checked and still see
  every download's progress in the app. The Chromium window is no longer
  needed as a progress monitor.
- **Mid-download cancellation** — the polling loop checks the cancel flag
  between sleeps and calls `download.cancel()`, so the Cancel button now
  works during the byte transfer too, not just between jobs.
- **Narrower URL column** — defaulted to 220px, Interactive resize.
  Filename gets the freed space.

## Caveats

- Headless mode still fails on jobs that trigger Fileaxa's CAPTCHA — there's
  no visible window to solve it in. If that becomes a problem, an
  auto-fallback to visible Chromium per-CAPTCHA-job is a small follow-up.
- The new `total_bytes` / `speed_bps` / `eta_s` runtime stats on `DownloadJob`
  are intentionally **not** persisted to the SQLite store — a fresh process
  starts a fresh speed clock.

## Install

```bash
pip install fileaxa_batch-0.2.0-py3-none-any.whl
playwright install chromium
# Debian/Ubuntu only:
sudo apt install -y libxcb-cursor0
fileaxa-batch
```

If upgrading from 0.1.0, just `pip install --upgrade` over the existing
install. Your `queue.db` is forward-compatible — no migration needed.

---

# fileaxa-batch v0.1.0

First tagged release. PyQt6 desktop app that batch-downloads Fileaxa
free-tier files via Playwright. You solve CAPTCHAs in the Chromium window;
the app drives the wait timer, captures the download, and persists the
queue between runs.

## Highlights

- **Multi-worker downloads** — `+ Worker (N/4)` button spawns up to four
  concurrent Chromium windows. Each worker pulls from a shared queue
  through a thread-safe `JobClaimer`, so no two workers ever race onto the
  same file.
- **Persistent queue** — SQLite-backed (`~/.config/fileaxa-batch/queue.db`).
  Survives crashes, app exits, and reboots. A `Resume` button picks up
  where you left off. Jobs that were mid-download when the app exited are
  re-claimed as PENDING and restarted from the top of the flow.
- **Anonymous and API-key modes** — anonymous works without any account;
  API mode adds per-file metadata (name, size) and quota in the status bar.
  API keys live in the OS keyring, never on disk in plaintext.
- **Configurable** — download directory, mode, free-tier timer length, and
  CAPTCHA timeout are all in Settings.

## Install

Linux/macOS:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install fileaxa_batch-0.1.0-py3-none-any.whl
playwright install chromium
# Debian/Ubuntu only:
sudo apt install -y libxcb-cursor0
fileaxa-batch
```

Windows: same flow, skip the apt line.

## Known limitations

- Fileaxa's free-tier endpoint generates one-time download URLs, so partial
  files from a crashed run cannot be resumed mid-byte — the app re-runs
  the full flow for any interrupted job.
- CAPTCHA solving is manual by design.

## What's next (not in this release)

- A configurable worker cap (currently hard-coded to 4)
- Per-row cancel from the table context menu
- Bundled Chromium so users don't need a separate `playwright install` step
