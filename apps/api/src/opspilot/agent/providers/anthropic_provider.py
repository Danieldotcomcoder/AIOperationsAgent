"""Anthropic adapter (ADR 0005).

The alternative provider, selected with ``LLM_PROVIDER=anthropic``. Tool-calling shape
differs from OpenAI's — content is a list of typed blocks, the system prompt is a
top-level parameter rather than a message, and tool results go back as ``tool_result``
blocks inside a user turn — and normalizing all of that here is precisely why the
adapter exists.

STATUS: written against the documented API, not yet exercised against the live service
(no key configured). Anything here is unverified until a run with a real key is made
and this note is removed.
"""

from __future__ import annotations

import json
import time
from typing import Any, cast

from opspilot.agent.providers.base import Message, ProviderResult, ToolCall, Usage
from opspilot.core.errors import ProviderUnavailable
from opspilot.core.logging import get_logger

log = get_logger(__name__)


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, model: str) -> None:
        from anthropic import Anthropic

        self._client = Anthropic(api_key=api_key, max_retries=0)
        self.model = model

    def complete(
        self,
        *,
        messages: list[Message],
        tools: list[dict[str, Any]],
        deadline_s: float,
        max_output_tokens: int,
    ) -> ProviderResult:
        from anthropic import APIError, APITimeoutError, RateLimitError, omit
        from anthropic.types import MessageParam, ToolChoiceNoneParam, ToolParam

        system, turns = _split_system(messages)
        offered, choice = _tool_request(messages, tools)
        started = time.monotonic()
        try:
            response = self._client.messages.create(
                model=self.model,
                system=system,
                messages=cast(list[MessageParam], turns),
                tools=cast(list[ToolParam], [_to_anthropic_tool(t) for t in offered])
                if offered
                else omit,
                tool_choice=cast(ToolChoiceNoneParam, choice) if choice else omit,
                max_tokens=max_output_tokens,
                timeout=deadline_s,
            )
        except (APITimeoutError, RateLimitError) as exc:
            raise ProviderUnavailable(f"anthropic transient failure: {type(exc).__name__}") from exc
        except APIError as exc:
            raise ProviderUnavailable(f"anthropic error: {type(exc).__name__}") from exc

        latency_ms = int((time.monotonic() - started) * 1000)
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                arguments = block.input if isinstance(block.input, dict) else {}
                calls.append(ToolCall(id=block.id, name=block.name, arguments=arguments))

        return ProviderResult(
            text="".join(text_parts) or None,
            tool_calls=calls,
            usage=Usage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            ),
            finish_reason=response.stop_reason or "stop",
            latency_ms=latency_ms,
            model=response.model,
        )


def _tool_request(
    messages: list[Message], tools: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, str] | None]:
    """Tools to declare, and a tool choice when their use must be refused.

    The orchestrator offers no tools on the final synthesis turn. Anthropic rejects a
    request whose history holds ``tool_use``/``tool_result`` blocks but defines no
    tools, so that turn re-declares the allowlist with ``tool_choice: none``: the model
    can read its earlier calls and cannot make another.
    """
    if tools:
        return tools, None
    if any(m.role == "tool" or m.tool_calls for m in messages):
        from opspilot.agent.tools.definitions import all_tool_schemas

        return all_tool_schemas(), {"type": "none"}
    return [], None


def _split_system(messages: list[Message]) -> tuple[str, list[dict[str, Any]]]:
    """Anthropic takes the system prompt as its own parameter, not as a turn."""
    system = "\n\n".join(m.content for m in messages if m.role == "system")
    turns: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "system":
            continue
        if m.role == "tool":
            # A tool result is a block inside a *user* turn, not a role of its own, and
            # every result for one assistant turn belongs in the same user turn.
            block = {"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.content}
            previous = turns[-1] if turns else None
            if (
                previous is not None
                and previous["role"] == "user"
                and isinstance(previous["content"], list)
                and all(item.get("type") == "tool_result" for item in previous["content"])
            ):
                previous["content"].append(block)
            else:
                turns.append({"role": "user", "content": [block]})
        elif m.role == "assistant" and m.tool_calls:
            blocks: list[dict[str, Any]] = []
            if m.content:
                blocks.append({"type": "text", "text": m.content})
            blocks.extend(
                {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
                for c in m.tool_calls
            )
            turns.append({"role": "assistant", "content": blocks})
        else:
            turns.append({"role": m.role, "content": m.content})
    return system, turns


def _to_anthropic_tool(tool: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": tool["name"],
        "description": tool["description"],
        "input_schema": tool["parameters"],
    }


__all__ = ["AnthropicProvider", "json"]
