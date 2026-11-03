"""A small, typed-ish client for Moodle's REST Web Services.

Everything after authentication goes through here: we POST ``wstoken``,
``wsfunction`` and ``moodlewsrestformat=json`` to ``/webservice/rest/server.php``
and get JSON back. No HTML parsing, no brittle scraping.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import httpx

from .errors import SessionExpired, UpstreamError

# Moodle error codes that mean "your token is no longer good".
TOKEN_ERROR_CODES = {
    "invalidtoken",
    "accessexception",
    "invalidlogin",
    "moodle_exception_invalidtoken",
}


def flatten_params(prefix: str, value: Any, out: dict[str, str]) -> None:
    """Encode nested Python structures the way Moodle's REST layer expects.

    ``{"options": {"limit": [1, 2]}}`` becomes
    ``options[limit][0]=1&options[limit][1]=2``.
    """
    if isinstance(value, Mapping):
        for key, sub in value.items():
            flatten_params(f"{prefix}[{key}]" if prefix else str(key), sub, out)
    elif isinstance(value, (list, tuple)):
        for index, sub in enumerate(value):
            flatten_params(f"{prefix}[{index}]", sub, out)
    elif isinstance(value, bool):
        out[prefix] = "1" if value else "0"
    elif isinstance(value, (int, float)):
        out[prefix] = str(value)
    elif value is None:
        out[prefix] = ""
    else:
        out[prefix] = str(value)


def encode_params(params: Mapping[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in params.items():
        flatten_params(str(key), value, out)
    return out


class MoodleWebService:
    """Authenticated REST client for one eClass account."""

    def __init__(
        self,
        site_url: str,
        token: str,
        *,
        timeout: float = 45.0,
        max_retries: int = 2,
        user_agent: str = "eclass-api/0.1 (+https://github.com/OWNER/eclass-api)",
    ) -> None:
        self.site_url = site_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.max_retries = max_retries
        self.user_agent = user_agent

    @property
    def headers(self) -> dict[str, str]:
        """Identify ourselves honestly on API traffic."""
        return {"User-Agent": self.user_agent}

    @property
    def endpoint(self) -> str:
        return f"{self.site_url}/webservice/rest/server.php"

    async def call(self, function: str, **params: Any) -> Any:
        """Invoke one web-service function and return the decoded payload."""
        data = {
            "wstoken": self.token,
            "wsfunction": function,
            "moodlewsrestformat": "json",
            **encode_params(params),
        }

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers) as client:
                    resp = await client.post(self.endpoint, data=data)
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < self.max_retries:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                raise UpstreamError(f"Network error calling {function}: {exc}") from exc

            if resp.status_code in {429, 500, 502, 503, 504} and attempt < self.max_retries:
                await asyncio.sleep(0.75 * (attempt + 1))
                last_error = UpstreamError(f"{function}: HTTP {resp.status_code}")
                continue

            if resp.status_code >= 400:
                raise UpstreamError(
                    f"{function} returned HTTP {resp.status_code}.",
                    detail={"body": resp.text[:300]},
                )

            return self._decode(resp, function)

        raise UpstreamError(f"{function} failed after retries.", detail={"last_error": str(last_error)})

    @staticmethod
    def _decode(resp: httpx.Response, function: str) -> Any:
        text = resp.text.strip()

        # Moodle sometimes emits PHP warnings before the JSON body.
        if not text.startswith(("{", "[", '"', "null")):
            start = min(
                (idx for idx in (text.find("{"), text.find("[")) if idx >= 0),
                default=-1,
            )
            if start > 0:
                text = text[start:]

        try:
            payload = json.loads(text) if text else {}
        except ValueError as exc:
            raise UpstreamError(
                f"{function} returned a non-JSON response.",
                detail={"body": resp.text[:300]},
            ) from exc

        if isinstance(payload, dict):
            errorcode = payload.get("errorcode") or payload.get("error")
            if "exception" in payload or (isinstance(errorcode, str) and not payload.get("data")):
                if errorcode in TOKEN_ERROR_CODES:
                    raise SessionExpired(
                        "eClass rejected the stored token; the account must sign in again.",
                        detail={"errorcode": errorcode},
                    )
                if errorcode:
                    raise UpstreamError(
                        str(payload.get("message") or errorcode),
                        detail={"errorcode": errorcode},
                    )
        return payload

    async def call_raw(self, function: str, **params: Any) -> httpx.Response:
        """Escape hatch for functions that return non-JSON payloads."""
        data = {
            "wstoken": self.token,
            "wsfunction": function,
            "moodlewsrestformat": "json",
            **encode_params(params),
        }
        async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers) as client:
            return await client.post(self.endpoint, data=data)

    # -- files -----------------------------------------------------------
    def authorized_url(self, url: str) -> str:
        """Append the web-service token to a ``pluginfile.php`` URL.

        Moodle hands back file URLs that assume a browser session; external
        clients must authenticate them with ``?token=``.
        """
        if not url:
            return url
        parts = urlsplit(url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        if "token" not in query:
            query["token"] = self.token
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

    async def download(self, url: str, destination: Path, *, overwrite: bool = False) -> Path:
        """Stream a file from eClass to ``destination``."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and not overwrite:
            return destination

        authorized = self.authorized_url(url)
        tmp = destination.with_suffix(destination.suffix + ".part")
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True, headers=self.headers) as client:
                async with client.stream("GET", authorized) as resp:
                    if resp.status_code == 404:
                        raise UpstreamError(
                            "eClass returned 404 for this file (moved, deleted, or no access)."
                        )
                    if resp.status_code >= 400:
                        raise UpstreamError(f"File download failed with HTTP {resp.status_code}.")
                    content_type = resp.headers.get("content-type", "")
                    if content_type.startswith("text/html"):
                        # Moodle serves an HTML error/login page instead of the file.
                        snippet = (await resp.aread())[:300].decode("utf-8", "replace")
                        raise UpstreamError(
                            "eClass returned a web page instead of the file; the token may lack access.",
                            detail={"snippet": snippet[:200]},
                        )
                    with tmp.open("wb") as handle:
                        async for chunk in resp.aiter_bytes(64 * 1024):
                            handle.write(chunk)
        except httpx.HTTPError as exc:
            tmp.unlink(missing_ok=True)
            raise UpstreamError(f"Network error downloading file: {exc}") from exc
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

        tmp.replace(destination)
        return destination

    async def get_text(self, url: str) -> str:
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True, headers=self.headers) as client:
            resp = await client.get(self.authorized_url(url))
            resp.raise_for_status()
            return resp.text
