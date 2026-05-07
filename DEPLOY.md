# Deployment guide

This walks through running the agent locally and deploying it to AWS via the AgentCore CLI.

## Prerequisites

You will need:

- **Node.js 20+** and the AgentCore CLI: `npm install -g @aws/agentcore`
- **Python 3.12** and **uv** ([install](https://docs.astral.sh/uv/getting-started/installation/))
- **AWS account** with credentials configured (`aws configure` or SSO)
- **Bedrock model access** for Claude Sonnet in your target region — request it once in the [Bedrock console](https://console.aws.amazon.com/bedrock/home#/modelaccess)
- **Docker** is **not** required for this project (we use `CodeZip` build)

Verify with:

```bash
agentcore --version
uv --version
aws sts get-caller-identity
```

## Local development

The local dev server runs your agent in a hot-reloading ASGI process. AWS credentials must be present because the LLM is Bedrock either way.

```bash
cd subjectLineOptimizer
agentcore dev
```

This starts the server on `http://localhost:8080`. In a second terminal:

```bash
agentcore invoke --dev "$(cat app/subject_line_optimizer/briefing/examples/reactivation.json)"
```

The agent streams progress back as text. The first invocation will take ~30-90 seconds (four LLM rounds plus per-round scoring; scoring itself is local Python and adds milliseconds).

For a follow-up turn in the same session, call again with a free-text message:

```bash
agentcore invoke --dev "I like #2 and #4, but they're too long. Give me shorter versions."
```

`agentcore invoke --dev` carries the same session ID across calls, so the agent sees the prior conversation. Switch sessions by passing `--session-id` explicitly.

### Memory in local dev

Local dev runs **without** AgentCore Memory by default. The Strands session manager returns `None` when `MEMORY_SUBJECT_LINE_OPTIMIZERMEMORY_ID` is unset, and the agent runs without persistent learning. Conversation history within a single `agentcore dev` session is preserved in process memory.

To exercise Memory locally, you must first deploy the Memory resource (see below) and then export its ID into your shell. The agent code does not change.

## Deploying to AWS

### One-time setup

The first deploy runs CDK bootstrap in your target account/region. This is a CloudFormation stack named `CDKToolkit` that holds CDK assets. If you have ever used CDK in this account/region before, you can skip this:

```bash
cd subjectLineOptimizer/agentcore/cdk
npx cdk bootstrap
```

### Configure the deployment target

Edit `agentcore/aws-targets.json` to set your account ID and region:

```json
[
  {
    "name": "default",
    "account": "123456789012",
    "region": "us-east-1"
  }
]
```

### Preview and deploy

Preview the CloudFormation change set without mutating anything:

```bash
cd subjectLineOptimizer
agentcore deploy --plan
```

When the plan looks right, deploy:

```bash
agentcore deploy
```

This synthesizes CDK, packages the agent code as a CodeZip, and creates:

- An AgentCore Memory resource with all four strategies (SEMANTIC, USER_PREFERENCE, SUMMARIZATION, EPISODIC) configured per [`agentcore/agentcore.json`](agentcore/agentcore.json)
- An AgentCore Runtime instance for the agent
- The IAM execution role for the runtime (Bedrock invoke, Memory read/write)
- CloudWatch log groups for traces and runtime logs

First deploy takes ~5-8 minutes. Subsequent deploys (code changes only) are 2-3 minutes.

### Invoke the deployed agent

```bash
agentcore invoke "$(cat app/subject_line_optimizer/briefing/examples/reactivation.json)"
```

Same as `--dev`, but routed to the AgentCore Runtime endpoint. The first invocation has runtime cold-start latency (~5-15 seconds added to the iteration time).

To call from your own code instead of the CLI:

```python
import json
import boto3

client = boto3.client("bedrock-agentcore", region_name="us-east-1")
response = client.invoke_agent_runtime(
    agentRuntimeArn="arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/subject_line_optimizer-...",
    runtimeSessionId="user-alice-session-001",
    payload=json.dumps({"prompt": briefing_json}),
)
for chunk in response["completion"]:
    print(chunk["chunk"]["bytes"].decode("utf-8"), end="")
```

The `runtimeSessionId` is what carries conversation context; reuse the same value for follow-up turns.

## Iterating on the agent

After local code changes:

1. `agentcore dev` will hot-reload locally — no CLI command needed.
2. To push to AWS: `agentcore deploy` (only the CodeZip is rebuilt; CDK skips unchanged stacks).

After changes to `agentcore/agentcore.json` (e.g. memory strategy edits, new resources):

1. `agentcore validate` to type-check the spec.
2. `agentcore deploy --plan` to preview the change set.
3. `agentcore deploy` to apply.

## Observing what the agent did

```bash
agentcore logs           # CloudWatch runtime logs
agentcore traces         # AgentCore traces (per-invocation LLM and Memory spans)
agentcore status         # Deployment status
```

In the AWS console, traces appear under **Bedrock AgentCore → Observability → Traces**. Memory records appear under Bedrock AgentCore → Memory → your memory store; the four namespace prefixes correspond to the four strategies.

### Viewing per-round spans

Each optimization round emits a custom `optimization_round` OTel span with attributes that show how scoring progressed:

```bash
agentcore traces --filter optimization_round
```

In the trace view, expand any invocation to see 1-4 child spans (one per round). The `round.top3_average` and `round.top_score` attributes show whether scores improved across rounds; `round.guidance_excerpt` shows what critique the LLM produced before each regeneration step.

### Querying in CloudWatch Logs Insights

The same data appears in structured logs as `optimization_round_complete` events. Open **CloudWatch → Logs Insights**, select the runtime log group (named `/agentcore/subject_line_optimizer` by default), and run:

```
fields @timestamp, round_number, top3_average, top_score, guidance_excerpt
| filter @message = "optimization_round_complete"
| sort @timestamp asc
```

To join log events to a specific trace, add `| filter traceId = "your-trace-id"`. The `traceId` field is the same value shown in the AgentCore traces UI.

## Costs to watch

- **Bedrock model invocations** — four to twelve LLM calls per optimization (one round-1 generation, regen + critique per subsequent round, scaled by `max_rounds`)
- **Memory storage and extraction** — billed per GB-month plus per extraction job; pattern extraction runs asynchronously about 60 seconds after each session
- **AgentCore Runtime instance hours** — billed only while the runtime is actively serving (not when idle)

For an article reader running through the demo a handful of times, total cost should be well under a dollar.

## Tearing it down

```bash
agentcore remove memory subject_line_optimizerMemory
agentcore remove runtime subject_line_optimizer
agentcore deploy
```

Or remove the CloudFormation stacks directly via the console / `aws cloudformation delete-stack`. The CDK bootstrap stack (`CDKToolkit`) can stay — it's shared across all CDK projects in the account.
