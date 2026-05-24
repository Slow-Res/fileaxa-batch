from __future__ import annotations

from PyQt6.QtWidgets import QPlainTextEdit


class UrlInput(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText(
            "Paste Fileaxa URLs here — one per line.\n"
            "e.g. https://fileaxa.com/eq080p9jv8de"
        )
