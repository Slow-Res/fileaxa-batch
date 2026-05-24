import httpx
import pytest

from fileaxa_batch.api.client import FileaxaClient
from fileaxa_batch.api.errors import ApiError, AuthError


def make_client(handler):
    client = FileaxaClient("test-key")
    client._client.close()
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def test_account_info_ok():
    def handler(request):
        assert request.url.path == "/api/account/info"
        assert request.url.params.get("key") == "test-key"
        return httpx.Response(
            200,
            json={
                "status": 200,
                "msg": "OK",
                "result": {"email": "u@x.com", "storage_left": "inf"},
            },
        )

    info = make_client(handler).get_account_info()
    assert info["email"] == "u@x.com"
    assert info["storage_left"] == "inf"


def test_file_info_parses_list_result():
    def handler(request):
        assert request.url.path == "/api/file/info"
        assert request.url.params.get("file_code") == "abc12345"
        return httpx.Response(
            200,
            json={
                "status": 200,
                "msg": "OK",
                "result": [{"filecode": "abc12345", "name": "x.bin", "size": 12345}],
            },
        )

    meta = make_client(handler).get_file_info("abc12345")
    assert meta.name == "x.bin"
    assert meta.size == 12345
    assert meta.file_code == "abc12345"


def test_file_info_missing_size_returns_none():
    def handler(request):
        return httpx.Response(
            200,
            json={"status": 200, "msg": "OK", "result": [{"name": "x.bin"}]},
        )

    meta = make_client(handler).get_file_info("abc12345")
    assert meta.name == "x.bin"
    assert meta.size is None


def test_auth_error_on_http_403():
    def handler(request):
        return httpx.Response(403, json={"status": 403, "msg": "Bad key"})

    with pytest.raises(AuthError):
        make_client(handler).get_account_info()


def test_auth_error_on_body_status_403():
    def handler(request):
        return httpx.Response(200, json={"status": 403, "msg": "Invalid key"})

    with pytest.raises(AuthError):
        make_client(handler).get_account_info()


def test_generic_api_error_on_body_error():
    def handler(request):
        return httpx.Response(200, json={"status": 500, "msg": "boom"})

    with pytest.raises(ApiError):
        make_client(handler).get_account_info()
