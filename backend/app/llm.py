"""Async OpenAI-compatible LLM client for the relay provider.

Two capabilities the tutor needs that the translator client lacked:
  - multimodal messages (text + image_url content blocks) for sending
    material pages as images,
  - token streaming for the chat turn.

Built from a :class:`app.config.Settings` (passed in, never imported-and-mutated)
so tests can supply fakes. Light retry on transient errors for the non-streaming
planning call; the streaming call does a single attempt (a half-streamed turn
can't be transparently retried).
"""

from __future__ import annotations

import asyncio
import json
import random
from typing import TYPE_CHECKING, Any, AsyncIterator

import httpx

if TYPE_CHECKING:
    from app.config import Settings

# HTTP statuses worth retrying.
_RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}

# A content block is either a plain string or a list of OpenAI content parts
# ({"type": "text", ...} / {"type": "image_url", ...}).
Content = str | list[dict[str, Any]]
Message = dict[str, Any]


class LLMError(RuntimeError):
    """Raised when an LLM call ultimately fails (after retries)."""


class LLMClient:
    """Minimal async client for an OpenAI-compatible chat-completions endpoint."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        request_timeout_s: float = 180.0,
        max_retries: int = 3,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._max_retries = max(0, max_retries)
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=request_timeout_s)

    # ----- lifecycle -----------------------------------------------------
    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "LLMClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # ----- public API ----------------------------------------------------
    async def complete(
        self,
        *,
        model: str,
        messages: list[Message],
        temperature: float = 0.2,
        max_tokens: int | None = None,
        stop: list[str] | None = None,
    ) -> str:
        """Non-streaming completion. Retries transient failures. Returns text."""
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if stop:
            payload["stop"] = stop

        last_err: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                resp = await self._client.post(
                    self._url(), json=payload, headers=self._headers()
                )
                if resp.status_code in _RETRY_STATUS:
                    raise _Retryable(
                        f"HTTP {resp.status_code}", retry_after=_retry_after(resp)
                    )
                resp.raise_for_status()
                return _extract_content(resp.json())
            except _Retryable as e:
                last_err = e
                if attempt >= self._max_retries:
                    break
                await asyncio.sleep(e.retry_after or _backoff(attempt))
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last_err = e
                if attempt >= self._max_retries:
                    break
                await asyncio.sleep(_backoff(attempt))
            except httpx.HTTPStatusError as e:
                raise LLMError(f"LLM request failed: {e}") from e
            except (ValueError, KeyError) as e:
                last_err = e
                if attempt >= self._max_retries:
                    break
                await asyncio.sleep(_backoff(attempt))

        raise LLMError(f"LLM request failed after retries: {last_err}")

    async def stream(
        self,
        *,
        model: str,
        messages: list[Message],
        temperature: float = 0.4,
        max_tokens: int | None = None,
        stop: list[str] | None = None,
    ) -> AsyncIterator[str]:
        """Stream completion tokens as text deltas (single attempt)."""
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if stop:
            payload["stop"] = stop

        try:
            async with self._client.stream(
                "POST", self._url(), json=payload, headers=self._headers()
            ) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", "replace")
                    raise LLMError(f"LLM stream failed: HTTP {resp.status_code} {body[:500]}")
                async for delta in _iter_sse_deltas(resp):
                    yield delta
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise LLMError(f"LLM stream transport error: {e}") from e

    # ----- internals -----------------------------------------------------
    def _url(self) -> str:
        return f"{self._base_url}/chat/completions"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }


async def _iter_sse_deltas(resp: httpx.Response) -> AsyncIterator[str]:
    """Parse an OpenAI streaming SSE body, yielding content deltas."""
    async for line in resp.aiter_lines():
        if not line or not line.startswith("data:"):
            continue
        data = line[len("data:") :].strip()
        if data == "[DONE]":
            break
        try:
            obj = json.loads(data)
        except ValueError:
            continue
        choices = obj.get("choices")
        if not isinstance(choices, list) or not choices:
            continue
        delta = choices[0].get("delta") if isinstance(choices[0], dict) else None
        if not isinstance(delta, dict):
            continue
        piece = delta.get("content")
        if isinstance(piece, str) and piece:
            yield piece


class _Retryable(Exception):
    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def _backoff(attempt: int) -> float:
    """Exponential backoff with full jitter, capped at 20s."""
    base = min(20.0, 0.5 * (2**attempt))
    return random.uniform(0.0, base)


def _retry_after(resp: httpx.Response) -> float | None:
    raw = resp.headers.get("retry-after")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _extract_content(body: Any) -> str:
    """Pull the assistant text out of an OpenAI-compatible response body."""
    if not isinstance(body, dict):
        raise ValueError("response body is not an object")
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("response has no choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        raise ValueError("choice has no message")
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            p.get("text", "")
            for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        ]
        if parts:
            return "".join(parts)
    raise ValueError("message has no text content")


def build_client(settings: "Settings") -> LLMClient:
    """Construct an :class:`LLMClient` from resolved :class:`Settings`."""
    return LLMClient(
        api_key=settings.api_key,
        base_url=settings.base_url,
        request_timeout_s=settings.request_timeout_s,
        max_retries=settings.max_retries,
    )
