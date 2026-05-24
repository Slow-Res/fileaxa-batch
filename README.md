# fileaxa-batch

A small PyQt6 desktop app that batch-downloads files from Fileaxa free-tier URLs.

The browser-driven flow (Playwright + Chromium) handles the wait timer; you tap CAPTCHAs in the Chromium window when they appear. The app window shows the queue, progress, and (optionally) per-file metadata fetched from Fileaxa's API.

## Install

```bash
# System deps (Ubuntu/Debian — PyQt6 6.5+ needs libxcb-cursor0)
sudo apt install -y python3-venv libxcb-cursor0

# Project
cd ~/fileaxa-batch
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
playwright install chromium
```

## Run

```bash
fileaxa-batch
# or
python -m fileaxa_batch
```

## Usage

1. Paste one or more Fileaxa URLs (one per line) into the top textarea.
   Example: `https://fileaxa.com/eq080p9jv8de`
2. Click **Add to queue**.
3. Click **Start**. A Chromium window opens.
4. The app waits out Fileaxa's free-tier timer. When the CAPTCHA appears, switch to the Chromium window and solve it. The download starts; the app captures the file and saves it to your configured download folder.
5. Repeat for the next queued URL.

## Modes

- **Anonymous** (default): no API key needed. App drives only Playwright.
- **API key** (optional): paste your Fileaxa API key in Settings. The app uses the key for *metadata only* — fetching filename, size, and showing your account quota in the status bar. Downloads still go through the Playwright/CAPTCHA flow.

## Configuration

- Settings live at `$XDG_CONFIG_HOME/fileaxa-batch/settings.json` (typically `~/.config/fileaxa-batch/settings.json`).
- API key is stored in your OS keyring (gnome-keyring on Linux). Never on disk in plaintext.
- Default download folder: `~/Downloads/fileaxa-batch/`

## Limitations

- Free-tier downloads still require manual CAPTCHA solving in the Chromium window. There is no bypass.
- One download at a time. Fileaxa's free tier enforces this; the app respects it.
- Linux/macOS/Windows in theory; only tested on Linux.

## License

MIT
