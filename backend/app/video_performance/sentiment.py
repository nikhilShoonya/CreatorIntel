"""Per-video content sentiment using the project's configured LLM (Groq, from backend/.env).

Videos are rated in batches (AI_SENTIMENT_BATCH_SIZE per request) so the instructions are sent once per batch
instead of once per video - far fewer requests and tokens against Groq's limits.
"""

import json
import logging
from collections.abc import Iterable
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
MAX_BATCH_CHARS = 9000  # keeps one batch well inside the per-minute token limit

SYSTEM_PROMPT = """You rate the sentiment of social media videos from each video's own text content (title, caption/description).
You receive a JSON list of videos, each with an "id" and its "text". Rate every video independently and return one
result per id. Describe the tone of the content itself - never the creator's personality or mental state.
Informational or educational content is usually Neutral unless clearly upbeat or negative.
Hashtags and links carry little meaning. If a text is too thin to judge, return low confidence (< 0.5) for it."""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["results"],
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "sentiment", "confidence"],
                "properties": {
                    "id": {"type": "integer"},
                    "sentiment": {"type": "string", "enum": ["Positive", "Neutral", "Negative"]},
                    "confidence": {"type": "number", "description": "0 to 1"},
                },
            },
        }
    },
}


class SentimentOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    sentiment: Literal["Positive", "Neutral", "Negative"]
    confidence: float = Field(ge=0.0, le=1.0)


class BatchOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    results: list[SentimentOut]


@dataclass
class SentimentResult:
    sentiment: str | None  # None = insufficient content / low confidence / unavailable
    confidence: float | None
    input_chars: int
    error: str | None = None
    pending: bool = False  # not rated yet (Groq limit / temporary error) - retried on the next run, not saved
    model: str | None = None


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
        return (await self.analyze_many([(video_id or 0, title, caption)]))[video_id or 0]

    async def analyze_many(self, videos: Iterable[tuple[int, str | None, str | None]]) -> dict[int, SentimentResult]:
        """Sentiment for many videos: {video_id: result}. Videos with too little text are not sent to the AI."""
        results: dict[int, SentimentResult] = {}
        todo: list[tuple[int, str]] = []
        for video_id, title, caption in videos:
            text = content_text(title, caption)
            if len(text) < MIN_CONTENT_CHARS:
                results[video_id] = SentimentResult(None, None, len(text), "Not enough text content to analyse")
            elif not self.llm.configured:
                results[video_id] = SentimentResult(None, None, len(text), "AI not configured (GROQ_API_KEY)")
            else:
                todo.append((video_id, text))

        batch: list[tuple[int, str]] = []
        size = 0
        for item in todo:
            if batch and (len(batch) >= self.settings.ai_sentiment_batch_size or size + len(item[1]) > MAX_BATCH_CHARS):
                results.update(await self._rate(batch))
                batch, size = [], 0
            batch.append(item)
            size += len(item[1])
        if batch:
            results.update(await self._rate(batch))
        return results

    async def _rate(self, batch: list[tuple[int, str]]) -> dict[int, SentimentResult]:
        lengths = {video_id: len(text) for video_id, text in batch}
        payload = json.dumps({"videos": [{"id": n, "text": text} for n, (_, text) in enumerate(batch, start=1)]},
                             ensure_ascii=False)
        last_error, pending = "AI sentiment unavailable", True
        for attempt in range(1, self.settings.ai_max_retries + 2):
            try:
                completion = await self.llm.complete(
                    system=SYSTEM_PROMPT, user=f"Videos (JSON):\n{payload}", schema_name="video_sentiment_batch", schema=SCHEMA
                )
                parsed = BatchOut.model_validate(completion.data)
            except ValidationError:
                last_error = "AI response failed validation"
            except LLMError as exc:
                last_error, pending = exc.message, exc.retryable or exc.quota
                if not exc.retryable:
                    break
            else:
                by_index = {r.id: r for r in parsed.results}
                out: dict[int, SentimentResult] = {}
                for n, (video_id, _) in enumerate(batch, start=1):
                    rated = by_index.get(n)
                    if rated is None:  # missing from the answer - try again next run
                        out[video_id] = SentimentResult(None, None, lengths[video_id], "Not rated yet", pending=True)
                    elif rated.confidence < self.settings.ai_min_confidence:
                        out[video_id] = SentimentResult(None, round(rated.confidence, 3), lengths[video_id], "Low confidence",
                                                        model=f"groq:{completion.model}")
                    else:
                        out[video_id] = SentimentResult(rated.sentiment, round(rated.confidence, 3), lengths[video_id],
                                                        model=f"groq:{completion.model}")
                log_event(logger, logging.INFO, "vt_sentiment_batch", videos=len(batch), model=completion.model, attempt=attempt)
                return out
            log_event(logger, logging.WARNING, "vt_sentiment_retry", videos=len(batch), attempt=attempt, reason=last_error)
        return {video_id: SentimentResult(None, None, lengths[video_id], last_error, pending=pending) for video_id, _ in batch}
