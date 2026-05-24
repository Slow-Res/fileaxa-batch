"""Thin wrapper over the `keyring` library so the rest of the app doesn't
depend on it directly."""
from __future__ import annotations

from typing import Optional

import keyring
import keyring.errors

SERVICE_NAME = "fileaxa-batch"
USERNAME = "api_key"


def get_api_key() -> Optional[str]:
    try:
        return keyring.get_password(SERVICE_NAME, USERNAME)
    except keyring.errors.KeyringError:
        return None


def set_api_key(key: str) -> None:
    keyring.set_password(SERVICE_NAME, USERNAME, key)


def clear_api_key() -> None:
    try:
        keyring.delete_password(SERVICE_NAME, USERNAME)
    except keyring.errors.PasswordDeleteError:
        pass
    except keyring.errors.KeyringError:
        pass
