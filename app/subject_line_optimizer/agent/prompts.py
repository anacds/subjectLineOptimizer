"""Prompt templates for generation, critique, and guidance.

All prompts are pure string builders -- no model calls, no Strands import.
They take dicts and dataclasses, return strings. The iteration loop calls
the generator and critique callables itself; the prompts only shape what
the LLM is asked to do.
"""

from __future__ import annotations

import json
from typing import Any

from memory.schema import MemoryContext

ARCHETYPES = (
    "urgency",
    "curiosity",
    "personalization",
    "value-led",
    "question-led",
    "benefit-led",
    "social-proof",
    "plain-direct",
)


def _briefing_block(briefing: Any) -> str:
    if hasattr(briefing, "model_dump"):
        b = briefing.model_dump()
    elif isinstance(briefing, dict):
        b = briefing
    else:
        b = {}
    return json.dumps(b, indent=2, ensure_ascii=False)


def round_one_prompt(briefing: Any, memory_context: MemoryContext) -> str:
    """Round-1 generation prompt. Asks for one candidate per archetype."""
    archetype_list = "\n".join(f"  {i + 1}. {a}" for i, a in enumerate(ARCHETYPES))
    memory_block = memory_context.render_for_prompt()
    memory_section = (
        f"\n\nWhat we already know about this marketer:\n{memory_block}\n"
        if memory_block
        else "\n\nNo prior session data for this marketer.\n"
    )
    return (
        "You are generating email subject-line candidates for the briefing below. "
        "Produce exactly one candidate for each of the eight archetypes, in order, "
        "covering the full stylistic range so we can compare:\n"
        f"{archetype_list}\n\n"
        "Output format: a JSON array of exactly 8 strings. No other text, no commentary, no markdown fences.\n\n"
        f"Briefing:\n{_briefing_block(briefing)}"
        f"{memory_section}"
    )


def regenerate_prompt(
    briefing: Any,
    memory_context: MemoryContext,
    surviving: list[str],
    guidance: str,
    n_to_generate: int,
) -> str:
    """Regenerate-N prompt with explicit guidance derived from prior round critique."""
    surviving_block = "\n".join(f"- {s}" for s in surviving) or "(none)"
    memory_block = memory_context.render_for_prompt()
    memory_section = (
        f"\n\nWhat we already know about this marketer:\n{memory_block}\n"
        if memory_block
        else ""
    )
    return (
        "Generate replacement candidates for an email subject-line optimization "
        f"loop. Produce exactly {n_to_generate} new candidates that follow the "
        "guidance below. Do not duplicate any of the surviving variants.\n\n"
        f"Surviving variants from prior rounds (do not reproduce):\n{surviving_block}\n\n"
        f"Guidance derived from why earlier candidates lost:\n{guidance}\n\n"
        "Output format: a JSON array of exactly "
        f"{n_to_generate} strings. No other text.\n\n"
        f"Briefing:\n{_briefing_block(briefing)}"
        f"{memory_section}"
    )


def critique_prompt(scored: list[dict], to_drop: list[dict]) -> str:
    """Ask the model to articulate why the dropped candidates lost.

    Returns explicit, briefing-specific guidance the regenerator can apply --
    not generic advice. The critique is the agent's "learning within a session."
    """
    keep_block = "\n".join(
        f"- {c['subject_line']}  (score {c['score']:.1f}, flags: {', '.join(c.get('flags', [])) or '-'})"
        for c in scored
        if c not in to_drop
    )
    drop_block = "\n".join(
        f"- {c['subject_line']}  (score {c['score']:.1f}, flags: {', '.join(c.get('flags', [])) or '-'})"
        for c in to_drop
    )
    return (
        "You are critiquing the weak candidates from one round of subject-line "
        "optimization. Read the kept and dropped candidates below, then write "
        "two to four sentences of explicit, actionable guidance for the next "
        "generation round. Reference specific patterns (length, tone, word "
        "choice, framing) -- not generic advice. The guidance will be passed "
        "verbatim to the regenerator.\n\n"
        f"Kept:\n{keep_block}\n\n"
        f"Dropped:\n{drop_block}\n\n"
        "Guidance:"
    )
