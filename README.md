# Subject Line Optimizer

A multi-round subject-line optimization agent built on AWS AgentCore. Given an email campaign briefing, it generates 8 candidates spanning recognizable archetypes, scores them against a transparent in-process heuristic dataset, prunes the weak ones, learns *why* they failed, regenerates replacements with explicit guidance, and returns a ranked shortlist of 5. Across sessions it gets better at each user's taste — AgentCore Memory's strategies extract patterns from session history asynchronously and bias future generation.

This is a portfolio / article project demonstrating why AgentCore is the right tool for non-trivial agent orchestration vs. plain Bedrock Agents, AgentCore Harness, or Lambda + Converse.

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for diagrams and the design rationale.

The short version:

- **AgentCore Runtime** hosts the agent (long iteration loops, streaming responses)
- **AgentCore Memory** does asynchronous pattern extraction across all four strategies (SEMANTIC, USER_PREFERENCE, SUMMARIZATION, EPISODIC)
- **Strands Agents SDK** wires the LLM + session manager
- **Bedrock Claude Sonnet** does generation and critique
- **Scoring is in-process Python** — 45-rule heuristic table, no managed sandbox. ARCHITECTURE.md explains when you would reach for AgentCore Code Interpreter and why this scorer doesn't qualify

The optimizer loop itself ([`agent/iteration.py`](app/subject_line_optimizer/agent/iteration.py)) is plain Python with four injected callables — no Strands, AgentCore, or LLM imports. That's the file the article excerpts.

## Quick start

```bash
# Prereqs: Node 20+, Python 3.12, uv, AWS credentials, Bedrock model access
npm install -g @aws/agentcore

cd subjectLineOptimizer
agentcore dev
# in another terminal:
agentcore invoke --dev "$(cat app/subject_line_optimizer/briefing/examples/reactivation.json)"
```

For the full local-and-deploy walkthrough, see [DEPLOY.md](DEPLOY.md).

## Layout

```
subjectLineOptimizer/
├── ARCHITECTURE.md              # how it fits together
├── DEPLOY.md                    # local dev + AWS deployment guide
├── AGENTS.md                    # AgentCore-CLI-managed context
├── agentcore/                   # CLI-managed config + CDK
│   ├── agentcore.json           # runtimes, memories, strategies
│   ├── aws-targets.json         # account + region
│   └── cdk/                     # CDK app deployed by `agentcore deploy`
└── app/
    └── subject_line_optimizer/
        ├── main.py              # BedrockAgentCoreApp entrypoint
        ├── agent/
        │   ├── iteration.py     # the loop -- pure Python, no framework
        │   ├── builder.py       # Strands wiring + scoring + memory
        │   └── prompts.py       # generation, critique, regenerate
        ├── scoring/
        │   └── score.py         # in-process scorer (called directly from builder.py)
        ├── memory/
        │   ├── session.py       # Strands session manager (CLI-generated)
        │   ├── client.py        # explicit pattern recall for the loop
        │   └── schema.py        # Pattern, MemoryContext
        ├── briefing/
        │   ├── schema.py        # Pydantic CampaignBriefing
        │   └── examples/        # 4 sample briefings
        ├── data/
        │   ├── heuristics.csv   # 45 weighted scoring rules
        │   └── README.md        # rule schema + source citations
        └── model/load.py        # Bedrock model selection (CLI-generated)
```

## Briefing schema

The agent accepts JSON briefings with `campaign_name` (required) plus optional `objective`, `audience`, `offer`, `brand_voice`, `channel`, and `constraints`. Constraints support `subject_line_max_length`, `avoid_emojis`, `avoid_words`, and `require_words`.

Four examples ship under [`app/subject_line_optimizer/briefing/examples/`](app/subject_line_optimizer/briefing/examples/):

- `reactivation.json` — lapsed-customer win-back with loyalty discount
- `acquisition.json` — new-subscriber first-purchase conversion
- `cross_sell.json` — equipment cross-sell to coffee subscribers
- `regulatory.json` — privacy-policy notice (compliance, not marketing)

Briefings can be in any language; the agent's prompts to the LLM remain in English.

## Scoring transparency

The scorer runs against [`data/heuristics.csv`](app/subject_line_optimizer/data/heuristics.csv) — 45 weighted rules across 9 categories (length, urgency, spam_risk, curiosity_triggers, value_signals, personalization, style, audience_fit, brand_voice). Each rule cites a public source. Audience tags are inferred from briefing free-text (campaign_name, objective, audience.description, brand_voice) and applied as per-rule modifiers, so the same rule can lift acquisition while penalizing regulatory.

This is deliberately *not* a research-grade ML model. The article frames it as "a starting point you'd calibrate against your own send data" — a transparent dataset is more pedagogically useful than a black box that hides exactly the part the agent is supposed to learn from.

## Follow-up turns

Within a session, you can ask for variations on the shortlist:

```bash
agentcore invoke --dev "I like #2 and #4, but they're too long. Give me shorter versions."
```

The Strands session manager carries the prior conversation, so the LLM already knows the briefing and the candidates. New candidates are scored deterministically through the same in-process scoring path.
