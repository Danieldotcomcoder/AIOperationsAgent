import json
from decimal import Decimal

import httpx
import pytest
import respx
from pydantic import ValidationError

from opspilot.agent.pricing import configured_cost
from opspilot.agent.providers import build_provider
from opspilot.agent.providers.anthropic_provider import _split_system
from opspilot.agent.providers.base import Message, ToolCall
from opspilot.core.config import Settings
from opspilot.core.errors import ProviderUnavailable


@pytest.fixture(autouse=True)
def mockable_transport(monkeypatch):
    # Recent SDKs default to httpx2, which respx does not intercept. Explicitly
    # inject the supported httpx transport so every request is covered by respx.
    import openai

    original = openai.OpenAI
    clients = []

    def factory(**kwargs):
        client = httpx.Client()
        clients.append(client)
        return original(http_client=client, **kwargs)

    monkeypatch.setattr(openai, "OpenAI", factory)
    yield
    for client in clients:
        client.close()


def settings(**values) -> Settings:
    return Settings(
        _env_file=None,
        llm_provider="openrouter",
        llm_model="openrouter/free",
        openrouter_api_key="test-key",
        **values,
    )


def completion(**message) -> dict:
    return {
        "id": "test",
        "object": "chat.completion",
        "created": 1,
        "model": "vendor/actual-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "{}", **message},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


@respx.mock
def test_openrouter_transport_and_tool_history() -> None:
    route = respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=completion())
    )
    provider = build_provider(settings())
    result = provider.complete(
        messages=[
            Message("user", "Investigate SO-1042"),
            Message(
                "assistant",
                "",
                tool_calls=[ToolCall("call-1", "get_order_context", {"order_number": "SO-1042"})],
            ),
            Message("tool", "{}", tool_call_id="call-1"),
        ],
        tools=[{"name": "get_order_context", "parameters": {"type": "object"}}],
        deadline_s=2,
        max_output_tokens=100,
    )
    request = route.calls[0].request
    body = json.loads(request.content)
    assert request.headers["authorization"] == "Bearer test-key"
    assert body["provider"] == {
        "require_parameters": True,
        "max_price": {"prompt": 0, "completion": 0},
    }
    assert body["messages"][1]["tool_calls"][0]["id"] == "call-1"
    assert body["messages"][2]["tool_call_id"] == "call-1"
    assert body["response_format"] == {"type": "json_object"}
    assert result.model == "vendor/actual-model"
    assert result.usage.input_tokens == 10
    assert configured_cost(settings(), result.model, 10, 5) == 0


@respx.mock
@pytest.mark.parametrize(
    "model",
    [
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "inclusionai/ling-3.0-flash-fin:free",
    ],
)
def test_tool_only_models_omit_unsupported_json_mode(model: str) -> None:
    route = respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=completion(content="not valid JSON"))
    )
    config = settings().model_copy(update={"llm_model": model})
    result = build_provider(config).complete(
        messages=[Message("user", "test")],
        tools=[{"name": "get_order_context", "parameters": {"type": "object"}}],
        deadline_s=1,
        max_output_tokens=100,
    )
    body = json.loads(route.calls[0].request.content)
    assert "response_format" not in body
    assert body["tools"]
    assert body["provider"]["require_parameters"] is True
    assert body["provider"]["max_price"] == {"prompt": 0, "completion": 0}
    # Transport does not repair or fabricate a valid answer: validation owns this.
    assert result.text == "not valid JSON"


@respx.mock
@pytest.mark.parametrize("status", [401, 429, 503])
def test_openrouter_errors_do_not_retry_in_sdk(status: int) -> None:
    route = respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(status, json={"error": {"message": "unavailable"}})
    )
    with pytest.raises(ProviderUnavailable):
        build_provider(settings()).complete(
            messages=[Message("user", "test")], tools=[], deadline_s=1, max_output_tokens=100
        )
    assert route.call_count == 1


@respx.mock
def test_malformed_tool_arguments_are_not_executable() -> None:
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json=completion(
                tool_calls=[
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "search_policies", "arguments": "[]"},
                    }
                ]
            ),
        )
    )
    result = build_provider(settings()).complete(
        messages=[], tools=[], deadline_s=1, max_output_tokens=100
    )
    assert result.tool_calls[0].arguments == {}


def test_free_only_rejects_paid_and_auto_routes() -> None:
    for model in ["vendor/paid", "openrouter/auto", "openrouter/auto:free"]:
        with pytest.raises(ValidationError, match="Free-only"):
            Settings(
                _env_file=None,
                llm_provider="openrouter",
                llm_model=model,
                openrouter_api_key="test-key",
            )
    with pytest.raises(ValidationError, match="price estimates"):
        settings(openrouter_free_only=False)


def test_paid_cost_and_explicit_key_requirement() -> None:
    config = settings(
        openrouter_free_only=False,
        openrouter_input_usd_per_million=1,
        openrouter_output_usd_per_million=2,
    )
    assert configured_cost(config, "vendor/model", 1000000, 1000000) == Decimal("3")
    with pytest.raises(ValidationError, match="OPENROUTER_API_KEY"):
        Settings(_env_file=None, llm_provider="openrouter", llm_model="openrouter/free")


def test_anthropic_replays_tool_use_block() -> None:
    _, turns = _split_system(
        [
            Message(
                "assistant",
                "",
                tool_calls=[ToolCall("call-1", "search_policies", {"query": "delay"})],
            )
        ]
    )
    assert turns[0]["content"][0] == {
        "type": "tool_use",
        "id": "call-1",
        "name": "search_policies",
        "input": {"query": "delay"},
    }


def test_anthropic_final_turn_declares_tools_but_forbids_their_use() -> None:
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from opspilot.agent.providers.anthropic_provider import AnthropicProvider

    provider = AnthropicProvider(api_key="test", model="claude-test")
    seen: dict[str, object] = {}

    def create(**kwargs: object) -> SimpleNamespace:
        seen.update(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="{}")],
            usage=SimpleNamespace(input_tokens=5, output_tokens=2),
            stop_reason="end_turn",
            model="claude-test",
        )

    provider._client = MagicMock()
    provider._client.messages.create.side_effect = create
    calls = [
        ToolCall("c1", "get_order_context", {"order_number": "SO-1042"}),
        ToolCall("c2", "search_policies", {"query": "delay"}),
    ]
    history = [
        Message("system", "rules"),
        Message("user", "Investigate SO-1042"),
        Message("assistant", "", tool_calls=calls),
        Message("tool", "{}", tool_call_id="c1", name="get_order_context"),
        Message("tool", "{}", tool_call_id="c2", name="search_policies"),
        Message("system", "final turn"),
    ]
    provider.complete(messages=history, tools=[], deadline_s=5, max_output_tokens=100)
    assert seen["tool_choice"] == {"type": "none"}
    assert [t["name"] for t in seen["tools"]] == [  # type: ignore[union-attr]
        "get_order_context",
        "list_overdue_orders",
        "search_policies",
    ]
    turns = seen["messages"]
    # Both results answer one assistant turn, so they travel in one user turn.
    assert [t["role"] for t in turns] == ["user", "assistant", "user"]  # type: ignore[union-attr]
    assert [b["tool_use_id"] for b in turns[2]["content"]] == ["c1", "c2"]  # type: ignore[index]
    assert seen["system"] == "rules\n\nfinal turn"
