from __future__ import annotations

import itertools
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypeVar, overload

from edge_agent.logger import get_logger
from edge_agent.providers.base import Provider
from edge_agent.template import render_template
from edge_agent.tool import Tool
from edge_agent.types import (
    AgentStep,
    Message,
    RunResult,
    ToolCall,
    ToolCallRecord,
    ToolResult,
)

if TYPE_CHECKING:
    from edge_agent.mcp import MCPServer

T = TypeVar("T")

AgentType = Literal["agent", "guardrail", "router", "evaluator", "fallback"]

_VALID_AGENT_TYPES: set[str] = {"agent", "guardrail", "router", "evaluator", "fallback"}

_agent_counter = itertools.count(1)


def _auto_name(agent_type: str) -> str:
    return f"{agent_type}-{next(_agent_counter)}"


def _resolve_instructions(instructions: str | Path) -> str:
    if isinstance(instructions, Path):
        return instructions.read_text(encoding="utf-8")
    return instructions


class Agent:
    """A minimal AI agent that can use tools and supports tool chaining.

    The agent loop sends the conversation to the LLM provider, executes any
    requested tool calls, appends the results, and repeats until the model
    returns a plain text response or *max_turns* is reached.

    If *provider* is omitted a :class:`~edge_agent.providers.gemini.GeminiProvider`
    is created automatically (API key resolved from the environment).

    If *name* is omitted an auto-incrementing name like ``"agent-1"`` is used.

    .. admonition:: Security — ``instructions`` as a Path

       When *instructions* is a :class:`~pathlib.Path`, the file is read
       verbatim at construction time with **no path validation or
       sandboxing**.  The caller is responsible for ensuring the path
       points to a trusted file; never build it from untrusted user input.
    """

    def __init__(
        self,
        *,
        name: str | None = None,
        instructions: str | Path = "You are a helpful assistant.",
        provider: Provider | None = None,
        tools: list[Tool] | None = None,
        mcp_servers: list[MCPServer] | None = None,
        agent_type: AgentType = "agent",
        output_type: type | None = None,
    ) -> None:
        if agent_type not in _VALID_AGENT_TYPES:
            raise ValueError(
                f"Invalid agent_type: {agent_type!r}. "
                f"Must be one of: {', '.join(sorted(_VALID_AGENT_TYPES))}"
            )
        self.name = name or _auto_name(agent_type)
        self.agent_type: AgentType = agent_type
        self.instructions: str = _resolve_instructions(instructions)
        self.output_type: type | None = output_type
        self.provider = provider or self._default_provider()
        self.tools = self._build_tool_map(tools or [])
        self.mcp_servers: list[MCPServer] = list(mcp_servers or [])
        self._logger = get_logger(f"agent.{self.name}")
        self._connect_mcp_servers()

    @staticmethod
    def _default_provider() -> Provider:
        from edge_agent.providers.gemini import GeminiProvider
        return GeminiProvider()

    @staticmethod
    def _build_tool_map(tools: list[Tool]) -> dict[str, Tool]:
        tool_map: dict[str, Tool] = {}
        for t in tools:
            if t.name in tool_map:
                raise ValueError(
                    f"Duplicate tool name: {t.name!r}. "
                    f"Each tool must have a unique name."
                )
            tool_map[t.name] = t
        return tool_map

    def _connect_mcp_servers(self) -> None:
        for server in self.mcp_servers:
            if not server.connected:
                server.connect()
            for t in server.tools:
                if t.name in self.tools:
                    raise ValueError(
                        f"Duplicate tool name from MCP server "
                        f"{server.name!r}: {t.name!r}. "
                        f"Each tool must have a unique name."
                    )
                self.tools[t.name] = t

    def close(self) -> None:
        """Close all MCP server connections owned by this agent."""
        for server in self.mcp_servers:
            server.close()

    def __enter__(self) -> Agent:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # -- public API -----------------------------------------------------------

    def run(
        self,
        user_message: str,
        *,
        max_turns: int = 10,
        extra_tools: list[Tool] | None = None,
        template_vars: dict[str, str] | None = None,
        output_type: type[T] | None = None,
    ) -> RunResult:
        """Run the agent loop and return a :class:`RunResult`.

        The result contains the final text in ``result.output``, an
        optional parsed dataclass in ``result.parsed``, and a list of
        :class:`AgentStep` entries in ``result.steps`` that trace every
        tool call made during the run.

        *extra_tools* are made available for this run only, without
        permanently modifying the agent's tool set.

        *template_vars* are substituted into the system instructions
        before sending (e.g. ``{{currentDate}}``, ``{{userName}}``).

        *output_type*, when set to a dataclass type, enables structured
        output: the LLM is asked to return JSON conforming to the
        dataclass schema and the result is parsed into an instance of
        that type.  Falls back to ``self.output_type`` when not supplied.
        """
        effective_output_type = output_type or self.output_type

        if extra_tools:
            run_tools = {**self.tools, **self._build_tool_map(extra_tools)}
        else:
            run_tools = self.tools

        system_content = render_template(self.instructions, template_vars)

        messages: list[Message] = [
            Message(role="system", content=system_content),
            Message(role="user", content=user_message),
        ]

        self._logger.info(
            "run started — %r", user_message[:80] + ("…" if len(user_message) > 80 else ""),
        )

        output_schema: dict[str, object] | None = None
        if effective_output_type is not None:
            from edge_agent.schema import schema_from_dataclass

            output_schema = schema_from_dataclass(effective_output_type)

        tools_list = list(run_tools.values()) or None
        tool_records: list[ToolCallRecord] = []

        for turn in range(1, max_turns + 1):
            response = self.provider.chat(messages, tools_list, output_schema)
            messages.append(response)

            if not response.tool_calls:
                self._logger.info("run finished in %d turn(s)", turn)
                output_text = response.content or ""
                parsed = None
                if effective_output_type is not None:
                    from edge_agent.schema import parse_json_to_dataclass

                    parsed = parse_json_to_dataclass(
                        effective_output_type, output_text or "{}"
                    )
                step = AgentStep(
                    agent_name=self.name,
                    agent_type=self.agent_type,
                    tools_used=tool_records,
                    output=output_text,
                    turns=turn,
                )
                return RunResult(output=output_text, steps=[step], parsed=parsed)

            self._logger.info(
                "turn %d — %d tool call(s)",
                turn,
                len(response.tool_calls),
            )

            for tc in response.tool_calls:
                msg, record = self._execute_tool(tc, run_tools)
                messages.append(msg)
                tool_records.append(record)

        self._logger.warning("max_turns (%d) reached", max_turns)
        last_content = messages[-1].content or ""
        parsed = None
        if effective_output_type is not None:
            from edge_agent.schema import parse_json_to_dataclass

            parsed = parse_json_to_dataclass(
                effective_output_type, last_content or "{}"
            )
        step = AgentStep(
            agent_name=self.name,
            agent_type=self.agent_type,
            tools_used=tool_records,
            output=last_content,
            turns=max_turns,
        )
        return RunResult(output=last_content, steps=[step], parsed=parsed)

    # -- internals ------------------------------------------------------------

    def _execute_tool(
        self,
        tool_call: ToolCall,
        tool_map: dict[str, Tool] | None = None,
    ) -> tuple[Message, ToolCallRecord]:
        tools = tool_map if tool_map is not None else self.tools
        tool = tools.get(tool_call.name)
        if tool is None:
            error_msg = f"Unknown tool: {tool_call.name}"
            self._logger.error(error_msg)
            msg = Message(
                role="tool",
                tool_result=ToolResult(
                    content=error_msg,
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                ),
            )
            record = ToolCallRecord(
                name=tool_call.name,
                arguments=tool_call.arguments,
                result=error_msg,
                duration_ms=0.0,
            )
            return msg, record

        self._logger.info("executing tool %r", tool_call.name)
        self._logger.debug("tool %r arguments: %s", tool_call.name, tool_call.arguments)

        start = time.perf_counter()
        try:
            result = tool(**tool_call.arguments)
        except Exception as exc:
            self._logger.error(
                "tool %r raised %s", tool_call.name, type(exc).__name__,
            )
            self._logger.debug("tool %r exception detail: %s", tool_call.name, exc)
            result = f"Error: tool {tool_call.name!r} failed with {type(exc).__name__}"
        elapsed_ms = (time.perf_counter() - start) * 1000

        content = result if isinstance(result, str) else str(result)

        msg = Message(
            role="tool",
            content=content,
            tool_result=ToolResult(
                content=content,
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
            ),
        )
        record = ToolCallRecord(
            name=tool_call.name,
            arguments=tool_call.arguments,
            result=content,
            duration_ms=elapsed_ms,
        )
        return msg, record


