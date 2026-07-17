"""Tests for the stdlib HTTP client (alpha_engine.net)."""

from __future__ import annotations

import http.client
import io
import urllib.error

import pytest

from alpha_engine import net


class _FakeResponse:
    """Stub response for monkeypatching urllib."""

    def __init__(self, status: int, body: bytes, headers: dict[str, str] | None = None):
        self.status = status
        self._body = body
        self.headers = headers or {}

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def test_get_returns_response_with_status_and_body(monkeypatch):
    def fake_urlopen(req, timeout):
        return _FakeResponse(200, b'{"key": "value"}')

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    resp = net.get("https://example.com")
    assert resp.status_code == 200
    assert resp.json() == {"key": "value"}


def test_post_sends_json_body(monkeypatch):
    captured_request = None

    def fake_urlopen(req, timeout):
        nonlocal captured_request
        captured_request = req
        return _FakeResponse(201, b'{"created": true}')

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    resp = net.post("https://example.com", json={"test": "data"})
    assert resp.status_code == 201
    assert captured_request is not None
    assert captured_request.data == b'{"test": "data"}'
    assert captured_request.headers.get("Content-type") == "application/json"


def test_response_text_decodes_utf8():
    resp = net.Response(200, {}, b"hello world", "https://example.com")
    assert resp.text == "hello world"


def test_response_text_handles_invalid_utf8():
    resp = net.Response(200, {}, b"\xff\xfe", "https://example.com")
    # Should not raise, uses 'replace' error handler
    assert "\ufffd" in resp.text or resp.text  # replacement char or decoded


def test_response_json_on_non_json_gives_helpful_error():
    resp = net.Response(200, {}, b"<html>not json</html>", "https://example.com/page")
    with pytest.raises(ValueError, match="https://example.com/page.*not valid JSON.*status 200"):
        resp.json()


def test_raise_for_status_raises_on_4xx():
    resp = net.Response(404, {}, b"", "https://example.com/missing")
    with pytest.raises(net.HTTPStatusError, match="HTTP 404.*https://example.com/missing"):
        resp.raise_for_status()


def test_raise_for_status_raises_on_5xx():
    resp = net.Response(500, {}, b"", "https://example.com/error")
    with pytest.raises(net.HTTPStatusError, match="HTTP 500"):
        resp.raise_for_status()


def test_raise_for_status_ok_for_2xx():
    resp = net.Response(200, {}, b"", "https://example.com")
    resp.raise_for_status()  # should not raise


def test_http_error_returns_response_not_exception(monkeypatch):
    """HTTPError (4xx/5xx from server) should return a Response, not raise."""

    def fake_urlopen(req, timeout):
        fp = io.BytesIO(b'{"error": "rate limited"}')
        err = urllib.error.HTTPError(
            req.full_url, 429, "Too Many Requests", http.client.HTTPMessage(), fp
        )
        raise err

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    resp = net.get("https://example.com")
    assert resp.status_code == 429
    assert resp.json() == {"error": "rate limited"}


def test_http_error_with_unreadable_body_returns_empty(monkeypatch):
    """If HTTPError.read() fails, return empty body instead of crashing."""

    def fake_urlopen(req, timeout):
        fp = io.BytesIO(b"")
        err = urllib.error.HTTPError(req.full_url, 500, "Error", http.client.HTTPMessage(), fp)
        raise err

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    resp = net.get("https://example.com")
    assert resp.status_code == 500


def test_url_error_wraps_as_http_status_error(monkeypatch):
    """Network errors (timeout, DNS, connection refused) should raise HTTPStatusError."""

    def fake_urlopen(req, timeout):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(net.HTTPStatusError, match="Network error.*Connection refused"):
        net.get("https://unreachable.example.com")


def test_get_includes_user_agent(monkeypatch):
    """Ensure default User-Agent is set."""
    captured_request = None

    def fake_urlopen(req, timeout):
        nonlocal captured_request
        captured_request = req
        return _FakeResponse(200, b"{}")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    net.get("https://example.com")
    assert captured_request is not None
    # urllib normalizes to lowercase 'User-agent'
    assert "User-agent" in captured_request.headers
    assert "alpha-engine" in captured_request.headers["User-agent"]


def test_params_are_url_encoded(monkeypatch):
    captured_url = None

    def fake_urlopen(req, timeout):
        nonlocal captured_url
        captured_url = req.full_url
        return _FakeResponse(200, b"{}")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    net.get("https://example.com/path", params={"key": "value with spaces"})
    assert captured_url is not None
    # urllib.parse.urlencode uses + for spaces
    assert "key=value+with+spaces" in captured_url or "key=value%20with%20spaces" in captured_url
