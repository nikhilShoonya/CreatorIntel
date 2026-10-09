"""Content analysis - one structured LLM request per creator.

Derives ONLY qualitative fields (genre, sub-genre, language, sentiment).
Counts, views, URLs etc. always come from the platform APIs.
"""

import json
import logging
from dataclasses import dataclass, field

from pydantic import ValidationError

from app.config.settings import Settings, get_settings
from app.schemas.analysis import AIAnalysisResult, analysis_json_schema
from app.schemas.platform import ChannelProfile
from app.schemas.taxonomy import GENRE_TAXONOMY, LANGUAGES, NO_SECONDARY_LANGUAGE, UNKNOWN
from app.services.llm_client import LLMClient, LLMError
from app.utils.logging import log_event
from app.utils.text import extract_hashtags, truncate

logger = logging.getLogger("creatorintel.analysis")

MAX_ITEMS = 15
MAX_TITLE_CHARS = 150
MAX_TEXT_CHARS = 400
MAX_BIO_CHARS = 600
MAX_TAGS = 25
MAX_SAMPLE_CHARS = 9000

SYSTEM_PROMPT = f"""You classify social media creators for an internal analytics tool.
You receive a sample of a creator's recent public content (bio, titles, captions, descriptions, tags).
Classify ONLY from this evidence. Never guess from the creator's name alone.

Return:
- genre: one value from the allowed list. Use "Other" if nothing fits.
- sub_genre: must belong to the chosen genre. Allowed sub-genres per genre:
{json.dumps(GENRE_TAXONOMY, ensure_ascii=False)}
  Use "General" when the content fits the genre but no specific sub-genre.
- language: the primary language of the content. Allowed: {", ".join(LANGUAGES)}.
  "Hinglish" = Hindi written in Latin script and/or Hindi mixed with English
  (e.g. "Market ka next move kya hoga?"). Devanagari Hindi = "Hindi". Pure English = "English".
  Hashtags, brand names and financial/technical terms alone do not make content English.
- secondary_language: another clearly present language, or "None".
- sentiment: overall tone of the analysed content sample (Positive, Neutral or Negative).
  Informational/educational content is usually Neutral unless clearly upbeat or negative.
  Describe the content only - never the creator's personality or mental state.
- sentiment_score: -1 (very negative) to 1 (very positive).
- *_confidence: 0 to 1. Use low confidence (< 0.5) when evidence is thin, contradictory or mostly empty.
- evidence_topics: up to 5 short topics actually present in the content."""


PENDING_PREFIX = "AI analysis pending"


@dataclass
class AnalysisOutcome:
    result: AIAnalysisResult | None = None
    error: str | None = None
    issues: list[str] = field(default_factory=list)
    model: str | None = None  # the Groq model that produced the result (main or fallback)
    pending: bool = False  # Groq daily limit reached - analysed again automatically later


def build_content_sample(profile: ChannelProfile) -> dict:
    """Bounded, de-duplicated text evidence for the LLM (also stored for Re-analyze)."""
    seen: set[str] = set()
    items: list[dict] = []
    tags: list[str] = []
    total = 0
    for item in profile.items:
        title = truncate(item.title, MAX_TITLE_CHARS) if profile.platform == "youtube" else ""
        text = truncate(item.text, MAX_TEXT_CHARS)
        key = f"{title}|{text}".lower()
        if (not title and not text) or key in seen:
            continue
        seen.add(key)
        entry = {k: v for k, v in (("title", title), ("text", text)) if v}
        size = sum(len(v) for v in entry.values())
        if total + size > MAX_SAMPLE_CHARS or len(items) >= MAX_ITEMS:
            break
        total += size
        items.append(entry)
        for tag in [*item.tags, *extract_hashtags(item.text)]:
            tag = tag.lower().strip()
            if tag and tag not in tags and len(tags) < MAX_TAGS:
                tags.append(tag[:40])

    return {
        "platform": profile.platform,
        "display_name": truncate(profile.display_name, 120),
        "bio": truncate(profile.bio, MAX_BIO_CHARS),
        "items": items,
        "tags": tags,
    }


