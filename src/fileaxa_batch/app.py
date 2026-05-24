from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication

from .core.settings import AppSettings
from .gui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("fileaxa-batch")
    app.setOrganizationName("fileaxa-batch")
    settings = AppSettings.load()
    settings.download_dir.mkdir(parents=True, exist_ok=True)
    window = MainWindow(settings)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
