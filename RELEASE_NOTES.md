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
