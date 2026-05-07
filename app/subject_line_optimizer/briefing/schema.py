"""Pydantic schema for the campaign briefing JSON.

All fields except `campaign_name` are optional. The agent handles missing
fields gracefully and may ask for a single critical missing field
(e.g. audience.description) before proceeding. Briefings can be in any
language; the agent's prompts to the LLM remain in English.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Audience(BaseModel):
    model_config = ConfigDict(extra="ignore")

    description: str | None = None
    size_estimate: int | None = Field(default=None, ge=0)


class Constraints(BaseModel):
    model_config = ConfigDict(extra="ignore")

    subject_line_max_length: int | None = Field(default=None, ge=10, le=200)
    avoid_emojis: bool = False
    avoid_words: list[str] = Field(default_factory=list)
    require_words: list[str] = Field(default_factory=list)

    @field_validator("avoid_words", "require_words", mode="before")
    @classmethod
    def _normalize_words(cls, value: list[str] | None) -> list[str]:
        if not value:
            return []
        return [w.strip().lower() for w in value if w and w.strip()]


class CampaignBriefing(BaseModel):
    model_config = ConfigDict(extra="ignore")

    campaign_name: str = Field(min_length=1)
    objective: str | None = None
    audience: Audience = Field(default_factory=Audience)
    offer: str | None = None
    brand_voice: str | None = None
    channel: str | None = "email"
    constraints: Constraints = Field(default_factory=Constraints)

    def critical_field_missing(self) -> str | None:
        """Return the name of a single critical missing field, or None.

        Used by the agent to decide whether to ask one clarifying question.
        Only `audience.description` is treated as critical — every other
        field has a sensible default or graceful behavior.
        """
        if not self.audience.description:
            return "audience.description"
        return None


def validate_briefing(payload: dict | str) -> CampaignBriefing:
    """Parse and validate a briefing dict or JSON string."""
    if isinstance(payload, str):
        payload = json.loads(payload)
    return CampaignBriefing.model_validate(payload)


def load_example(name: str) -> CampaignBriefing:
    """Load a packaged example briefing by file stem (e.g. 'reactivation')."""
    examples_dir = Path(__file__).parent / "examples"
    path = examples_dir / f"{name}.json"
    return validate_briefing(path.read_text(encoding="utf-8"))
