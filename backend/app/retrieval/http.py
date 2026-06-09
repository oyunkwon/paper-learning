"""Cached async HTTP for the retrieval clients.

A thin wrapper over httpx that:
  - adds a short retry on transient failures + 429 (the public APIs rate-limit),
  - caches successful responses on disk keyed by URL, so re-planning the same
    paper (or re-running after a crash) doesn't re-hit the APIs.

Caching is keyed by the full URL (params included). Cache is best-effort: a
read/write error never breaks a request. JSON and text bodies are supported
(arXiv returns Atom XML as text; S2/OpenAlex return JSON).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger("paper.retrieval.http")

_RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}


class RetrievalError(RuntimeError):
    """A retrieval request ultimately failed (after retries)."""


class CachedHTTP:
    """Async HTTP client with disk cache + transient-retry, shared by clients.

    ``contact_email`` is sent as a User-Agent / mailto so OpenAlex routes us to
    its faster "polite pool" (validated recommendation) and APIs can reach us.
    """

    def __init__(
        self,
        *,
        cache_dir: Path,
        contact_email: str = "paper-learning@localhost",
        timeout_s: float = 30.0,
        max_retries: int = 3,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._email = contact_email
        self._max_retries = max(0, max_retries)
        self._owns = client is None
        self._client = client or httpx.AsyncClient(
            timeout=timeout_s,
            headers={"User-Agent": f"paper-learning/0.1 (mailto:{contact_email})"},
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        if self._owns:
            await self._client.aclose()

    async def __aenter__(self) -> "CachedHTTP":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # ----- public ------------------------------------------------------
    async def get_json(
        self, url: str, *, params: dict[str, Any] | None = None, use_cache: bool = True
    ) -> Any:
        raw = await self._get(url, params=params, use_cache=use_cache)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            raise RetrievalError(f"non-JSON response from {url}: {e}") from e

    async def get_text(
        self, url: str, *, params: dict[str, Any] | None = None, use_cache: bool = True
    ) -> str:
        return await self._get(url, params=params, use_cache=use_cache)

    # ----- internals ---------------------------------------------------
    async def _get(
        self, url: str, *, params: dict[str, Any] | None, use_cache: bool
    ) -> str:
        full = self._full_url(url, params)
        cache_file = self._cache_path(full)
        if use_cache:
            cached = self._read_cache(cache_file)
            if cached is not None:
                return cached

        last_err: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                resp = await self._client.get(url, params=params)
                if resp.status_code in _RETRY_STATUS:
                    if attempt >= self._max_retries:
                        raise RetrievalError(
                            f"{url} -> HTTP {resp.status_code} after retries"
                        )
                    await asyncio.sleep(_backoff(attempt, resp))
                    continue
                resp.raise_for_status()
                body = resp.text
                self._write_cache(cache_file, body)
                return body
            except httpx.HTTPStatusError as e:
                # 4xx (other than the retryable set) won't improve on retry.
                raise RetrievalError(f"{url} -> {e}") from e
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last_err = e
                if attempt >= self._max_retries:
                    break
                await asyncio.sleep(_backoff(attempt, None))
        raise RetrievalError(f"{url} failed after retries: {last_err}")

    def _full_url(self, url: str, params: dict[str, Any] | None) -> str:
        if not params:
            return url
        query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}{query}"

    def _cache_path(self, full_url: str) -> Path:
        digest = hashlib.sha256(full_url.encode("utf-8")).hexdigest()[:32]
        return self._cache_dir / f"{digest}.cache"

    def _read_cache(self, path: Path) -> str | None:
        try:
            if path.exists():
                return path.read_text(encoding="utf-8")
        except OSError:
            return None
        return None

    def _write_cache(self, path: Path, body: str) -> None:
        try:
            path.write_text(body, encoding="utf-8")
        except OSError:
            log.debug("cache write failed for %s", path)


def _backoff(attempt: int, resp: httpx.Response | None) -> float:
    if resp is not None:
        retry_after = resp.headers.get("retry-after")
        if retry_after:
            try:
                return float(retry_after)
            except ValueError:
                pass
    return random.uniform(0.0, min(10.0, 0.5 * (2**attempt)))
