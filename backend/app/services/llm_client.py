"""Groq Chat Completions client (OpenAI-compatible API) using strict JSON-schema output."""

import json
import logging
from typing import Any

import httpx

from app.config.settings import Settings, get_settings
from app.services.http_client import HttpRequestError, request_json
from app.utils.logging import log_event

logger = logging.getLogger("creatorintel.llm")


class LLMError(Exception):
    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.message = message
        self.retryable = retryable


class LLMClient:
    def __init__(self, settings: Settings | None = None, client: httpx.AsyncClient | None = None):
        self.settings = settings or get_settings()
        self.client = client
        self._send_temperature = True

    @property
    def configured(self) -> bool:
        return self.settings.ai_configured

    async def structured_completion(
        self, *, system: str, user: str, schema_name: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        """Return the parsed JSON object produced under the given schema."""
        if not self.configured:
            raise LLMError("AI analysis is not configured (set GROQ_API_KEY in backend/.env)", retryable=False)

        body: dict[str, Any] = {
            "model": self.settings.groq_model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            },
        }
        if self._send_temperature:
            body["temperature"] = 0
        if self.settings.groq_model.startswith("openai/gpt-oss"):
            # Classification needs little reasoning; keeps latency and token usage low.
            body["reasoning_effort"] = "low"

        try:
            data = await request_json(
                "POST",
                f"{self.settings.groq_base_url.rstrip('/')}/chat/completions",
                service="Groq API",
                json=body,
                headers={"Authorization": f"Bearer {self.settings.groq_api_key}"},
                timeout=self.settings.ai_timeout_seconds,
                client=self.client,
            )
        except HttpRequestError as exc:
            error = (exc.payload or {}).get("error") if isinstance(exc.payload, dict) else None
            param = (error or {}).get("param") if isinstance(error, dict) else None
            if exc.status_code == 400 and param == "temperature" and self._send_temperature:
                # Some reasoning models reject temperature; fall back to the model default.
                self._send_temperature = False
                log_event(logger, logging.WARNING, "llm_temperature_unsupported", model=self.settings.groq_model)
                return await self.structured_completion(system=system, user=user, schema_name=schema_name, schema=schema)
            if exc.status_code in (401, 403):
                raise LLMError("Groq API key is invalid or lacks access to the model", retryable=False) from exc
            if exc.status_code == 404:
                raise LLMError(f"Groq model '{self.settings.groq_model}' is not available", retryable=False) from exc
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
        return parsed
