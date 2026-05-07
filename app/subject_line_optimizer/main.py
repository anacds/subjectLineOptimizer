"""AgentCore Runtime entrypoint for the Subject Line Optimizer.

Accepts a payload with `prompt` (string) containing a campaign briefing JSON.
Progress is streamed back as text chunks so the caller sees the loop advancing
in real time; the final chunk is the full result as JSON.

To refine results, re-submit a modified briefing (adjust constraints, brand
voice, max length, etc.) with the same session_id. AgentCore Memory carries
learned patterns from prior sessions forward automatically.
"""

from __future__ import annotations

import json
import logging

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from agent.builder import run_initial_optimization
from agent.iteration import IterationResult
from briefing.schema import validate_briefing

app = BedrockAgentCoreApp()
log = app.logger


def _format_round_lines(round_log) -> list[str]:
    lines = [f"\n[round {round_log.round_number}]"]
    for c in round_log.candidates[:8]:
        flag_str = ", ".join(c.get("flags", [])[:3]) or "-"
        lines.append(
            f"  {c['score']:5.1f}  {c['subject_line']}  ({flag_str})"
        )
    if round_log.pruned:
        lines.append(f"  pruned: {len(round_log.pruned)}")
    if round_log.guidance:
        lines.append(f"  guidance for next round: {round_log.guidance[:160]}")
    return lines


def _format_shortlist(result: IterationResult) -> str:
    lines = [
        "\n=== Final shortlist ===",
        f"plateaued: {result.plateaued}",
        f"rounds: {len(result.rounds)}",
        "",
    ]
    for i, c in enumerate(result.shortlist, 1):
        rate = c.get("predicted_open_rate", [0, 0])
        lines.append(
            f"{i}. {c['subject_line']}\n"
            f"   score {c['score']:.1f}  open-rate band {rate[0]:.1f}-{rate[1]:.1f}%\n"
            f"   {c.get('explanation', '').splitlines()[0]}"
        )
    return "\n".join(lines)


def _serialize_result(result: IterationResult) -> dict:
    return {
        "shortlist": result.shortlist,
        "plateaued": result.plateaued,
        "rounds": [
            {
                "round_number": r.round_number,
                "candidates": r.candidates,
                "pruned": [c["subject_line"] for c in r.pruned],
                "guidance": r.guidance,
            }
            for r in result.rounds
        ],
    }


@app.entrypoint
async def invoke(payload, context):
    """Run the optimization loop for a campaign briefing."""
    session_id = getattr(context, "session_id", None) or "default-session"
    user_id    = getattr(context, "user_id", None)    or "default-user"
    prompt     = payload.get("prompt") or ""

    try:
        briefing = validate_briefing(prompt)
    except (ValueError, json.JSONDecodeError) as exc:
        yield f"Invalid briefing: {exc}\n"
        return

    log.info("Optimization for session=%s user=%s campaign=%s", session_id, user_id, briefing.campaign_name)
    yield f"Optimizing subject lines for: {briefing.campaign_name}\n"

    if missing := briefing.critical_field_missing():
        yield (
            f"\nWarning: critical field '{missing}' is empty -- the agent will "
            "proceed with reduced audience inference. For best results, include "
            "an audience.description in the briefing.\n"
        )

    result = run_initial_optimization(briefing, session_id, user_id)

    for round_log in result.rounds:
        for line in _format_round_lines(round_log):
            yield line + "\n"

    yield _format_shortlist(result) + "\n"
    yield "\n---\n"
    yield json.dumps(_serialize_result(result), ensure_ascii=False)


if __name__ == "__main__":
    app.run()
