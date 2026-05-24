class ApiError(Exception):
    """Base for Fileaxa API errors."""


class AuthError(ApiError):
    """API rejected the key (401/403 from HTTP or status in body)."""


class RateLimitError(ApiError):
    """API rate-limited us (429)."""