def has_evidence(sample: dict | None) -> bool:
    if not sample:
        return False
    return bool(sample.get("items")) or len(sample.get("bio") or "") >= 20


class ContentAnalyzer:
    def __init__(self, llm: LLMClient | None = None, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.llm = llm or LLMClient(self.settings)

    def _user_prompt(self, sample: dict) -> str:
        return "Creator content sample (JSON):\n" + json.dumps(sample, ensure_ascii=False)

    def _apply_thresholds(self, result: AIAnalysisResult) -> tuple[AIAnalysisResult, list[str]]:
        threshold = self.settings.ai_min_confidence
        issues: list[str] = []
        if result.language_confidence < threshold:
            issues.append("Language confidence too low - marked Unknown")
        if result.sentiment_confidence < threshold:
            issues.append("Sentiment confidence too low - marked Unknown")
        return result, issues

    async def analyze(self, sample: dict | None, *, creator_id: int | None = None) -> AnalysisOutcome:
        if not self.llm.configured:
            return AnalysisOutcome(error="AI analysis unavailable: GROQ_API_KEY is not configured")
        if not has_evidence(sample):
            return AnalysisOutcome(error="AI analysis skipped: not enough public text content to analyse")

        attempts = self.settings.ai_max_retries + 1
        last_error = "AI analysis failed"
        for attempt in range(1, attempts + 1):
            try:
                completion = await self.llm.complete(
                    system=SYSTEM_PROMPT,
                    user=self._user_prompt(sample or {}),
                    schema_name="creator_content_analysis",
                    schema=analysis_json_schema(),
                )
                result = AIAnalysisResult.model_validate(completion.data)
                result, issues = self._apply_thresholds(result)
                log_event(logger, logging.INFO, "ai_analysis_ok", creator_id=creator_id, attempt=attempt, model=completion.model)
                return AnalysisOutcome(result=result, issues=issues, model=completion.model)
            except ValidationError as exc:
                last_error = "AI response failed validation"
                log_event(
                    logger, logging.WARNING, "ai_analysis_invalid",
                    creator_id=creator_id, attempt=attempt, errors=exc.error_count(),
                )
            except LLMError as exc:
                if exc.quota:
                    log_event(logger, logging.WARNING, "ai_analysis_pending", creator_id=creator_id, reason=exc.message)
                    return AnalysisOutcome(error=f"{PENDING_PREFIX}: {exc.message}", pending=True)
                last_error = exc.message
                log_event(logger, logging.WARNING, "ai_analysis_error", creator_id=creator_id, attempt=attempt, reason=exc.message)
                if not exc.retryable:
                    break
        return AnalysisOutcome(error=f"AI analysis unavailable: {last_error}")


def resolve_display_values(result: AIAnalysisResult, min_confidence: float) -> dict:
    """Map a validated AI result onto stored fields, honouring confidence thresholds."""
    genre_low = result.genre_confidence < min_confidence
    return {
        "genre": "Other" if genre_low else result.genre,
        "sub_genre": None if genre_low else result.sub_genre,
        "genre_needs_review": genre_low or result.genre == "Other",
        "language": UNKNOWN if result.language_confidence < min_confidence else result.language,
        "secondary_language": None
        if result.secondary_language == NO_SECONDARY_LANGUAGE or result.language_confidence < min_confidence
        else result.secondary_language,
        "sentiment": UNKNOWN if result.sentiment_confidence < min_confidence else result.sentiment,
        "sentiment_score": round(result.sentiment_score, 3),
        "genre_confidence": round(result.genre_confidence, 3),
        "language_confidence": round(result.language_confidence, 3),
        "sentiment_confidence": round(result.sentiment_confidence, 3),
        "evidence_topics": result.evidence_topics[:5],
    }
