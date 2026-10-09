"""Groq Chat Completions client (OpenAI-compatible API) using strict JSON-schema output.

Groq's free plan limits are per organisation and per model (e.g. openai/gpt-oss-20b: 30 requests/min,
8,000 tokens/min, 1,000 requests/day, 200,000 tokens/day), so adding API keys does not add capacity.
This client makes the most of the quota instead:
  * requests are paced below the per-minute limits (shared by every caller in the process) instead of
    being fired and retried after HTTP 429;
  * a per-minute 429 waits for Groq's own "try again in" time - no stacked HTTP/agent retries;
  * when a model's daily limit is reached it is skipped until its reset and the next configured model
    (GROQ_FALLBACK_MODELS - each Groq model has its own limits) is used;
  * when every model is exhausted the caller gets a non-retryable "quota" error with the reset time, so the
    work is marked "AI pending" and picked up again later instead of burning more requests.

API keys (GROQ_API_KEY_1..3, or GROQ_API_KEY): the first usable key is used. The next key is tried with the same
model and request ONLY when the active key itself is unusable - invalid/revoked key (401) or a restricted account
(403). Such a key is skipped for KEY_COOLDOWN_SECONDS and then tried again (automatic recovery). Rate and daily
limits are never a reason to switch keys (Groq Services Agreement 6.3: no use intended to avoid incurring fees):
they are waited out or the work is marked pending. Keys are never logged - only their slot (key_slot=#1, #2, ...).
"""

import asyncio
import hashlib
import json
import logging
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config.settings import Settings, get_settings
from app.services.api_usage import record_groq_call, record_groq_headers
from app.services.http_client import HttpRequestError, request_json
from app.utils.logging import log_event

logger = logging.getLogger("creatorintel.llm")

# Time hooks (tests replace these for this module only, without touching the app's own event loop).
_sleep = asyncio.sleep
_now = time.time
_monotonic = time.monotonic

WINDOW_SECONDS = 60.0
SAFETY = 0.9  # stay below the published per-minute limits
MAX_MINUTE_WAITS = 4  # per-minute 429s waited out before giving up for now
MAX_WAIT_SECONDS = 65.0
DAILY_THRESHOLD_SECONDS = 120.0  # a "try again in" longer than this means a daily limit
KEY_COOLDOWN_SECONDS = 30 * 60  # an unusable key is skipped this long, then tried again


class LLMError(Exception):
    def __init__(self, message: str, *, retryable: bool = True, quota: bool = False, retry_at: datetime | None = None):
        super().__init__(message)
        self.message = message
        self.retryable = retryable
        self.quota = quota  # daily limit of every configured model reached - try again after retry_at
        self.retry_at = retry_at


@dataclass
class LLMResult:
    data: dict[str, Any]
    model: str
    tokens: int | None = None


class _KeyUnusable(Exception):
    def __init__(self, scope: str, reason: str):
        super().__init__(reason)
        self.scope = scope  # "key" (this key only) or "account" (the account behind this key)
        self.reason = reason


class _RateLimited(Exception):
    def __init__(self, wait: float, daily: bool):
        super().__init__(f"rate limited ({'daily' if daily else 'per minute'}), retry in {wait:.0f}s")
        self.wait = wait
        self.daily = daily


class _Budget:
    """Sliding one-minute window of requests/tokens for one model, shared by all callers in this process."""

    def __init__(self) -> None:
        self._events: deque[list[float]] = deque()  # [timestamp, tokens]
        self._lock = threading.Lock()
        self.exhausted_until = 0.0  # wall-clock time when a daily limit resets

    def reserve(self, tokens: int, rpm: int, tpm: int) -> tuple[float, list[float] | None]:
        """(0, entry) when a slot was reserved, otherwise (seconds to wait, None)."""
        with self._lock:
            now = _monotonic()
            while self._events and now - self._events[0][0] >= WINDOW_SECONDS:
                self._events.popleft()
            used = sum(e[1] for e in self._events)
            fits = len(self._events) < max(1, int(rpm * SAFETY)) and used + tokens <= int(tpm * SAFETY)
            if fits or not self._events:  # a single oversized request is still allowed when the window is empty
                entry = [now, float(tokens)]
                self._events.append(entry)
                return 0.0, entry
            return max(0.25, self._events[0][0] + WINDOW_SECONDS - now), None

    def settle(self, entry: list[float] | None, tokens: int | None) -> None:
        if entry is not None and tokens:
            with self._lock:
                entry[1] = float(tokens)

    def penalize(self, wait: float) -> None:
        """After a per-minute 429, treat the window as full until Groq's retry time."""
        with self._lock:
            now = _monotonic()
            self._events.append([now - WINDOW_SECONDS + min(wait, WINDOW_SECONDS), 10**9])


