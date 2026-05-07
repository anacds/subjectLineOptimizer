"""Heuristic scorer for subject lines.

Pure Python over a CSV: no network, no LLM, no AWS. Imported directly by
agent/builder.py as score_subject_line(); also runnable as a CLI for ad-hoc
inspection (read JSON from stdin, write JSON to stdout).

CLI input:  {"subject_line": str, "briefing": {...}}
            or {"subject_lines": [str, ...], "briefing": {...}}
CLI output: one result dict per subject line (always a list)

Result shape:
{
    "subject_line": str,
    "score": float,                          # 0-100 composite
    "predicted_open_rate": [float, float],   # [low, high] %
    "dimensions": {
        "length": float, "urgency_words": float, "spam_risk": float,
        "curiosity_triggers": float, "value_signals": float,
        "personalization": float, "style": float, "audience_fit": float,
        "brand_voice": float
    },
    "flags": [str],                          # rule_ids that fired
    "explanation": str                       # ranked list of contributions
}

Heuristics are loaded from $HEURISTICS_PATH if set, else
data/heuristics.csv resolved relative to this file's parent.
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# ----------------------------------------------------------------------
# Heuristic table
# ----------------------------------------------------------------------

DIMENSIONS = (
    "length",
    "urgency_words",
    "spam_risk",
    "curiosity_triggers",
    "value_signals",
    "personalization",
    "style",
    "audience_fit",
    "brand_voice",
)

CATEGORY_TO_DIMENSION = {
    "length": "length",
    "urgency": "urgency_words",
    "spam_risk": "spam_risk",
    "curiosity_triggers": "curiosity_triggers",
    "value_signals": "value_signals",
    "personalization": "personalization",
    "style": "style",
    "audience_fit": "audience_fit",
    "brand_voice": "brand_voice",
}


@dataclass
class Rule:
    rule_id: str
    category: str
    pattern: str
    match_type: str
    weight: float
    audience_modifier: dict[str, float]
    note: str


def _parse_audience_modifier(raw: str) -> dict[str, float]:
    if not raw:
        return {}
    out: dict[str, float] = {}
    for clause in raw.split(";"):
        clause = clause.strip()
        if not clause:
            continue
        key, val = clause.split(":")
        out[key.strip()] = float(val)
    return out


def load_rules(path: Path) -> list[Rule]:
    rules: list[Rule] = []
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            rules.append(
                Rule(
                    rule_id=row["rule_id"],
                    category=row["category"],
                    pattern=row["pattern"],
                    match_type=row["match_type"],
                    weight=float(row["weight"]),
                    audience_modifier=_parse_audience_modifier(row["audience_modifier"]),
                    note=row["note"],
                )
            )
    return rules


# ----------------------------------------------------------------------
# Audience tag inference
# ----------------------------------------------------------------------

_RETENTION_HINTS = ("lapsed", "reactivat", "win back", "loyal", "existing", "subscriber")
_ACQUISITION_HINTS = ("acquisition", "new subscriber", "first purchase", "prospect", "new customer", "new arrivals")
_CROSS_SELL_HINTS = ("cross-sell", "cross sell", "upsell", "upgrade", "add-on", "complementary")
_REGULATORY_HINTS = ("privacy policy", "terms of service", "compliance", "notice", "legal", "regulation", "gdpr")
_PRICE_HINTS = ("price-conscious", "budget", "discount", "deal", "value-conscious", "thrifty")
_PREMIUM_HINTS = ("premium", "luxury", "high-end", "boutique", "craft", "artisan")
_B2B_HINTS = ("b2b", "enterprise", "saas", "businesses", "procurement")
_B2C_HINTS = ("consumer", "shopper", "customer", "subscriber", "buyer")
_UNDERSTATED_HINTS = ("understated", "calm", "minimal", "muted", "professional")
_WARM_HINTS = ("warm", "friendly", "conversational", "approachable")


def infer_audience_tags(briefing: dict) -> set[str]:
    """Derive audience_modifier keys from briefing free-text fields."""
    haystacks = [
        (briefing.get("campaign_name") or ""),
        (briefing.get("objective") or ""),
        ((briefing.get("audience") or {}).get("description") or ""),
        (briefing.get("brand_voice") or ""),
    ]
    blob = " ".join(haystacks).lower()

    tags: set[str] = set()

    def _any(hints: tuple[str, ...]) -> bool:
        return any(h in blob for h in hints)

    if _any(_RETENTION_HINTS):
        tags.add("retention")
    if _any(_ACQUISITION_HINTS):
        tags.add("acquisition")
    if _any(_CROSS_SELL_HINTS):
        tags.add("cross_sell")
    if _any(_REGULATORY_HINTS):
        tags.add("regulatory")
    if _any(_PRICE_HINTS):
        tags.add("price_conscious")
    if _any(_PREMIUM_HINTS):
        tags.add("premium")
    if _any(_B2B_HINTS):
        tags.add("b2b")
    elif _any(_B2C_HINTS):
        tags.add("b2c")
    if _any(_UNDERSTATED_HINTS):
        tags.add("understated")
    if _any(_WARM_HINTS):
        tags.add("warm")

    return tags


# ----------------------------------------------------------------------
# Rule matching
# ----------------------------------------------------------------------


def _match_range(spec: str, length: int) -> bool:
    spec = spec.strip()
    if spec.startswith("<"):
        return length < int(spec[1:])
    if spec.startswith(">"):
        return length > int(spec[1:])
    if "-" in spec:
        lo, hi = spec.split("-", 1)
        return int(lo) <= length <= int(hi)
    return False


def _word_any(words_pipe: str, text: str) -> bool:
    text_lower = text.lower()
    for w in words_pipe.split("|"):
        w = w.strip().lower()
        if not w:
            continue
        # bound the match to word characters where possible
        # but allow phrases like "act now" which contain spaces
        if " " in w:
            if w in text_lower:
                return True
        else:
            if re.search(rf"(?<!\w){re.escape(w)}(?!\w)", text_lower):
                return True
    return False


def _phrase_any(phrases_pipe: str, text: str) -> bool:
    text_lower = text.lower()
    return any(p.strip().lower() in text_lower for p in phrases_pipe.split("|") if p.strip())


def _count_over(pattern_pipe: str, text: str, threshold: int) -> bool:
    text_lower = text.lower()
    count = 0
    for w in pattern_pipe.split("|"):
        w = w.strip().lower()
        if not w:
            continue
        if " " in w:
            count += text_lower.count(w)
        else:
            count += len(re.findall(rf"(?<!\w){re.escape(w)}(?!\w)", text_lower))
    return count > threshold


def fires(rule: Rule, subject_line: str) -> bool:
    """Return True iff the rule matches the given subject line."""
    if rule.match_type == "range":
        return _match_range(rule.pattern, len(subject_line))
    if rule.match_type == "regex":
        try:
            return re.search(rule.pattern, subject_line) is not None
        except re.error:
            return False
    if rule.match_type == "literal":
        return rule.pattern in subject_line
    if rule.match_type == "phrase":
        return rule.pattern.lower() in subject_line.lower()
    if rule.match_type == "word_any":
        return _word_any(rule.pattern, subject_line)
    if rule.match_type == "phrase_any":
        return _phrase_any(rule.pattern, subject_line)
    if rule.match_type.startswith("count_over_"):
        threshold = int(rule.match_type.rsplit("_", 1)[-1])
        return _count_over(rule.pattern, subject_line, threshold)
    return False


def effective_weight(rule: Rule, audience_tags: set[str]) -> float:
    weight = rule.weight
    for tag, delta in rule.audience_modifier.items():
        if tag in audience_tags:
            weight += delta
    return weight


# ----------------------------------------------------------------------
# Scoring
# ----------------------------------------------------------------------

# Composite score is centered around 50 and clamped to [0, 100].
# Per-rule contributions sum into a raw delta; the delta is scaled so that
# realistic subject lines mostly land in the 30-80 range.
_RAW_TO_COMPOSITE_SCALE = 1.5
_COMPOSITE_BASELINE = 50.0
_OPEN_RATE_LOW_INTERCEPT = 5.0   # at score 0
_OPEN_RATE_HIGH_INTERCEPT = 50.0 # at score 100


def score_subject_line(
    subject_line: str,
    briefing: dict,
    rules: list[Rule],
) -> dict:
    audience_tags = infer_audience_tags(briefing)

    dim_totals: dict[str, float] = {d: 0.0 for d in DIMENSIONS}
    contributions: list[tuple[str, float, str]] = []  # (rule_id, weight, note)
    flags: list[str] = []

    for rule in rules:
        if not fires(rule, subject_line):
            continue
        w = effective_weight(rule, audience_tags)
        dim = CATEGORY_TO_DIMENSION.get(rule.category, "style")
        dim_totals[dim] += w
        contributions.append((rule.rule_id, w, rule.note))
        flags.append(rule.rule_id)

    # Constraints from the briefing become hard penalties.
    constraints = briefing.get("constraints") or {}
    avoid_words = [w.lower() for w in constraints.get("avoid_words", []) if w]
    require_words = [w.lower() for w in constraints.get("require_words", []) if w]
    max_len = constraints.get("subject_line_max_length")

    sl_lower = subject_line.lower()
    for w in avoid_words:
        if w and w in sl_lower:
            penalty = -8.0
            dim_totals["brand_voice"] += penalty
            contributions.append((f"CONSTRAINT_AVOID:{w}", penalty, f"constraint violation: contains '{w}'"))
            flags.append(f"CONSTRAINT_AVOID:{w}")
    for w in require_words:
        if w and w not in sl_lower:
            penalty = -8.0
            dim_totals["brand_voice"] += penalty
            contributions.append((f"CONSTRAINT_REQUIRE:{w}", penalty, f"constraint violation: missing '{w}'"))
            flags.append(f"CONSTRAINT_REQUIRE:{w}")
    if max_len and len(subject_line) > max_len:
        overflow = len(subject_line) - max_len
        penalty = -1.0 * overflow
        dim_totals["length"] += penalty
        contributions.append(
            (f"CONSTRAINT_MAX_LEN:{max_len}", penalty, f"exceeds max length by {overflow}")
        )
        flags.append(f"CONSTRAINT_MAX_LEN:{max_len}")

    raw = sum(dim_totals.values())
    composite = max(0.0, min(100.0, _COMPOSITE_BASELINE + raw * _RAW_TO_COMPOSITE_SCALE))

    # Open-rate band: linear interpolation, then a +/- 4pt half-band.
    midpoint = _OPEN_RATE_LOW_INTERCEPT + (composite / 100.0) * (
        _OPEN_RATE_HIGH_INTERCEPT - _OPEN_RATE_LOW_INTERCEPT
    )
    band_low = max(0.0, midpoint - 4.0)
    band_high = min(100.0, midpoint + 4.0)

    contributions.sort(key=lambda c: abs(c[1]), reverse=True)
    explanation_lines = [
        f"audience tags: {sorted(audience_tags) or ['(none inferred)']}",
        f"composite: {composite:.1f} (raw delta {raw:+.1f}, baseline {_COMPOSITE_BASELINE:.0f})",
        "top contributions:",
    ]
    for rule_id, w, note in contributions[:6]:
        explanation_lines.append(f"  {w:+5.1f}  {rule_id}  -- {note}")
    explanation = "\n".join(explanation_lines)

    return {
        "subject_line": subject_line,
        "score": round(composite, 2),
        "predicted_open_rate": [round(band_low, 2), round(band_high, 2)],
        "dimensions": {k: round(v, 2) for k, v in dim_totals.items()},
        "flags": flags,
        "explanation": explanation,
    }


# ----------------------------------------------------------------------
# CLI entry
# ----------------------------------------------------------------------


def _resolve_heuristics_path() -> Path:
    env = os.environ.get("HEURISTICS_PATH")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "data" / "heuristics.csv"


def main() -> None:
    payload = json.loads(sys.stdin.read())
    rules = load_rules(_resolve_heuristics_path())

    if "subject_lines" in payload:
        subject_lines = payload["subject_lines"]
    else:
        subject_lines = [payload["subject_line"]]
    briefing = payload.get("briefing") or {}

    results = [score_subject_line(sl, briefing, rules) for sl in subject_lines]
    json.dump(results, sys.stdout)


if __name__ == "__main__":
    main()
