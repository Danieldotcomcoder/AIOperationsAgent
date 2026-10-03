"""OpenRouter's OpenAI-compatible transport, with explicit routing constraints."""

from typing import Any

from opspilot.agent.providers.openai_provider import OpenAIProvider


class OpenRouterProvider(OpenAIProvider):
    name = "openrouter"

    def __init__(self, api_key: str, model: str, *, free_only: bool = True) -> None:
        routing: dict[str, Any] = {"require_parameters": True}
        if free_only:
            routing["max_price"] = {"prompt": 0, "completion": 0}
        super().__init__(
            api_key,
            model,
            base_url="https://openrouter.ai/api/v1",
            extra_body={"provider": routing},
            # These pinned free models advertise tools but not response_format
            # (OpenRouter catalog, 2026-09-25). The prompt still requests JSON and
            # the orchestrator still rejects any invalid schema or citation.
            json_mode=model
            not in {
                "nvidia/nemotron-3-ultra-550b-a55b:free",
                "inclusionai/ling-3.0-flash-fin:free",
            },
        )
