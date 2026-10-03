# ADR 0011 — OpenRouter with free-only routing by default

Accepted 2026-09-23. The project owner requested OpenRouter to access free services.

Use the installed OpenAI SDK with OpenRouter's fixed HTTPS API endpoint and a separate
secret. Preserve assistant tool calls and result IDs, usage, and the actual returned
model name. Keep retries in the bounded orchestrator. Require support for requested
tool/JSON parameters. No agent framework or new dependency is needed.

Free-only mode accepts `openrouter/free` or a specific vendor model's `:free` variant,
rejects the paid auto router, and sends zero input/output price ceilings. No model
fallback list or paid plugin is requested. Paid mode is explicit and requires operator
price estimates; reservations scale with configured run bounds and those rates. Costs
remain estimates, not invoice truth. Embedding accounting is separate from chat costs.

Mocked transport tests cover routing constraints, tool history, usage, malformed tool
arguments and error normalization. Availability and quality of free models still need
live verification and human evaluation. Pin a model for repeatable evaluation; the free
router can choose different compatible models across requests.

References: [OpenRouter quickstart](https://openrouter.ai/docs/quickstart),
[free router](https://openrouter.ai/openrouter/free),
[provider routing](https://openrouter.ai/docs/guides/routing/provider-selection).
