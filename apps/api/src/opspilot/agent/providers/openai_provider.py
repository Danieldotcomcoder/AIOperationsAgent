"""OpenAI adapter (ADR 0005).

Maps the provider-neutral ``Message``/``ToolSpec`` shapes onto the Chat Completions
API and normalizes the reply back. Nothing above this file knows which provider
answered.

Two rules this adapter must never break:

* a transport failure, a timeout or a 5xx raises ``ProviderUnavailable`` — it never
  returns a plausible-looking empty answer, because an empty answer is indistinguishable
  from "the model found nothing" and that distinction is the whole point of the
  incomplete-run handling;
* usage is populated even on a failed parse, so budget accounting stays correct when
  the response is unusable.
"""

from __future__ import annotations

import json
from typing import Any, cast

from opspilot.agent.providers.base import Message, ProviderResult, ToolCall, Usage
from opspilot.core.errors import ProviderUnavailable
from opspilot.core.logging import get_logger

log = get_logger(__name__)


class OpenAIProvider:
    name = "openai"

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        base_url: str | None = None,
        extra_body: dict[str, Any] | None = None,
        json_mode: bool = True,
    ) -> None:
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key, max_retries=0, base_url=base_url)
        self.model = model
        self._extra_body = extra_body
        self._json_mode = json_mode

    def complete(
        self,
        *,
        messages: list[Message],
        tools: list[dict[str, Any]],
        deadline_s: float,
        max_output_tokens: int,
    ) -> ProviderResult:
        import time

        from openai import APIError, APITimeoutError, RateLimitError, omit
        from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam

        started = time.monotonic()
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=cast(
                    list[ChatCompletionMessageParam], [_to_openai_message(m) for m in messages]
                ),
                tools=cast(
                    list[ChatCompletionToolParam],
                    [{"type": "function", "function": t} for t in tools],
                )
                if tools
                else omit,
                max_tokens=max_output_tokens,
                temperature=0,  # investigations should not vary run to run
                timeout=deadline_s,
                response_format={"type": "json_object"} if self._json_mode else omit,
                extra_body=self._extra_body,
            )
        except (APITimeoutError, RateLimitError) as exc:
            raise ProviderUnavailable(
                f"{self.name} transient failure: {type(exc).__name__}"
            ) from exc
        except APIError as exc:
            raise ProviderUnavailable(f"{self.name} error: {type(exc).__name__}") from exc

        latency_ms = int((time.monotonic() - started) * 1000)
        if not response.choices:
            raise ProviderUnavailable(f"{self.name} returned no completion")
        choice = response.choices[0]
        usage = Usage(
            input_tokens=getattr(response.usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(response.usage, "completion_tokens", 0) or 0,
        )

        calls: list[ToolCall] = []
        for raw in choice.message.tool_calls or []:
            if raw.type != "function":
                # This application only registers function tools. Custom calls have
                # no authority and remain a counted unknown-tool attempt.
                calls.append(ToolCall(id=raw.id, name="unknown", arguments={}))
                continue
            try:
                arguments = json.loads(raw.function.arguments or "{}")
                if not isinstance(arguments, dict):
                    arguments = {}
            except json.JSONDecodeError:
                # Unparseable arguments are an expected model failure. Hand the
                # dispatcher an empty dict; its validation will reject it and the
                # model gets a plain error back within its remaining budget.
                log.warning("provider.unparseable_tool_arguments")
                arguments = {}
            calls.append(ToolCall(id=raw.id, name=raw.function.name, arguments=arguments))

        return ProviderResult(
            text=choice.message.content,
            tool_calls=calls,
            usage=usage,
            finish_reason=choice.finish_reason or "stop",
            latency_ms=latency_ms,
            model=response.model,
        )


def _to_openai_message(message: Message) -> dict[str, Any]:
    if message.role == "assistant" and message.tool_calls:
        return {
            "role": "assistant",
            "content": message.content or None,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments),
                    },
                }
                for call in message.tool_calls
            ],
        }
    if message.role == "tool":
        return {
            "role": "tool",
            "tool_call_id": message.tool_call_id,
            "content": message.content,
        }
    return {"role": message.role, "content": message.content}
