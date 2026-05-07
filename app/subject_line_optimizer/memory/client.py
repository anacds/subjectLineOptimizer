"""Explicit long-term-memory reader for the iteration loop.

The Strands session manager (memory/session.py) handles raw event writes
and conversation-history retrieval automatically. This module is a thin
direct reader on top of bedrock-agentcore's MemoryClient, used by the
iteration loop to ask -- before generating any candidates -- "what do
we already know about this user that should bias generation?"

Returns MemoryContext objects (memory/schema.py). Returns empty when
MEMORY_SUBJECT_LINE_OPTIMIZERMEMORY_ID is unset (local dev without
Memory) or when the underlying retrieve calls find no matches (new
user, or within the ~60s strategy-extraction window).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from .schema import MemoryContext, Pattern

log = logging.getLogger(__name__)

_MEMORY_ID_ENV = "MEMORY_SUBJECT_LINE_OPTIMIZERMEMORY_ID"
_FACTS_NAMESPACE_TEMPLATE = "/users/{actor_id}/facts"
_PREFERENCES_NAMESPACE_TEMPLATE = "/users/{actor_id}/preferences"
_TOP_K = 5


def recall_for_user(actor_id: str, briefing: Any) -> MemoryContext:
    """Read facts and preferences for `actor_id`, biased by briefing context."""
    memory_id = os.environ.get(_MEMORY_ID_ENV)
    if not memory_id:
        return MemoryContext()

    try:
        from bedrock_agentcore.memory import MemoryClient
    except ImportError:
        log.warning("bedrock_agentcore.memory unavailable; running without recall")
        return MemoryContext()

    region = os.environ.get("AWS_REGION") or "us-east-1"
    client = MemoryClient(region_name=region)
    query = _build_query(briefing)

    facts = _retrieve(
        client,
        memory_id,
        _FACTS_NAMESPACE_TEMPLATE.format(actor_id=actor_id),
        query,
    )
    preferences = _retrieve(
        client,
        memory_id,
        _PREFERENCES_NAMESPACE_TEMPLATE.format(actor_id=actor_id),
        query,
    )
    return MemoryContext(facts=facts, preferences=preferences)


def _build_query(briefing: Any) -> str:
    """Build a single retrieval query string from briefing free-text fields."""
    parts: list[str] = []
    if hasattr(briefing, "model_dump"):
        b = briefing.model_dump()
    elif isinstance(briefing, dict):
        b = briefing
    else:
        b = {}

    for key in ("campaign_name", "objective", "brand_voice"):
        val = b.get(key)
        if val:
            parts.append(str(val))
    audience = b.get("audience") or {}
    desc = audience.get("description") if isinstance(audience, dict) else None
    if desc:
        parts.append(desc)
    return " ".join(parts) or "subject line preferences"


def _retrieve(
    client: Any,
    memory_id: str,
    namespace: str,
    query: str,
) -> list[Pattern]:
    try:
        records = client.retrieve_memories(
            memory_id=memory_id,
            namespace=namespace,
            query=query,
            top_k=_TOP_K,
        )
    except Exception as exc:  # noqa: BLE001 -- recall must never break the agent
        log.warning("memory retrieval failed for %s: %s", namespace, exc)
        return []

    patterns: list[Pattern] = []
    for record in records or []:
        content = _extract_content(record)
        if not content:
            continue
        patterns.append(
            Pattern(
                namespace=namespace,
                content=content,
                score=float(_extract_score(record)),
            )
        )
    return patterns


def _extract_content(record: Any) -> str:
    """Pull the human-readable text out of a MemoryRecord (shape varies by SDK version)."""
    if isinstance(record, dict):
        content = record.get("content")
        if isinstance(content, dict):
            return str(content.get("text") or "")
        if isinstance(content, str):
            return content
    text = getattr(record, "text", None)
    if text:
        return str(text)
    return ""


def _extract_score(record: Any) -> float:
    if isinstance(record, dict):
        return float(record.get("score") or 0.0)
    return float(getattr(record, "score", 0.0) or 0.0)
