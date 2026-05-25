from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse

# Fileaxa file codes are alphanumeric. Observed examples are 12 chars; we accept 8-16
# to be lenient against future variation.
_FILE_CODE_RE = re.compile(r"^[a-z0-9]{8,16}$", re.IGNORECASE)

# psdly verification redirector — sits in front of fileaxa with a ~20s wait
# page before forwarding the browser to the real fileaxa.com URL.
_PSDLY_TOKEN_RE = re.compile(
    r"^https?://(?:www\.)?psdly\.co\.uk/go/([A-Za-z0-9_-]+)/?$"
)


def parse_file_code(url: str) -> Optional[str]:
    """Extract the file_code from a Fileaxa URL, or a synthetic file_code
    for a psdly redirector URL, or None if neither.

    Accepts forms like:
        https://fileaxa.com/eq080p9jv8de
        https://fileaxa.com/eq080p9jv8de/some-filename.html
        fileaxa.com/eq080p9jv8de                              (scheme inferred)
        https://www.psdly.co.uk/go/<token>                    (LOCAL EXPERIMENT)
    """
    if not url:
        return None
    url = url.strip()
    if not url:
        return None
    # psdly: synthetic file_code; the real fileaxa code is only learned
    # after the verification page redirects at download time.
    m = _PSDLY_TOKEN_RE.match(url)
    if m:
        return f"psdly_{m.group(1)}"
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