# ── typed subclasses ────────────────────────────────────────────────────────


class Guardrail(Agent):
    """An agent that gates requests — can block or allow them in a Chain."""

    def __init__(
        self,
        *,
        name: str | None = None,
        instructions: str | Path = "You are a safety guardrail.",
        provider: Provider | None = None,
        tools: list[Tool] | None = None,
        mcp_servers: list[MCPServer] | None = None,
        output_type: type | None = None,
    ) -> None:
        super().__init__(
            name=name,
            instructions=instructions,
            provider=provider,
            tools=tools,
            mcp_servers=mcp_servers,
            agent_type="guardrail",
            output_type=output_type,
        )


class Router(Agent):
    """An agent that routes requests to the appropriate specialist in a Chain."""

    def __init__(
        self,
        *,
        name: str | None = None,
        instructions: str | Path = "Route the user's request to the most appropriate agent.",
        provider: Provider | None = None,
        tools: list[Tool] | None = None,
        mcp_servers: list[MCPServer] | None = None,
        output_type: type | None = None,
    ) -> None:
        super().__init__(
            name=name,
            instructions=instructions,
            provider=provider,
            tools=tools,
            mcp_servers=mcp_servers,
            agent_type="router",
            output_type=output_type,
        )


class Evaluator(Agent):
    """An agent that reviews output and can approve or request revisions in a Chain."""

    def __init__(
        self,
        *,
        name: str | None = None,
        instructions: str | Path = "Review the output and approve or request revisions.",
        provider: Provider | None = None,
        tools: list[Tool] | None = None,
        mcp_servers: list[MCPServer] | None = None,
        output_type: type | None = None,
    ) -> None:
        super().__init__(
            name=name,
            instructions=instructions,
            provider=provider,
            tools=tools,
            mcp_servers=mcp_servers,
            agent_type="evaluator",
            output_type=output_type,
        )


class Fallback(Agent):
    """An agent that can signal failure so the Chain tries the next agent."""

    def __init__(
        self,
        *,
        name: str | None = None,
        instructions: str | Path = "You are a helpful assistant.",
        provider: Provider | None = None,
        tools: list[Tool] | None = None,
        mcp_servers: list[MCPServer] | None = None,
        output_type: type | None = None,
    ) -> None:
        super().__init__(
            name=name,
            instructions=instructions,
            provider=provider,
            tools=tools,
            mcp_servers=mcp_servers,
            agent_type="fallback",
            output_type=output_type,
        )
