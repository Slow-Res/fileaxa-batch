from fileaxa_batch.core.urls import parse_file_code, validate_fileaxa_url


def test_parses_standard_url():
    assert parse_file_code("https://fileaxa.com/eq080p9jv8de") == "eq080p9jv8de"


def test_parses_url_with_filename():
    assert (
        parse_file_code("https://fileaxa.com/eq080p9jv8de/movie.mkv.html")
        == "eq080p9jv8de"
    )


def test_parses_url_without_scheme():
    assert parse_file_code("fileaxa.com/abc123def456") == "abc123def456"


def test_parses_www_subdomain():
    assert parse_file_code("https://www.fileaxa.com/abc123def456") == "abc123def456"


def test_strips_whitespace():
    assert parse_file_code("  https://fileaxa.com/abc123def456  ") == "abc123def456"


def test_rejects_other_domain():
    assert parse_file_code("https://example.com/abc123def456") is None


def test_rejects_bad_code():
    assert parse_file_code("https://fileaxa.com/!!notvalid!!") is None


def test_rejects_too_short():
    assert parse_file_code("https://fileaxa.com/abc") is None


def test_rejects_empty():
    assert parse_file_code("") is None
    assert parse_file_code("   ") is None
    assert parse_file_code(None) is None  # type: ignore[arg-type]


def test_validate_helper():
    assert validate_fileaxa_url("https://fileaxa.com/eq080p9jv8de") is True
    assert validate_fileaxa_url("https://example.com/abc") is False
