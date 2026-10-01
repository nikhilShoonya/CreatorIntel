"""Shared async HTTP client with timeouts, bounded retries and rate-limit backoff."""

import asyncio
import logging
import random
from collections.abc import Callable
from typing import Any

import httpx

from app.config.settings import get_settings
from app.utils.logging import log_event, redact

logger = logging.getLogger("creatorintel.http")

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_BACKOFF_SECONDS = 30.0

_client: httpx.AsyncClient | None = None


class HttpRequestError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None, payload: Any = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.payload = payload


def get_http_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        settings = get_settings()
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.http_timeout_seconds, connect=10.0),
            follow_redirects=False,
            headers={"User-Agent": "CreatorIntel/1.0"},
        )
    return _client


async def close_http_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _backoff_seconds(attempt: int, response: httpx.Response | None) -> float:
    if response is not None:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), MAX_BACKOFF_SECONDS)
            except ValueError:
                pass
    return min(2**attempt + random.uniform(0, 0.5), MAX_BACKOFF_SECONDS)


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


async def request_json(
    method: str,
    url: str,
    *,
    service: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    json: Any = None,
    timeout: float | None = None,
    max_retries: int | None = None,
    should_retry: Callable[[int, Any], bool] | None = None,
    on_attempt: Callable[[], None] | None = None,
    client: httpx.AsyncClient | None = None,
) -> Any:
    """Perform a JSON request. Raises HttpRequestError (never leaks URLs/credentials)."""
    settings = get_settings()
    client = client or get_http_client()
    retries = settings.http_max_retries if max_retries is None else max_retries
    last_error: HttpRequestError | None = None

    for attempt in range(retries + 1):
        response: httpx.Response | None = None
        if on_attempt is not None:
            on_attempt()
        try:
            response = await client.request(
                method, url, params=params, headers=headers, json=json,
                timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT,
            )
        except httpx.TimeoutException:
            last_error = HttpRequestError(f"{service} request timed out")
        except httpx.TransportError as exc:
            last_error = HttpRequestError(f"{service} network error: {type(exc).__name__}")
        else:
            payload = _safe_json(response)
            if response.is_success:
                if payload is None:
                    raise HttpRequestError(f"{service} returned a non-JSON response", status_code=response.status_code)
                return payload
            last_error = HttpRequestError(
                f"{service} returned HTTP {response.status_code}",
                status_code=response.status_code,
                payload=payload,
            )
            retryable = response.status_code in RETRYABLE_STATUS or (
                should_retry is not None and should_retry(response.status_code, payload)
            )
            if not retryable:
                raise last_error

        if attempt < retries:
            delay = _backoff_seconds(attempt, response)
            log_event(
                logger, logging.WARNING, "http_retry",
                service=service, attempt=attempt + 1, max_attempts=retries + 1,
                delay_s=f"{delay:.1f}", reason=redact(last_error.message),
            )
            await asyncio.sleep(delay)

    assert last_error is not None
    raise last_error
