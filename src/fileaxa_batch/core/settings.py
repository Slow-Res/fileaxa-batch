from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Mode(str, Enum):
    ANONYMOUS = "anonymous"
    API = "api"


class DownloadMode(str, Enum):
    """How the actual file bytes get to disk after the CAPTCHA flow.

    PLAYWRIGHT: download.save_as() — what Chromium has been doing forever.
        Battle-tested, exactly matches what the browser would do. Downside:
        Playwright doesn't surface byte-level progress so Speed/ETA columns
        stay empty during transfer.

    HTTPX: after capturing the download URL we cancel Playwright's transfer
        and re-fetch with httpx, copying the browser's request headers and
        cookies. Real per-chunk progress. Downside: CDN may reject the
        non-Chromium request if a header is missing or wrong; failures are
        loud rather than silently falling back.
    """
    PLAYWRIGHT = "playwright"
    HTTPX = "httpx"


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "fileaxa-batch"


def _config_path() -> Path:
    return config_dir() / "settings.json"


def _default_download_dir() -> Path:
    return Path.home() / "Downloads" / "fileaxa-batch"


@dataclass
class AppSettings:
    download_dir: Path = field(default_factory=_default_download_dir)
    mode: Mode = Mode.ANONYMOUS
    free_timer_seconds: int = 25
    captcha_timeout_seconds: int = 120
    headless: bool = False
    download_mode: DownloadMode = DownloadMode.PLAYWRIGHT

    @classmethod
    def load(cls) -> "AppSettings":
        path = _config_path()
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return cls()
        return cls(
            download_dir=Path(data.get("download_dir", str(_default_download_dir()))),
            mode=Mode(data.get("mode", Mode.ANONYMOUS.value)),
            free_timer_seconds=int(data.get("free_timer_seconds", 70)),
            captcha_timeout_seconds=int(data.get("captcha_timeout_seconds", 600)),
            headless=bool(data.get("headless", False)),
            download_mode=DownloadMode(
                data.get("download_mode", DownloadMode.PLAYWRIGHT.value)
            ),
        )

    def save(self) -> None:
        path = _config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "download_dir": str(self.download_dir),
            "mode": self.mode.value,
            "free_timer_seconds": self.free_timer_seconds,
            "captcha_timeout_seconds": self.captcha_timeout_seconds,
            "headless": self.headless,
            "download_mode": self.download_mode.value,
        }
        path.write_text(json.dumps(data, indent=2))
