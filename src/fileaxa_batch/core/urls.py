from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse

# Fileaxa file codes are alphanumeric. Observed examples are 12 chars; we accept 8-16
# to be lenient against future variation.
_FILE_CODE_RE = re.compile(r"^[a-z0-9]{8,16}$", re.IGNORECASE)


def parse_file_code(url: str) -> Optional[str]:
    """Extract the file_code from a Fileaxa URL, or None if it isn't one.

    Accepts forms like:
        https://fileaxa.com/eq080p9jv8de
        https://fileaxa.com/eq080p9jv8de/some-filename.html
        fileaxa.com/eq080p9jv8de        (scheme inferred)
    """
    if not url:
        return None
    url = url.strip()
    if not url:
        return None
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
