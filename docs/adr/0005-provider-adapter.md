# ADR 0005 — Thin provider adapter, two real providers plus a deterministic fake

**Status:** Accepted — 2026-09-12

## Context

The plan names GPT-4.1-mini as the initial benchmark candidate and a small adapter
behind it. Two risks: (a) an agent framework would bury the control flow that this
project exists to demonstrate; (b) a single hard-wired SDK makes provider comparison
and offline development impossible.

## Decision

Define one narrow protocol in `agent/providers/base.py`:

```python
class ModelProvider(Protocol):
    name: str
    async def complete(
        self, *, messages: list[Message], tools: list[ToolSpec],
        response_format: type[BaseModel] | None, deadline_s: float,
    ) -> ProviderResult: ...
```

`ProviderResult` carries text, optional tool calls, token usage, finish reason and
latency — everything the run accounting needs, normalized across providers.

Three implementations:

- `OpenAIProvider` — default, `gpt-4.1-mini`, matches the plan's cost model.
- `AnthropicProvider` — alternative, selected by `LLM_PROVIDER=anthropic`.
- `FakeProvider` — deterministic scripted responses keyed by scenario, used by local
  development, CI, and every deterministic evaluation case. No network, no spend.

No LangChain, LlamaIndex, or agent framework. The orchestrator loop is ~150 lines of
explicit code in `agent/orchestrator.py` and is meant to be read.

## Consequences

- Tool-calling differences (OpenAI `tools` vs Anthropic `tools`) are normalized in
  one place, at the cost of not exposing provider-specific features.
- Cost accounting needs a per-model price table (`agent/pricing.py`); it is an
  estimate and is labelled as such wherever it is displayed.
- CI runs the full investigation path with zero API spend.

## Revisit when

Evaluation shows a materially better quality/cost point on another model, or a
provider feature (e.g. cached prefixes) would cut cost enough to justify leaking into
the interface.
