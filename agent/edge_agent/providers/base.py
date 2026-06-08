from __future__ import annotations

from abc import ABC, abstractmethod

from edge_agent.tool import Tool
from edge_agent.types import Message


class Provider(ABC):
    """Abstract base for LLM providers.

    Each concrete provider translates between edge_agent's common types and the
    provider-specific API format, makes the HTTP call, and returns the result
    as a :class:`Message`.
    """

    @abstractmethod
    def chat(
        self,
        messages: list[Message],
        tools: list[Tool] | None = None,
        output_schema: dict[str, object] | None = None,
    ) -> Message:
        """Send a conversation to the LLM and return the assistant response."""
        ...
