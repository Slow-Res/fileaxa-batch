from __future__ import annotations

import sys
from importlib.resources import files

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from .core.settings import AppSettings
from .gui.main_window import MainWindow


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
    app = QApplication(sys.argv)
    app.setApplicationName("fileaxa-batch")
    app.setOrganizationName("fileaxa-batch")
    app.setWindowIcon(_load_app_icon())
    settings = AppSettings.load()
    settings.download_dir.mkdir(parents=True, exist_ok=True)
    window = MainWindow(settings)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
