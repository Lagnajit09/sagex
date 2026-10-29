"""HTTP client for the Autosage backend.

Talks to the base URL from config, sends the API key as `X-API-Key`, unwraps the
`{success, message, data, errors}` envelope, and turns failures into a single
`ApiError` with a human-friendly message. Synchronous on purpose — callers run it
from a background worker (like shell commands) so the UI never blocks.
"""

import json
import os
from contextlib import nullcontext

import httpx

from sagex import config
from sagex.api import store

_TIMEOUT = 15.0
_WRITE_TIMEOUT = 60.0

# Gateway errors come from nginx/the platform with an HTML body — no envelope message.
_UNAVAILABLE_STATUSES = (502, 503, 504)

_UNREACHABLE = "Unable to reach the Autosage server. Check your connection and try again later."

# Optional `factory(method) -> context manager` wrapped around each request; the
# CLI installs one to show its activity line. None (e.g. in the TUI) = no indicator.
_wait_indicator = None


def set_wait_indicator(factory) -> None:
    """Install (or clear, with None) the indicator shown while a request is in flight."""
    global _wait_indicator
    _wait_indicator = factory


def waiting(method: str):
    """The installed wait indicator for one request, or a no-op."""
    return _wait_indicator(method) if _wait_indicator else nullcontext()


class ApiError(Exception):
    """A failed API call, carrying a message suitable for showing the user."""

    def __init__(self, message: str, *, status: int | None = None, errors=None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.errors = errors            # the envelope's `errors` (e.g. a code, candidates)


def _connection_error(message: str, base_url: str, exc: Exception) -> ApiError:
    """A user-facing network error. Deliberately generic: end users shouldn't see
    internal URLs or be told to start a server they don't run. SAGEX_DEBUG=1 appends
    the URL and the underlying error, for troubleshooting a custom SAGEX_API_URL."""
    if os.environ.get("SAGEX_DEBUG") == "1":
        message = f"{message} [{base_url}: {type(exc).__name__}: {exc}]"
    return ApiError(message)


class ApiClient:
    """Thin wrapper over httpx for the Autosage REST API."""

    def __init__(self, base_url: str, api_key: str | None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def get(self, path: str, params: dict | None = None, headers: dict | None = None):
        """GET `path` (e.g. '/api/workflows/') and return the envelope's `data`."""
        return self._request("GET", path, params=params, headers=headers)

    def post(self, path: str, json=None, headers: dict | None = None):
        """POST `json` to `path` (create) and return the envelope's `data`."""
        return self._request("POST", path, json=json, headers=headers)

    def put(self, path: str, json=None):
        """PUT `json` to `path` (full update) and return the envelope's `data`."""
        return self._request("PUT", path, json=json)

    def patch(self, path: str, json=None):
        """PATCH `json` to `path` (partial update) and return the envelope's `data`."""
        return self._request("PATCH", path, json=json)

    def delete(self, path: str):
        """DELETE `path` and return the envelope's `data` (usually None on success)."""
        return self._request("DELETE", path)

    def stream(self, method: str, path: str, json=None):
        """Open a Server-Sent Events endpoint and yield (event, data) frames live.

        No read timeout: a run can stay silent for minutes and the server sends no
        heartbeats. Raises ApiError on an error response or a dropped connection.
        """
        url = f"{self.base_url}{path}"
        headers = {**self._headers(), "Accept": "text/event-stream"}
        timeout = httpx.Timeout(_TIMEOUT, read=None)
        started = False
        try:
            with httpx.stream(method, url, headers=headers, json=json, timeout=timeout) as resp:
                if resp.status_code >= 400:
                    resp.read()
                    self._unwrap(resp)
                for frame in _sse_frames(resp.iter_lines()):
                    started = True
                    yield frame
        except httpx.RequestError as exc:
            if started:
                raise _connection_error(
                    "Lost the connection to the Autosage server mid-run.", self.base_url, exc
                ) from exc
            raise _connection_error(_UNREACHABLE, self.base_url, exc) from exc

    # --- internals -----------------------------------------------------------

    def _headers(self) -> dict:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    def _request(self, method: str, path: str, params: dict | None = None, json=None,
                 headers: dict | None = None):
        url = f"{self.base_url}{path}"
        is_read = method.upper() == "GET"
        try:
            with waiting(method):
                resp = httpx.request(
                    method, url, headers={**self._headers(), **(headers or {})},
                    params=params, json=json,
                    # Writes can be slow server-side (e.g. a script create uploads to storage).
                    timeout=_TIMEOUT if is_read else _WRITE_TIMEOUT,
                )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise _connection_error(_UNREACHABLE, self.base_url, exc) from exc
        except httpx.TimeoutException as exc:
            # The request reached the server, so a change may still have been applied.
            raise ApiError(
                "The server took too long to respond." if is_read else
                "The server took too long to respond — the change may still have gone "
                "through. Check before retrying."
            ) from exc
        except httpx.RequestError as exc:
            raise _connection_error(_UNREACHABLE, self.base_url, exc) from exc
        return self._unwrap(resp)

    def _unwrap(self, resp: httpx.Response):
        """Return the envelope's `data` on success; raise ApiError otherwise."""
        try:
            body = resp.json()
        except Exception:
            body = None

        if resp.status_code == 401 and self.api_key:
            raise ApiError(
                "Not authenticated — your API key is invalid or was revoked. "
                "Run: sagex auth login",
                status=401,
            )
        if resp.status_code in _UNAVAILABLE_STATUSES:
            raise ApiError(
                "The Autosage server is temporarily unavailable. Please try again later.",
                status=resp.status_code,
            )
        if resp.status_code >= 400:
            message = body.get("message") if isinstance(body, dict) else None
            errors = body.get("errors") if isinstance(body, dict) else None
            raise ApiError(message or f"Request failed ({resp.status_code}).",
                           status=resp.status_code, errors=errors)

        if isinstance(body, dict) and "data" in body:
            return body["data"]
        return body


def _sse_frames(lines):
    """Group SSE lines into (event, data) frames; data is JSON-decoded when possible."""
    event, data = "message", []
    for line in lines:
        if line.startswith("event:"):
            event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data.append(line[len("data:"):].removeprefix(" "))
        elif not line:
            if data:
                raw = "\n".join(data)
                try:
                    yield event, json.loads(raw)
                except ValueError:
                    yield event, raw
            event, data = "message", []


def build_client() -> ApiClient:
    """Build a client from the configured base URL and the stored key."""
    return ApiClient(config.api_url(), store.get_key())
