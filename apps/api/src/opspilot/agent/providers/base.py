"""The provider interface (ADR 0005).

Deliberately narrow. Everything a run needs to be accounted for — usage, latency,
finish reason — is normalized here, so cost and latency reporting does not branch on
which provider answered.

No agent framework. The loop that uses this lives in ``agent/orchestrator.py`` and is
meant to be read in one sitting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

Role = Literal["system", "user", "assistant", "tool"]


@dataclass(slots=True)
class Message:
    role: Role
    content: str
    #: Set on a tool-result message so the provider can correlate it with its call.
    tool_call_id: str | None = None
    name: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    #: Raw, unvalidated arguments as the model produced them. Validation happens in
    #: the dispatcher against the Pydantic model, never here.
    arguments: dict[str, Any]


@dataclass(slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(slots=True)
class ProviderResult:
    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    finish_reason: str = "stop"
    latency_ms: int = 0
    model: str = ""

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class ModelProvider(Protocol):
    """Synchronous by design (ADR 0009): the orchestrator runs inside a Celery worker.

    Implementations must:

    * honour ``deadline_s`` — the run deadline is not negotiable;
    * raise ``ProviderUnavailable`` for transport/5xx/timeout, never return a fake
      answer;
    * populate ``usage`` even on a failed parse, so budget accounting stays correct.
    """

    name: str

    def complete(
        self,
        *,
        messages: list[Message],
        tools: list[dict[str, Any]],
        deadline_s: float,
        max_output_tokens: int,
    ) -> ProviderResult: ...
