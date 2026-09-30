import asyncio
import json

import httpx
import pytest

from app.agents.content_analysis_agent import ContentAnalyzer, resolve_display_values
from app.config.settings import Settings
from app.schemas.analysis import AIAnalysisResult
from app.services.llm_client import LLMClient
from tests.fakes import mock_client

SAMPLE = {"platform": "youtube", "display_name": "X", "bio": "", "items": [{"title": "Market ka next move kya hoga?"}], "tags": []}

VALID = {
    "genre": "Finance & Investment", "sub_genre": "Stock Market & Trading", "language": "Hinglish",
    "secondary_language": "None", "sentiment": "Neutral", "sentiment_score": 0.1, "genre_confidence": 0.9,
    "language_confidence": 0.9, "sentiment_confidence": 0.9, "evidence_topics": ["Nifty"],
}


def _response(payload: dict) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(payload)}}]})


def _analyzer(handler, **settings) -> ContentAnalyzer:
    cfg = Settings(**settings)
    return ContentAnalyzer(LLMClient(cfg, client=mock_client(handler)), settings=cfg)


def test_invalid_ai_output_is_rejected_then_retried():
    responses = iter([_response({**VALID, "genre": "Stock Tips"}), _response(VALID)])
    outcome = asyncio.run(_analyzer(lambda _r: next(responses)).analyze(SAMPLE))
    assert outcome.result is not None
    assert outcome.result.language == "Hinglish"


def test_ai_failure_does_not_fabricate():
    outcome = asyncio.run(_analyzer(lambda _r: _response({"genre": "nonsense"}), ai_max_retries=1).analyze(SAMPLE))
    assert outcome.result is None
    assert "unavailable" in outcome.error


def test_ai_not_configured():
    outcome = asyncio.run(_analyzer(lambda _r: _response(VALID), groq_api_key="").analyze(SAMPLE))
    assert outcome.result is None
    assert "GROQ_API_KEY" in outcome.error


def test_no_evidence_skips_llm():
    def handler(_r):
        raise AssertionError("LLM must not be called without evidence")

    outcome = asyncio.run(_analyzer(handler).analyze({"items": [], "bio": ""}))
    assert outcome.result is None


def test_sub_genre_must_match_genre():
    with pytest.raises(ValueError):
        AIAnalysisResult.model_validate({**VALID, "sub_genre": "Makeup"})


def test_low_confidence_becomes_unknown_or_needs_review():
    result = AIAnalysisResult.model_validate({**VALID, "genre_confidence": 0.2, "language_confidence": 0.3})
    values = resolve_display_values(result, 0.5)
    assert values["genre"] == "Other"
    assert values["genre_needs_review"] is True
    assert values["language"] == "Unknown"
    assert values["sentiment"] == "Neutral"
