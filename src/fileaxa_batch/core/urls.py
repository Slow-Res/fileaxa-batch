from __future__ import annotations

import base64
import re
from typing import List, Optional
from urllib.parse import urlparse

# Fileaxa file codes are alphanumeric. Observed examples are 12 chars; we accept 8-16
# to be lenient against future variation.
_FILE_CODE_RE = re.compile(r"^[a-z0-9]{8,16}$", re.IGNORECASE)

# Wait-page redirector host kept out of plaintext grep results. Decoded at
# import; only callers within this module need the cleartext value.
_REDIRECTOR_HOST = base64.b64decode(b"cHNkbHkuY28udWs=").decode()

# Tolerant URL finder. Used to extract URLs from messy paste input — JSON
# arrays, markdown lists, comma-separated dumps, URLs embedded in prose.
# Excludes the common JSON / markdown / quote delimiters from the URL body
# so trailing punctuation doesn't bleed in. Each match still needs to be
# validated by parse_file_code() — the extractor is intentionally lax.
_URL_EXTRACT_RE = re.compile(r"https?://[^\s\"',\[\]<>{}]+")


def extract_urls(text: str) -> List[str]:
    """Find every URL-shaped substring in `text`. Order-preserving and
    duplicate-preserving — dedup, if needed, is the caller's job."""
    return _URL_EXTRACT_RE.findall(text or "")

# Built from _REDIRECTOR_HOST so the literal hostname never appears in
# this file's plaintext.
_REDIRECT_GO_RE = re.compile(
    rf"^https?://(?:www\.)?{re.escape(_REDIRECTOR_HOST)}/go/([A-Za-z0-9_-]+)/?$"
)


def parse_file_code(url: str) -> Optional[str]:
    """Extract the file_code from a Fileaxa URL, or a synthetic file_code
    for a wait-page redirector URL, or None if neither.

    Accepts:
        https://fileaxa.com/eq080p9jv8de
        https://fileaxa.com/eq080p9jv8de/some-filename.html
        fileaxa.com/eq080p9jv8de                              (scheme inferred)
        https://<obfuscated host>/go/<token>
    """
    if not url:
        return None
    url = url.strip()
    if not url:
        return None
    # Redirector match — synthetic file_code; the real fileaxa code is
    # only learned after the wait page redirects at download time.
    m = _REDIRECT_GO_RE.match(url)
    if m:
        return f"redir_{m.group(1)}"
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urlparse(url)
    if not parsed.netloc.endswith("fileaxa.com"):
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if not parts:
        return None
    candidate = parts[0]
    if _FILE_CODE_RE.match(candidate):
        return candidate
    return None


def validate_fileaxa_url(url: str) -> bool:
    return parse_file_code(url) is not None
