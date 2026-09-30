"""Per-video content sentiment using the project's configured LLM (Groq, from backend/.env)."""

import logging
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config.settings import Settings, get_settings
from app.services.llm_client import LLMClient, LLMError
from app.utils.logging import log_event
from app.utils.text import truncate

logger = logging.getLogger("creatorintel.video_performance.sentiment")

MIN_CONTENT_CHARS = 15
MAX_TITLE = 200
MAX_TEXT = 1500

SYSTEM_PROMPT = """You rate the sentiment of ONE social media video's own text content (title, caption/description).
Describe the tone of the content itself - never the creator's personality or mental state.
Informational or educational content is usually Neutral unless clearly upbeat or negative.
Hashtags and links carry little meaning. If the text is too thin to judge, return low confidence (< 0.5)."""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["sentiment", "confidence"],
    "properties": {
        "sentiment": {"type": "string", "enum": ["Positive", "Neutral", "Negative"]},
        "confidence": {"type": "number", "description": "0 to 1"},
    },
}


class SentimentOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sentiment: Literal["Positive", "Neutral", "Negative"]
    confidence: float = Field(ge=0.0, le=1.0)


@dataclass
class SentimentResult:
    sentiment: str | None  # None = insufficient content / low confidence / unavailable
    confidence: float | None
    input_chars: int
    error: str | None = None


def content_text(title: str | None, caption: str | None) -> str:
    parts = [truncate(title, MAX_TITLE), truncate(caption, MAX_TEXT)]
    seen: list[str] = []
    for part in parts:
        if part and part not in seen:
            seen.append(part)
    return "\n".join(seen)


class VideoSentimentAnalyzer:
    def __init__(self, settings: Settings | None = None, llm: LLMClient | None = None):
        self.settings = settings or get_settings()
        self.llm = llm or LLMClient(self.settings)

    @property
    def model_name(self) -> str:
        return f"groq:{self.settings.groq_model}"

    async def analyze(self, title: str | None, caption: str | None, *, video_id: int | None = None) -> SentimentResult:
        text = content_text(title, caption)
        if len(text) < MIN_CONTENT_CHARS:
            return SentimentResult(None, None, len(text), "Not enough text content to analyse")
        if not self.llm.configured:
            return SentimentResult(None, None, len(text), "AI not configured (GROQ_API_KEY)")
        last_error = "AI sentiment unavailable"
        for attempt in range(1, self.settings.ai_max_retries + 2):
            try:
                raw = await self.llm.structured_completion(
                    system=SYSTEM_PROMPT, user=f"Video content:\n{text}", schema_name="video_sentiment", schema=SCHEMA
                )
                result = SentimentOut.model_validate(raw)
                if result.confidence < self.settings.ai_min_confidence:
                    return SentimentResult(None, round(result.confidence, 3), len(text), "Low confidence")
                return SentimentResult(result.sentiment, round(result.confidence, 3), len(text))
            except ValidationError:
                last_error = "AI response failed validation"
            except LLMError as exc:
                last_error = exc.message
                if not exc.retryable:
                    break
            log_event(logger, logging.WARNING, "vt_sentiment_retry", video_id=video_id, attempt=attempt, reason=last_error)
        return SentimentResult(None, None, len(text), last_error)
