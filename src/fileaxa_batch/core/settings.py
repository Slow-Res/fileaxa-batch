from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Mode(str, Enum):
    ANONYMOUS = "anonymous"
    API = "api"


def _config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "fileaxa-batch"


def _config_path() -> Path:
    return _config_dir() / "settings.json"


def _default_download_dir() -> Path:
    return Path.home() / "Downloads" / "fileaxa-batch"


@dataclass
class AppSettings:
    download_dir: Path = field(default_factory=_default_download_dir)
    mode: Mode = Mode.ANONYMOUS
    free_timer_seconds: int = 25
    captcha_timeout_seconds: int = 120
    headless: bool = False

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
        }
        path.write_text(json.dumps(data, indent=2))
