from __future__ import annotations

import os
import sys
from importlib.resources import files
from pathlib import Path

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from .core.settings import AppSettings
from .gui.main_window import MainWindow


def _bootstrap_bundled_chromium() -> None:
    """When running from a PyInstaller bundle, point Playwright at the
    Chromium we shipped alongside the executable. No-op during normal
    source / pip runs (sys.frozen is only set by PyInstaller)."""
    if not getattr(sys, "frozen", False):
        return
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return  # user override wins
    browsers = Path(sys.executable).parent / "browsers"
    if browsers.exists():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers)


def _load_app_icon() -> QIcon:
    """Bundled placeholder icon. Falls back to the system 'download' theme
    icon if the SVG can't be loaded (e.g. running from a stripped zipapp)."""
    try:
        path = str(files("fileaxa_batch").joinpath("data/icon.svg"))
        icon = QIcon(path)
        if not icon.isNull():
            return icon
    except (ModuleNotFoundError, FileNotFoundError):
        pass
    return QIcon.fromTheme("download")


def main() -> int:
    _bootstrap_bundled_chromium()
    app = QApplication(sys.argv)
    app.setApplicationName("fileaxa-batch")
    app.setOrganizationName("fileaxa-batch")
    icon = _load_app_icon()
    app.setWindowIcon(icon)
    settings = AppSettings.load()
    settings.download_dir.mkdir(parents=True, exist_ok=True)
    window = MainWindow(settings)
    # Set on the window too — QApplication.setWindowIcon alone is unreliable
    # under gnome-shell; the title bar / alt-tab / taskbar pick up the
    # per-window icon more consistently.
    window.setWindowIcon(icon)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