_BUDGETS: dict[str, _Budget] = {}
_BUDGETS_LOCK = threading.Lock()
_KEY_DISABLED_UNTIL: dict[str, float] = {}  # key fingerprint -> wall-clock time it may be tried again
_KEY_DISABLED_REASON: dict[str, str] = {}  # key fingerprint -> why it is skipped (shown in Settings)


def _fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def _budget(key: str, model: str) -> _Budget:
    """Limits belong to the account behind a key, per model."""
    with _BUDGETS_LOCK:
        return _BUDGETS.setdefault(f"{_fingerprint(key)}:{model}", _Budget())


def reset_budgets() -> None:
    """Forget pacing/exhaustion/key state (tests)."""
    with _BUDGETS_LOCK:
        _BUDGETS.clear()
        _KEY_DISABLED_UNTIL.clear()
        _KEY_DISABLED_REASON.clear()


_DURATION = re.compile(r"(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m(?!s))?(?:(\d+(?:\.\d+)?)s)?(?:(\d+(?:\.\d+)?)ms)?")


def _parse_wait(text: str | None) -> float | None:
    """'7m12.5s', '12.7s', '1h2m', '250ms' or a plain number of seconds -> seconds."""
    try:
        return float(text) if text else None
    except ValueError:
        pass
    for candidate in re.findall(r"try again in ([0-9hms.]+)", text or "") or ([text] if text else []):
        match = _DURATION.fullmatch(candidate.strip().rstrip("."))  # Groq's sentence ends with "."
        if match and any(match.groups()):
            h, m, s, ms = (float(g) if g else 0.0 for g in match.groups())
            return h * 3600 + m * 60 + s + ms / 1000
    return None


def _estimate_tokens(system: str, user: str, schema: dict) -> int:
    # ~3 characters per token is conservative for mixed English/Hindi/Hinglish; plus room for the answer.
    return (len(system) + len(user) + len(json.dumps(schema))) // 3 + 400


