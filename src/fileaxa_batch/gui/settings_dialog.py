from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..core.settings import AppSettings, DownloadMode, Mode
from ..secrets import clear_api_key, get_api_key, set_api_key


def _row(*widgets: QWidget) -> QWidget:
    box = QHBoxLayout()
    box.setContentsMargins(0, 0, 0, 0)
    for w in widgets:
        box.addWidget(w)
    wrapper = QWidget()
    wrapper.setLayout(box)
    return wrapper


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(520)
        self._settings = settings

        form = QFormLayout()

        # Download dir
        self._dir_edit = QLineEdit(str(settings.download_dir))
        dir_pick = QPushButton("Browse…")
        dir_pick.clicked.connect(self._pick_dir)
        form.addRow("Download folder:", _row(self._dir_edit, dir_pick))

        # Mode
        self._mode = QComboBox()
        self._mode.addItem("Anonymous (no API key)", Mode.ANONYMOUS)
        self._mode.addItem("API key (metadata enrichment)", Mode.API)
        self._mode.setCurrentIndex(1 if settings.mode == Mode.API else 0)
        form.addRow("Mode:", self._mode)

        # API key (write-only — existing key is never displayed)
        self._key_edit = QLineEdit()
        self._key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_edit.setPlaceholderText(
            "•••••• (stored in keyring)" if get_api_key() else "Paste API key to save…"
        )
        clear_btn = QPushButton("Clear stored key")
        clear_btn.clicked.connect(self._clear_key)
        form.addRow("API key:", _row(self._key_edit, clear_btn))

        # Timers
        self._timer = QSpinBox()
        self._timer.setRange(10, 600)
        self._timer.setSuffix(" s")
        self._timer.setValue(settings.free_timer_seconds)
        form.addRow("Free-tier wait timer:", self._timer)

        self._captcha = QSpinBox()
        self._captcha.setRange(60, 3600)
        self._captcha.setSuffix(" s")
        self._captcha.setValue(settings.captcha_timeout_seconds)
        form.addRow("CAPTCHA solve timeout:", self._captcha)

        self._headless = QCheckBox(
            "Run Chromium headless (no browser window — CAPTCHAs will fail)"
        )
        self._headless.setChecked(settings.headless)
        form.addRow("Headless mode:", self._headless)

        self._download_mode = QComboBox()
        self._download_mode.addItem(
            "Playwright (no Speed/ETA)", DownloadMode.PLAYWRIGHT
        )
        self._download_mode.addItem(
            "httpx (real Speed/ETA, copies browser headers)", DownloadMode.HTTPX
        )
        self._download_mode.setCurrentIndex(
            1 if settings.download_mode == DownloadMode.HTTPX else 0
        )
        form.addRow("Download transport:", self._download_mode)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout()
        root.addLayout(form)
        root.addWidget(buttons)
        self.setLayout(root)

    def _pick_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(
            self, "Choose download folder", self._dir_edit.text()
        )
        if d:
            self._dir_edit.setText(d)

    def _clear_key(self) -> None:
        clear_api_key()
        self._key_edit.clear()
        self._key_edit.setPlaceholderText("Paste API key to save…")
        QMessageBox.information(self, "Cleared", "API key removed from keyring.")

    def accept(self) -> None:
        self._settings.download_dir = Path(self._dir_edit.text()).expanduser()
        self._settings.mode = self._mode.currentData()
        self._settings.free_timer_seconds = self._timer.value()
        self._settings.captcha_timeout_seconds = self._captcha.value()
        self._settings.headless = self._headless.isChecked()
        self._settings.download_mode = self._download_mode.currentData()
        self._settings.save()
        new_key = self._key_edit.text().strip()
        if new_key:
            set_api_key(new_key)
        super().accept()
