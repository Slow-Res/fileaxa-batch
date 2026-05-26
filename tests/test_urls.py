from fileaxa_batch.core.urls import (
    _REDIRECTOR_HOST,
    extract_urls,
    parse_file_code,
    validate_fileaxa_url,
)


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


# ---- extract_urls ----------------------------------------------------------


def test_extract_plain_lines():
    assert extract_urls(
        "https://fileaxa.com/abc12345\nhttps://fileaxa.com/def67890"
    ) == ["https://fileaxa.com/abc12345", "https://fileaxa.com/def67890"]


def test_extract_json_array():
    """Common copy-paste case from API responses or browser dev tools."""
    text = (
        "[\n"
        f'  "https://www.{_REDIRECTOR_HOST}/go/tq_QMhtAVOhIGx_gJlyAWnN03to3mh8brRBEbVmGL5Q",\n'
        f'  "https://www.{_REDIRECTOR_HOST}/go/2c_uye-oy72-OsoIcn6JNrJBd5fYpN1cwYxVVNLGv3s"\n'
        "]"
    )
    urls = extract_urls(text)
    assert len(urls) == 2
    assert urls[0].endswith("/go/tq_QMhtAVOhIGx_gJlyAWnN03to3mh8brRBEbVmGL5Q")
    assert urls[1].endswith("/go/2c_uye-oy72-OsoIcn6JNrJBd5fYpN1cwYxVVNLGv3s")


def test_extract_markdown_list():
    text = "- https://fileaxa.com/abc12345\n- https://fileaxa.com/def67890"
    assert extract_urls(text) == [
        "https://fileaxa.com/abc12345",
        "https://fileaxa.com/def67890",
    ]


def test_extract_strips_trailing_punctuation():
    """Comma, quote, closing bracket — all sit between URLs in JSON / prose."""
    text = '"https://fileaxa.com/abc12345", "https://fileaxa.com/def67890"'
    urls = extract_urls(text)
    assert urls == ["https://fileaxa.com/abc12345", "https://fileaxa.com/def67890"]


def test_extract_ignores_non_urls():
    assert extract_urls("just a sentence with no urls") == []
    assert extract_urls("") == []
    assert extract_urls(None) == []  # type: ignore[arg-type]


def test_redirector_url_yields_redir_prefix():
    """The host is base64-decoded in core.urls; tests pull it from there
    instead of hardcoding the literal string."""
    token = "tq_QMhtAVOhIGx_gJlyAWnN03to3mh8brRBEbVmGL5Q"
    url = f"https://www.{_REDIRECTOR_HOST}/go/{token}"
    assert parse_file_code(url) == f"redir_{token}"


def test_redirector_url_without_www():
    token = "tq_QMhtAVOhIGx_gJlyAWnN03to3mh8brRBEbVmGL5Q"
    assert parse_file_code(
        f"https://{_REDIRECTOR_HOST}/go/{token}"
    ) == f"redir_{token}"


def test_redirector_pattern_rejects_other_hosts():
    """The /go/<token> shape on any OTHER domain must not be picked up."""
    token = "tq_QMhtAVOhIGx_gJlyAWnN03to3mh8brRBEbVmGL5Q"
    assert parse_file_code(f"https://example.com/go/{token}") is None


def test_extracted_urls_validate():
    """End-to-end: messy paste → extract → parse_file_code, each step works."""
    text = '["https://fileaxa.com/abc12345", "garbage", "https://fileaxa.com/def67890"]'
    codes = [parse_file_code(u) for u in extract_urls(text)]
    assert codes == ["abc12345", "def67890"]
