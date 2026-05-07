"""Wire the iteration loop and supporting services into a Strands Agent.

One callable is exposed for the runtime entrypoint:
- `run_initial_optimization(briefing, session_id, user_id)` -- runs the full
  generate/score/critique/regenerate loop and returns an IterationResult.

Builds a fresh Strands Agent backed by AgentCoreMemorySessionManager so
that conversation history, summaries, and long-term patterns are visible to
the LLM. Scoring is an in-process call to `score_subject_line` -- the
heuristic table is pure Python over a CSV, with no untrusted input and no
heavyweight dependencies, so a managed sandbox would not survive an
enterprise production review for this workload (see ARCHITECTURE.md).
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from strands import Agent

from opentelemetry import trace

from agent.iteration import IterationResult, RoundLog, run_optimization
from agent.prompts import (
    critique_prompt as build_critique_prompt,
    regenerate_prompt,
    round_one_prompt,
)
from briefing.schema import CampaignBriefing
from memory.client import recall_for_user
from memory.session import get_memory_session_manager
from model.load import load_model
from scoring.score import Rule, load_rules, score_subject_line

log = logging.getLogger(__name__)
_tracer = trace.get_tracer("subject_line_optimizer.iteration")

GENERATOR_SYSTEM_PROMPT = (
    "You are a subject-line generator for an iterative optimization loop. "
    "Match the requested archetypes and count exactly."
)

CRITIQUE_SYSTEM_PROMPT = (
    "You critique the weak candidates from one round of subject-line optimization. "
    "Produce two to four sentences of explicit, actionable guidance for the next "
    "generation round. Reference specific patterns; do not give generic advice."
)


class SubjectLineList(BaseModel):
    subject_lines: list[str] = Field(
        description="Email subject line candidates, one per requested slot."
    )


_HEURISTICS_PATH = Path(__file__).resolve().parent.parent / "data" / "heuristics.csv"


@lru_cache(maxsize=1)
def _rules() -> list[Rule]:
    """Load the heuristic rules once per runtime instance."""
    return load_rules(_HEURISTICS_PATH)


def _serialize_briefing(briefing: Any) -> dict:
    if hasattr(briefing, "model_dump"):
        return briefing.model_dump()
    if isinstance(briefing, dict):
        return briefing
    return {}


def score_candidates(subject_lines: list[str], briefing: Any) -> list[dict]:
    """In-process scoring -- imports score.py directly, no sandbox round-trip."""
    if not subject_lines:
        return []
    briefing_dict = _serialize_briefing(briefing)
    rules = _rules()
    return [score_subject_line(sl, briefing_dict, rules) for sl in subject_lines]


def _emit_round_telemetry(round_log: RoundLog) -> None:
    """Observability callback: record per-round metrics as an OTel span event and a structured log.

    Both surfaces show up in `agentcore traces` and `agentcore logs`.
    Span attributes follow OTel semantic-convention naming. Structured
    log fields use the same keys so a CloudWatch Logs Insights query can
    join trace IDs to log events.
    """
    candidates = round_log.candidates
    top3 = candidates[:3]
    top3_avg = sum(float(c.get("score", 0.0)) for c in top3) / max(1, len(top3))
    top_score = float(candidates[0].get("score", 0.0)) if candidates else 0.0
    top_subject = candidates[0].get("subject_line", "") if candidates else ""

    span = _tracer.start_span("optimization_round")
    try:
        span.set_attribute("round.number", round_log.round_number)
        span.set_attribute("round.candidate_count", len(candidates))
        span.set_attribute("round.pruned_count", len(round_log.pruned))
        span.set_attribute("round.top3_average", round(top3_avg, 2))
        span.set_attribute("round.top_score", round(top_score, 2))
        span.set_attribute("round.top_subject_line", top_subject[:200])
        if round_log.guidance:
            span.set_attribute("round.guidance_excerpt", round_log.guidance[:300])
    finally:
        span.end()

    log.info(
        "optimization_round_complete",
        extra={
            "round_number": round_log.round_number,
            "candidate_count": len(candidates),
            "pruned_count": len(round_log.pruned),
            "top3_average": round(top3_avg, 2),
            "top_score": round(top_score, 2),
            "guidance_excerpt": round_log.guidance[:300] if round_log.guidance else "",
        },
    )


def _make_agent(session_id: str, user_id: str, system_prompt: str) -> Agent:
    """Build a fresh Strands Agent with the right system prompt and memory wiring."""
    return Agent(
        model=load_model(),
        session_manager=get_memory_session_manager(session_id, user_id),
        system_prompt=system_prompt,
        tools=[],
    )


def run_initial_optimization(
    briefing: CampaignBriefing | dict,
    session_id: str,
    user_id: str,
) -> IterationResult:
    """Run the full optimization loop for a fresh briefing."""
    generator = _make_agent(session_id, user_id, GENERATOR_SYSTEM_PROMPT)
    critic = _make_agent(session_id, user_id, CRITIQUE_SYSTEM_PROMPT)

    def generate(prompt: str, _n: int) -> list[str]:
        response = generator(prompt, structured_output_model=SubjectLineList)
        return response.structured_output.subject_lines

    def critique(scored: list[dict], to_drop: list[dict]) -> str:
        prompt = build_critique_prompt(scored, to_drop)
        response = critic(prompt)
        return str(response).strip()

    return run_optimization(
        briefing,
        generate=generate,
        score=score_candidates,
        critique=critique,
        recall=recall_for_user,
        on_round=_emit_round_telemetry,
        actor_id=user_id,
        round_one_prompt_builder=round_one_prompt,
        regenerate_prompt_builder=regenerate_prompt,
    )
