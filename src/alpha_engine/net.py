"""Minimal stdlib HTTP client. Replaces the `requests` dependency.

Every network call in this project is a plain GET or POST that reads a JSON
(or empty) body — a ~60-line wrapper over `urllib.request` covers all of it,
so pulling in requests (plus urllib3/certifi/idna/charset-normalizer) bought
nothing. The surface deliberately mirrors the tiny slice of the requests API
the adapters and tests already use: `get()`/`post()` returning a `Response`
with `status_code`, `headers`, `json()`, and `raise_for_status()`.

HTTP error statuses (4xx/5xx) do NOT raise here — they come back as a normal
`Response` so retry loops can inspect `status_code` and `Retry-After`, and
`raise_for_status()` stays the caller's explicit choice, same as requests.
"""

from __future__ import annotations

import json as _json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

_DEFAULT_UA = "alpha-engine/0.1 (+https://github.com/)"


class HTTPStatusError(RuntimeError):
    """Raised by Response.raise_for_status() on a 4xx/5xx status."""


class Response:
    def __init__(self, status_code: int, headers: Any, body: bytes, url: str):
        self.status_code = status_code
        # urllib's HTTPMessage has case-insensitive .get(), matching requests;
        # typed Any because it may also be a plain dict (tests, error paths).
        self.headers = headers
        self.url = url
        self._body = body

    def json(self) -> Any:
        try:
            return _json.loads(self._body)
        except _json.JSONDecodeError as e:
            raise ValueError(
                f"Response from {self.url} is not valid JSON (status {self.status_code}): {e}"
            ) from e

    @property
    def text(self) -> str:
        return self._body.decode("utf-8", errors="replace")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise HTTPStatusError(f"HTTP {self.status_code} for {self.url}")


def _request(
    method: str,
    url: str,
    *,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    data: str | bytes | None = None,
    json: Any | None = None,
    timeout: float = 20,
) -> Response:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"

    hdrs = dict(headers or {})
    hdrs.setdefault("User-Agent", _DEFAULT_UA)

    body: bytes | None = None
    if json is not None:
        body = _json.dumps(json).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    elif data is not None:
        body = data.encode("utf-8") if isinstance(data, str) else data

    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - https APIs
            return Response(resp.status, resp.headers, resp.read(), url)
    except urllib.error.HTTPError as e:
        # An HTTP error status is still a response; let callers decide.
        body_bytes = b""
        try:
            body_bytes = e.read()
        except Exception:  # noqa: BLE001 - if body read fails, return empty
            pass
        return Response(e.code, e.headers or {}, body_bytes, url)
    except urllib.error.URLError as e:
        # Network error (timeout, DNS failure, connection refused).
        # Wrap as HTTPStatusError so callers see consistent exception type.
        raise HTTPStatusError(f"Network error for {url}: {e.reason}") from e


def get(
    url: str,
    *,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    data: str | bytes | None = None,
    timeout: float = 20,
) -> Response:
    return _request("GET", url, params=params, headers=headers, data=data, timeout=timeout)


def post(
    url: str,
    *,
    json: Any | None = None,
    data: str | bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 20,
) -> Response:
    return _request("POST", url, json=json, data=data, headers=headers, timeout=timeout)
