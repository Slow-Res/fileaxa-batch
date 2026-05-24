from __future__ import annotations

from typing import Any

import httpx

from ..core.models import FileMeta
from .errors import ApiError, AuthError, RateLimitError


class FileaxaClient:
    """Sync Fileaxa REST client. Only exposes the endpoints we actually use:
    account/info (for quota display) and file/info (for filename/size enrichment).
    """

    BASE_URL = "https://fileaxa.com/api"

    def __init__(self, api_key: str, timeout: float = 15.0):
        if not api_key:
            raise ValueError("api_key is required")
        self._key = api_key
        self._client = httpx.Client(
            timeout=timeout,
            transport=httpx.HTTPTransport(retries=2),
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "FileaxaClient":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def _get(self, path: str, params: dict) -> dict:
        params = {**params, "key": self._key}
        url = f"{self.BASE_URL}{path}"
        try:
            r = self._client.get(url, params=params)
        except httpx.HTTPError as e:
            raise ApiError(f"network error: {e}") from e

        if r.status_code in (401, 403):
            raise AuthError(f"API rejected key (HTTP {r.status_code})")
        if r.status_code == 429:
            raise RateLimitError("API rate-limited")
        if r.status_code >= 500:
            raise ApiError(f"server error {r.status_code}")

        try:
            data = r.json()
        except ValueError as e:
            raise ApiError(f"non-JSON response: {e}") from e

        # Fileaxa repeats status inside the body.
        status = data.get("status")
        if status not in (200, "200"):
            msg = data.get("msg", "unknown error")
            if status in (401, 403, "401", "403"):
                raise AuthError(str(msg))
            raise ApiError(f"API error: {msg} (status={status})")
        return data

    def get_account_info(self) -> dict:
        """Returns the result dict from /api/account/info — fields vary by account
        tier; expect at least 'email' and 'storage_left'. Empty dict if absent.
        """
        data = self._get("/account/info", {})
        result = data.get("result", {})
        return result if isinstance(result, dict) else {}

    def get_file_info(self, file_code: str) -> FileMeta:
        """Fetches name + size for a file_code. Returns FileMeta with None fields
        if the API doesn't include them (e.g., file not in your account)."""
        data = self._get("/file/info", {"file_code": file_code})
        result = data.get("result")
        # /file/info returns a single-element list per the docs.
        if isinstance(result, list) and result:
            result = result[0]
        if not isinstance(result, dict):
            return FileMeta(file_code=file_code)
        size_raw = result.get("size")
        try:
            size = int(size_raw) if size_raw is not None else None
        except (TypeError, ValueError):
            size = None
        return FileMeta(
            file_code=file_code,
            name=result.get("name") or None,
            size=size,
        )