class LLMClient:
    def __init__(self, settings: Settings | None = None, client: httpx.AsyncClient | None = None):
        self.settings = settings or get_settings()
        self.client = client
        self._no_temperature: set[str] = set()

    @property
    def configured(self) -> bool:
        return self.settings.ai_configured

    def _active_key(self) -> tuple[int, str] | None:
        """(position, key) of the first key that is not disabled, or None."""
        now = _now()
        with _BUDGETS_LOCK:
            for position, key in enumerate(self.settings.groq_api_keys, start=1):
                if _KEY_DISABLED_UNTIL.get(_fingerprint(key), 0.0) <= now:
                    return position, key
        return None

    def _disable(self, key: str, reason: str) -> None:
        with _BUDGETS_LOCK:
            _KEY_DISABLED_UNTIL[_fingerprint(key)] = _now() + KEY_COOLDOWN_SECONDS
            _KEY_DISABLED_REASON[_fingerprint(key)] = reason

    def key_states(self) -> list[dict[str, Any]]:
        """Status of every configured key by slot (never the key itself) - for the Settings page."""
        now, states, active_seen = _now(), [], False
        for slot, key in enumerate(self.settings.groq_api_keys, start=1):
            fingerprint = _fingerprint(key)
            with _BUDGETS_LOCK:
                disabled_until = _KEY_DISABLED_UNTIL.get(fingerprint, 0.0)
                reason = _KEY_DISABLED_REASON.get(fingerprint, "")
            if disabled_until > now:
                states.append({"slot": slot, "state": "unusable", "message": reason or "Key is unusable",
                               "until": datetime.fromtimestamp(disabled_until, timezone.utc)})
                continue
            if active_seen:
                states.append({"slot": slot, "state": "standby", "message": "Used only if the active key stops working",
                               "until": None})
                continue
            active_seen = True
            exhausted = [_budget(key, m).exhausted_until for m in self.settings.groq_models]
            if exhausted and min(exhausted) > now:
                states.append({"slot": slot, "state": "daily_limit", "message": "Daily limit reached - new AI work waits as pending",
                               "until": datetime.fromtimestamp(min(exhausted), timezone.utc), "key": key})
            else:
                states.append({"slot": slot, "state": "active", "message": "In use", "until": None, "key": key})
        return states

    def quota_available(self) -> bool:
        """False while no key is usable, or the active key's models are all past their daily limit."""
        active = self._active_key()
        if active is None:
            return False
        now = _now()
        return any(_budget(active[1], m).exhausted_until <= now for m in self.settings.groq_models)

    def next_reset(self) -> datetime | None:
        active = self._active_key()
        if active is None:
            with _BUDGETS_LOCK:
                times = [_KEY_DISABLED_UNTIL.get(_fingerprint(k), 0.0) for k in self.settings.groq_api_keys]
        else:
            times = [_budget(active[1], m).exhausted_until for m in self.settings.groq_models]
        return datetime.fromtimestamp(min(times), timezone.utc) if times and min(times) > _now() else None

    async def structured_completion(
        self, *, system: str, user: str, schema_name: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        """Return the parsed JSON object produced under the given schema."""
        return (await self.complete(system=system, user=user, schema_name=schema_name, schema=schema)).data

    async def complete(self, *, system: str, user: str, schema_name: str, schema: dict[str, Any]) -> LLMResult:
        if not self.configured:
            raise LLMError("AI analysis is not configured (set GROQ_API_KEY_1 in backend/.env)", retryable=False)
        estimate = _estimate_tokens(system, user, schema)
        for _ in range(len(self.settings.groq_api_keys)):  # bounded: every key at most once per request
            active = self._active_key()
            if active is None:
                break
            position, key = active
            try:
                return await self._complete_with_key(key, estimate, system, user, schema_name, schema)
            except _KeyUnusable as exc:
                self._disable(key, exc.reason)
                log_event(logger, logging.WARNING, "llm_key_unusable", key_slot=f"#{position}", scope=exc.scope,
                          reason=exc.reason, retry_in_s=KEY_COOLDOWN_SECONDS)
        reset = self.next_reset()
        when = reset.astimezone().strftime("%d %b %H:%M") if reset else "later"
        raise LLMError(
            f"No usable Groq API key (configured keys are invalid, revoked or restricted) - AI work is pending "
            f"and will be retried automatically after {when}",
            retryable=False, quota=True, retry_at=reset,
        )

    async def _complete_with_key(
        self, key: str, estimate: int, system: str, user: str, schema_name: str, schema: dict
    ) -> LLMResult:
        models = self.settings.groq_models
        for index, model in enumerate(models):
            budget = _budget(key, model)
            if budget.exhausted_until > _now():
                continue
            try:
                return await self._complete_with(key, model, budget, estimate, system, user, schema_name, schema)
            except _RateLimited as exc:  # daily limit of this model
                budget.exhausted_until = _now() + exc.wait
                log_event(logger, logging.WARNING, "llm_daily_limit", model=model, resets_in_s=f"{exc.wait:.0f}",
                          fallback=models[index + 1] if index + 1 < len(models) else "none")
            except _ModelUnavailable:
                log_event(logger, logging.WARNING, "llm_model_unavailable", model=model)
                if index + 1 == len(models):
                    raise LLMError(f"Groq model '{model}' is not available", retryable=False) from None
        reset = self.next_reset()
        when = reset.astimezone().strftime("%d %b %H:%M") if reset else "the daily reset"
        raise LLMError(
            f"Groq daily limit reached for {', '.join(models)} - it will run again automatically after {when}",
            retryable=False, quota=True, retry_at=reset,
        )

    async def _complete_with(
        self, key: str, model: str, budget: _Budget, estimate: int, system: str, user: str, schema_name: str, schema: dict
    ) -> LLMResult:
        rpm, tpm = self.settings.groq_requests_per_minute, self.settings.groq_tokens_per_minute
        for _ in range(MAX_MINUTE_WAITS + 1):
            wait, entry = budget.reserve(estimate, rpm, tpm)
            while entry is None:  # pace below the per-minute limits instead of hitting them
                await _sleep(min(wait, MAX_WAIT_SECONDS))
                wait, entry = budget.reserve(estimate, rpm, tpm)
            try:
                result = await self._post(key, model, system, user, schema_name, schema)
            except _RateLimited as exc:
                budget.settle(entry, estimate)
                if exc.daily:
                    raise
                budget.penalize(exc.wait)
                log_event(logger, logging.INFO, "llm_minute_limit_wait", model=model, wait_s=f"{exc.wait:.1f}")
                await _sleep(min(exc.wait, MAX_WAIT_SECONDS))
                continue
            budget.settle(entry, result.tokens)
            return result
        raise LLMError("Groq per-minute limit keeps being reached - try again shortly")

    async def _post(self, key: str, model: str, system: str, user: str, schema_name: str, schema: dict) -> LLMResult:
        body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_schema", "json_schema": {"name": schema_name, "strict": True, "schema": schema}},
        }
        if model not in self._no_temperature:
            body["temperature"] = 0
        if model.startswith("openai/gpt-oss"):
            body["reasoning_effort"] = "low"  # classification needs little reasoning; saves tokens

        headers: dict[str, str] = {}

        def remember(response: httpx.Response) -> None:
            headers.update({k.lower(): v for k, v in response.headers.items() if k.lower() in ("retry-after",)})
            record_groq_headers(key, response.headers)

        try:
            data = await request_json(
                "POST", f"{self.settings.groq_base_url.rstrip('/')}/chat/completions", service="Groq API",
                json=body, headers={"Authorization": f"Bearer {key}"},
                timeout=self.settings.ai_timeout_seconds, client=self.client,
                max_retries=0,  # 429s are handled here (waiting / fallback); other failures by the caller
                on_response=remember,
            )
        except HttpRequestError as exc:
            error = (exc.payload or {}).get("error") if isinstance(exc.payload, dict) else None
            error = error if isinstance(error, dict) else {}
            message = str(error.get("message") or "")
            if exc.status_code == 429:
                wait = _parse_wait(message) or _parse_wait(headers.get("retry-after")) or 10.0
                daily = "per day" in message.lower() or "(rpd)" in message.lower() or "(tpd)" in message.lower() \
                    or wait > DAILY_THRESHOLD_SECONDS
                record_groq_call(key, 0, limit_reached=daily)
                raise _RateLimited(wait, daily) from exc
            record_groq_call(key, 0)
            if exc.status_code == 400 and error.get("param") == "temperature" and model not in self._no_temperature:
                self._no_temperature.add(model)  # some reasoning models reject temperature
                log_event(logger, logging.WARNING, "llm_temperature_unsupported", model=model)
                return await self._post(key, model, system, user, schema_name, schema)
            if exc.status_code == 401:  # this key: invalid, revoked or expired
                raise _KeyUnusable("key", "API key is invalid or revoked") from exc
            if exc.status_code == 403:  # the account behind this key: restricted / no access
                raise _KeyUnusable("account", "Account is restricted or has no access to the model") from exc
            if exc.status_code == 404:
                raise _ModelUnavailable() from exc
            if exc.status_code == 400:
                raise LLMError("LLM API rejected the request (400)", retryable=False) from exc
            raise LLMError(exc.message) from exc

        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("LLM response had an unexpected shape") from exc
        if message.get("refusal"):
            raise LLMError("LLM refused to analyse the content", retryable=False)
        content = message.get("content")
        if not content:
            raise LLMError("LLM returned an empty response")
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMError("LLM returned invalid JSON") from exc
        if not isinstance(parsed, dict):
            raise LLMError("LLM returned JSON that is not an object")
        usage = data.get("usage") if isinstance(data, dict) else None
        tokens = usage.get("total_tokens") if isinstance(usage, dict) else None
        tokens = tokens if isinstance(tokens, int) else None
        record_groq_call(key, tokens or 0)
        return LLMResult(parsed, model, tokens)


class _ModelUnavailable(Exception):
    pass
