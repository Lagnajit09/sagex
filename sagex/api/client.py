"""HTTP client for the Autosage backend.

Talks to the base URL from config, sends the API key as `X-API-Key`, unwraps the
`{success, message, data, errors}` envelope, and turns failures into a single
`ApiError` with a human-friendly message. Synchronous on purpose — callers run it
from a background worker (like shell commands) so the UI never blocks.
"""

import httpx

from sagex import config
from sagex.api import store

_TIMEOUT = 15.0


class ApiError(Exception):
    """A failed API call, carrying a message suitable for showing the user."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


class ApiClient:
    """Thin wrapper over httpx for the Autosage REST API."""

    def __init__(self, base_url: str, api_key: str | None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def get(self, path: str, params: dict | None = None):
        """GET `path` (e.g. '/api/workflows/') and return the envelope's `data`."""
        return self._request("GET", path, params=params)

    # --- internals -----------------------------------------------------------

    def _headers(self) -> dict:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    def _request(self, method: str, path: str, params: dict | None = None, json=None):
        url = f"{self.base_url}{path}"
        try:
            resp = httpx.request(
                method, url, headers=self._headers(),
                params=params, json=json, timeout=_TIMEOUT,
            )
        except httpx.RequestError as exc:
            raise ApiError(
                f"Can't reach the backend at {self.base_url}. Is it running?"
            ) from exc
        return self._unwrap(resp)

    def _unwrap(self, resp: httpx.Response):
        """Return the envelope's `data` on success; raise ApiError otherwise."""
        try:
            body = resp.json()
        except Exception:
            body = None

        if resp.status_code == 401:
            raise ApiError("Not authenticated — API key missing or invalid.", status=401)
        if resp.status_code >= 400:
            message = body.get("message") if isinstance(body, dict) else None
            raise ApiError(message or f"Request failed ({resp.status_code}).", status=resp.status_code)

        if isinstance(body, dict) and "data" in body:
            return body["data"]
        return body


def build_client() -> ApiClient:
    """Build a client from the current config (base URL) and stored key."""
    settings = config.load()
    return ApiClient(settings["api_url"], store.get_key())
