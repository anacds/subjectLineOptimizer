"""The optimizer loop -- pure Python, framework-free.

Takes a briefing plus injected callables (generate, score, critique, recall)
and runs the generate / score / critique / regenerate cycle until the top-3
average plateaus or `max_rounds` is hit. Returns a ranked shortlist with the
full per-round log.

This module has no Strands import, no AgentCore import, no LLM client.
The article excerpts this file because the orchestration story is decoupled
from any framework choice. Strands wiring lives in agent/builder.py;
the LLM lives behind the `generate` and `critique` callables passed in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from memory.schema import MemoryContext


# ---------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------


@dataclass
class RoundLog:
    """One round's candidates, scores, what was pruned, and guidance carried forward."""

    round_number: int
    candidates: list[dict]  # each dict is the score.py result shape
    pruned: list[dict]
    guidance: str


@dataclass
class IterationResult:
    """Final output of run_optimization."""

    shortlist: list[dict]  # top 5, sorted desc by score
    rounds: list[RoundLog]
    memory_context: MemoryContext = field(default_factory=MemoryContext)
    plateaued: bool = False


# ---------------------------------------------------------------------
# Callable contracts (duck-typed)
# ---------------------------------------------------------------------
# generate(prompt: str, n: int) -> list[str]
# score(subject_lines: list[str], briefing: Any) -> list[dict]
# critique(scored: list[dict], to_drop: list[dict]) -> str
# recall(actor_id: str, briefing: Any) -> MemoryContext
#
# All four are passed in; the loop never imports them. This is what makes
# the loop testable, model-agnostic, and trivially reusable in other
# orchestrations (a different scoring backend, a different LLM, no memory).


# ---------------------------------------------------------------------
# Tunable defaults
# ---------------------------------------------------------------------

INITIAL_N = 8
DROP_FRACTION = 0.4  # bottom 40% pruned each round (3-4 of 8)
SHORTLIST_SIZE = 5
DEFAULT_MAX_ROUNDS = 4
DEFAULT_PLATEAU_EPSILON = 0.5  # composite-score points


# ---------------------------------------------------------------------
# Loop
# ---------------------------------------------------------------------


def run_optimization(
    briefing: Any,
    generate: Callable[[str, int], list[str]],
    score: Callable[[list[str], Any], list[dict]],
    critique: Callable[[list[dict], list[dict]], str],
    recall: Callable[[str, Any], MemoryContext] | None = None,
    on_round: Callable[[RoundLog], None] | None = None,
    actor_id: str = "anonymous",
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    plateau_epsilon: float = DEFAULT_PLATEAU_EPSILON,
    *,
    round_one_prompt_builder: Callable[[Any, MemoryContext], str],
    regenerate_prompt_builder: Callable[[Any, MemoryContext, list[str], str, int], str],
) -> IterationResult:
    """Run the generate/score/critique/regenerate loop to convergence.

    The two `*_prompt_builder` arguments are required keyword args because
    they're the only string templates the loop needs. Keeping them injected
    means agent/prompts.py can change without touching this file.

    `on_round` is an optional observability callback fired once per completed
    round with the corresponding RoundLog. Exceptions raised by the callback
    are swallowed -- observability must never break the loop.
    """
    memory_context = recall(actor_id, briefing) if recall else MemoryContext()

    rounds: list[RoundLog] = []
    survivors: list[dict] = []
    prior_top3_avg: float | None = None
    plateaued = False

    for round_number in range(1, max_rounds + 1):
        if round_number == 1:
            prompt = round_one_prompt_builder(briefing, memory_context)
            new_candidates = generate(prompt, INITIAL_N)
            scored_new = score(new_candidates, briefing)
            current = scored_new
        else:
            n_to_generate = INITIAL_N - len(survivors)
            if n_to_generate <= 0:
                break
            guidance = rounds[-1].guidance
            prompt = regenerate_prompt_builder(
                briefing,
                memory_context,
                [c["subject_line"] for c in survivors],
                guidance,
                n_to_generate,
            )
            new_candidates = generate(prompt, n_to_generate)
            scored_new = score(new_candidates, briefing)
            current = survivors + scored_new

        current = _dedupe_by_subject(current)
        current.sort(key=_by_score_desc)

        # Plateau check on top-3 average
        top3_avg = _top_n_average(current, 3)
        if prior_top3_avg is not None and (top3_avg - prior_top3_avg) < plateau_epsilon:
            plateaued = True
            log = RoundLog(round_number, current, [], "")
            rounds.append(log)
            _safe_emit(on_round, log)
            break
        prior_top3_avg = top3_avg

        # Prune the bottom for the next round and ask the LLM why they lost
        if round_number < max_rounds:
            keep_count = max(SHORTLIST_SIZE, int(round(len(current) * (1 - DROP_FRACTION))))
            survivors = current[:keep_count]
            pruned = current[keep_count:]
            guidance = critique(current, pruned) if pruned else ""
        else:
            survivors = current
            pruned = []
            guidance = ""

        log = RoundLog(round_number, current, pruned, guidance)
        rounds.append(log)
        _safe_emit(on_round, log)

    final = rounds[-1].candidates if rounds else []
    return IterationResult(
        shortlist=final[:SHORTLIST_SIZE],
        rounds=rounds,
        memory_context=memory_context,
        plateaued=plateaued,
    )


# ---------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------


def _by_score_desc(candidate: dict) -> float:
    return -float(candidate.get("score", 0.0))


def _safe_emit(
    on_round: Callable[[RoundLog], None] | None,
    round_log: RoundLog,
) -> None:
    if on_round is None:
        return
    try:
        on_round(round_log)
    except Exception:  # noqa: BLE001 -- observability must never break the loop
        pass


def _top_n_average(candidates: list[dict], n: int) -> float:
    if not candidates:
        return 0.0
    top = candidates[: max(1, n)]
    return sum(float(c.get("score", 0.0)) for c in top) / len(top)


def _dedupe_by_subject(candidates: list[dict]) -> list[dict]:
    """Remove duplicates by subject_line, keeping the highest-scoring instance."""
    best: dict[str, dict] = {}
    for c in candidates:
        key = c.get("subject_line", "")
        if not key:
            continue
        existing = best.get(key)
        if existing is None or float(c.get("score", 0.0)) > float(existing.get("score", 0.0)):
            best[key] = c
    return list(best.values())
