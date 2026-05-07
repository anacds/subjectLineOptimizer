# Heuristics dataset

`heuristics.csv` is a transparent, weighted-rule scoring model for email subject lines. The scoring script in [`../scoring/score.py`](../scoring/score.py) consumes it; the agent never reads it directly.

## Why a heuristic table, not an ML model

For an article showing how an agent iterates against a feedback signal, the signal needs to be **inspectable**. With this CSV a reader can point to any score the agent reasoned about and trace it back to a specific row, weight, and source tag. A black-box model would hide exactly the part that makes the agent's behavior interesting.

Treat this as **a starting point you'd calibrate against your own send data** — not a research-grade model. The weights are reasonable approximations from public marketing literature, not measured lifts.

## Schema

| Column | Meaning |
|---|---|
| `rule_id` | Unique identifier, used in score explanations |
| `category` | One of: `length`, `urgency`, `spam_risk`, `curiosity_triggers`, `value_signals`, `personalization`, `style`, `audience_fit`, `brand_voice` |
| `pattern` | Regex, literal, word list, or numeric range — interpretation depends on `match_type` |
| `match_type` | `range` (numeric, e.g. `30-50`, `>70`), `regex`, `literal`, `word_any` (any token in pipe-separated list), `phrase` (exact substring), `phrase_any` (any phrase in pipe-separated list), `count_over_N` (regex match count exceeds N) |
| `weight` | Signed score contribution when the rule fires; positive = lift, negative = penalty |
| `audience_modifier` | Optional `key:+/-N;...` overrides applied when the briefing's audience matches that key. Recognized keys: `acquisition`, `retention`, `cross_sell`, `regulatory`, `b2b`, `b2c`, `premium`, `price_conscious`, `understated`, `warm`. The scorer infers these tags from the briefing |
| `note` | Human-readable explanation surfaced in score breakdowns |
| `source_tag` | Reference to the source documented below |

## Sources

The weights below summarize directional findings from public sources. Citations are illustrative — none of these claim to be the ground truth.

| `source_tag` | Reference |
|---|---|
| `mailchimp_2024` | Mailchimp, "Email Subject Line Best Practices," guidance on length and personalization |
| `litmus_2023` | Litmus, "State of Email" reports — mobile preview lengths and truncation behavior |
| `gmail_truncation_guide` | Public Gmail mobile rendering tests; ~70-character soft cap |
| `campaign_monitor_2023` | Campaign Monitor benchmark reports on subject-line length distributions |
| `return_path_2022` | Return Path / Validity reports on urgency word performance and exclamation usage |
| `htmlemailcheck` | Common spam-trigger lists (Mail-Tester, HTML Email Check) |
| `gmail_promotions_2023` | Public discussion of Gmail Promotions tab routing signals |
| `himalmedia_questions` | Studies of question-led headlines in marketing copy |
| `buzzfeed_headline_study` | Public analyses of curiosity-gap headlines (e.g. "Upworthy effect" research) |
| `kissmetrics_offers` | KISSmetrics writing on percent-off vs dollar-off framing |
| `bigcommerce_shipping` | BigCommerce / Shopify writeups on free-shipping conversion lift |
| `return_path_retention` | Retention / loyalty messaging analyses |
| `experian_personalization` | Experian Marketer Email Quarterly on personalization lift |
| `experian_emoji` | Experian / Mailjet studies of emoji performance by sector |
| `nielsen_norman_tone` | Nielsen Norman Group on writing tone for digital communication |
| `cialdini_influence` | Cialdini's *Influence* (scarcity, social proof) — applied to copy |
| `b2b_subject_line_study` | Various B2B subject-line benchmark reports |

## Calibration

If you want to use this in production, the path is:

1. Score your last 10k subject lines with the unmodified table.
2. Regress predicted score against actual open rate.
3. Adjust weights and `audience_modifier` deltas until the regression residuals look unbiased across audience tags.
4. Add rules for patterns specific to your brand voice that the table misses.

The scoring code is the same; only the CSV changes.
