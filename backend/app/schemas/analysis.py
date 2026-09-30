"""Structured contract for the AI content analysis response."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.taxonomy import (
    GENRE_TAXONOMY,
    GENRES,
    LANGUAGES,
    NO_SECONDARY_LANGUAGE,
    SENTIMENTS,
    SUB_GENRES,
)


class AIAnalysisResult(BaseModel):
    """Validated LLM output. Anything outside the taxonomy is rejected."""

    model_config = ConfigDict(extra="forbid")

    genre: str
    sub_genre: str
    language: str
    secondary_language: str
    sentiment: Literal["Positive", "Neutral", "Negative"]
    sentiment_score: float = Field(ge=-1.0, le=1.0)
    genre_confidence: float = Field(ge=0.0, le=1.0)
    language_confidence: float = Field(ge=0.0, le=1.0)
    sentiment_confidence: float = Field(ge=0.0, le=1.0)
    evidence_topics: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("genre")
    @classmethod
    def _genre_in_taxonomy(cls, value: str) -> str:
        if value not in GENRES:
            raise ValueError(f"genre '{value}' is not in the allowed taxonomy")
        return value

    @field_validator("sub_genre")
    @classmethod
    def _sub_genre_known(cls, value: str) -> str:
        if value not in SUB_GENRES:
            raise ValueError(f"sub_genre '{value}' is not in the allowed taxonomy")
        return value

    @field_validator("language")
    @classmethod
    def _language_known(cls, value: str) -> str:
        if value not in LANGUAGES:
            raise ValueError(f"language '{value}' is not supported")
        return value

    @field_validator("secondary_language")
    @classmethod
    def _secondary_known(cls, value: str) -> str:
        if value not in LANGUAGES and value != NO_SECONDARY_LANGUAGE:
            raise ValueError(f"secondary_language '{value}' is not supported")
        return value

    @field_validator("evidence_topics")
    @classmethod
    def _clean_topics(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for topic in value:
            topic = " ".join(str(topic).split())[:60]
            if topic and topic not in cleaned:
                cleaned.append(topic)
        return cleaned

    @model_validator(mode="after")
    def _sub_genre_matches_genre(self) -> "AIAnalysisResult":
        if self.sub_genre not in GENRE_TAXONOMY[self.genre]:
            raise ValueError(f"sub_genre '{self.sub_genre}' does not belong to genre '{self.genre}'")
        if self.secondary_language == self.language:
            self.secondary_language = NO_SECONDARY_LANGUAGE
        return self


def analysis_json_schema() -> dict:
    """JSON schema sent to the LLM (strict structured output)."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "genre", "sub_genre", "language", "secondary_language", "sentiment", "sentiment_score",
            "genre_confidence", "language_confidence", "sentiment_confidence", "evidence_topics",
        ],
        "properties": {
            "genre": {"type": "string", "enum": GENRES},
            "sub_genre": {"type": "string", "enum": SUB_GENRES},
            "language": {"type": "string", "enum": LANGUAGES},
            "secondary_language": {"type": "string", "enum": [*LANGUAGES, NO_SECONDARY_LANGUAGE]},
            "sentiment": {"type": "string", "enum": SENTIMENTS},
            "sentiment_score": {"type": "number", "description": "-1 (very negative) to 1 (very positive)"},
            "genre_confidence": {"type": "number", "description": "0 to 1"},
            "language_confidence": {"type": "number", "description": "0 to 1"},
            "sentiment_confidence": {"type": "number", "description": "0 to 1"},
            "evidence_topics": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Up to 5 short topics observed in the content",
            },
        },
    }
