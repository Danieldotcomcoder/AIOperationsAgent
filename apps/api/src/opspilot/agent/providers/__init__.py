"""Model provider adapters (ADR 0005).

One narrow protocol, four implementations, selected by configuration. Nothing above
this package knows which provider answered — that is what makes a provider comparison
a config change rather than a refactor.
"""

from __future__ import annotations

from opspilot.agent.providers.base import (
    Message,
    ModelProvider,
    ProviderResult,
    ToolCall,
    Usage,
)
from opspilot.core.config import Settings


def build_provider(settings: Settings, *, scenario: str = "healthy") -> ModelProvider:
    """Construct the configured provider.

    ``scenario`` only reaches the fake provider; it is how tests and evaluation cases
    request a specific failure mode (an unavailable provider, an unsupported citation,
    malformed output) without monkeypatching anything.
    """
    if settings.llm_provider == "fake":
        from opspilot.agent.providers.fake import FakeProvider

        return FakeProvider(scenario=scenario)

    if settings.llm_provider == "openrouter":
        from opspilot.agent.providers.openrouter_provider import OpenRouterProvider

        return OpenRouterProvider(
            settings.openrouter_api_key.get_secret_value(),
            settings.llm_model,
            free_only=settings.openrouter_free_only,
        )

    if settings.llm_provider == "openai":
        from opspilot.agent.providers.openai_provider import OpenAIProvider

        return OpenAIProvider(
            api_key=settings.openai_api_key.get_secret_value(), model=settings.llm_model
        )

    if settings.llm_provider == "anthropic":
        from opspilot.agent.providers.anthropic_provider import AnthropicProvider

        return AnthropicProvider(
            api_key=settings.anthropic_api_key.get_secret_value(), model=settings.llm_model
        )

    raise ValueError(f"unknown LLM_PROVIDER: {settings.llm_provider}")


__all__ = [
    "Message",
    "ModelProvider",
    "ProviderResult",
    "ToolCall",
    "Usage",
    "build_provider",
]
