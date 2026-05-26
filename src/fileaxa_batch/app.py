from __future__ import annotations

import atexit
import os
import signal
import sys
from importlib.resources import files
from pathlib import Path

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from .core.cleanup import kill_orphan_chromiums
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

    # Startup sweep: previous session may have been killed mid-flight
    # (SIGKILL, crash, hung thread). Reap any Playwright Chromium
    # subprocesses that survived so we start with a clean X11 client
    # budget.
    stale = kill_orphan_chromiums()
    if stale:
        print(f"swept {stale} orphan Chromium process(es) from previous session",
              file=sys.stderr)

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

    # SIGTERM / SIGINT (Ctrl+C, systemd stop) trigger a clean Qt quit so
    # closeEvent runs and workers get a chance to mark PAUSED.
    def _on_shutdown_signal(*_args) -> None:
        app.quit()
    signal.signal(signal.SIGTERM, _on_shutdown_signal)
    signal.signal(signal.SIGINT, _on_shutdown_signal)
    # Qt's C++ event loop blocks Python's signal delivery; a periodic
    # no-op timer hands control back so SIGTERM/SIGINT get processed
    # within at most ~500ms.
    _signal_pump = QTimer()
    _signal_pump.start(500)
    _signal_pump.timeout.connect(lambda: None)

    # Last-resort cleanup: if the process exits via any path that doesn't
    # tear Playwright down properly (closeEvent hang, sys.exit before
    # the worker QThread finishes, etc.), this still kills the Chromiums.
    # SIGKILL is the one case we can't intercept — handled by the
    # startup sweep on next launch.
    atexit.register(lambda: kill_orphan_chromiums())

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
