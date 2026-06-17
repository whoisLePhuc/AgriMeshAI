from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    id: str | None = None
    thought_signature: str | None = None


@dataclass
class ToolResult:
    content: str
    tool_call_id: str | None = None
    tool_name: str | None = None


@dataclass
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_result: ToolResult | None = None


@dataclass
class ToolCallRecord:
    """A single tool invocation with full context."""

    name: str
    arguments: dict[str, Any]
    result: str
    duration_ms: float


@dataclass
class AgentStep:
    """One agent's execution trace within a run."""

    agent_name: str
    agent_type: str
    tools_used: list[ToolCallRecord]
    output: str
    turns: int


@dataclass
class RunResult:
    """Structured result from Agent.run() or Chain.run()."""

    output: str
    steps: list[AgentStep]
    parsed: Any | None = None

    def __str__(self) -> str:
        return self.output
