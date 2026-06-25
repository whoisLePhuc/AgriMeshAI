"""Interactive terminal REPL for edge_agent."""

from __future__ import annotations

from src.agent import Agent
from src.logger import get_logger
from src.types import Message


class Session:
    """Wraps an :class:`Agent` in an interactive input/output loop.

    Conversation history is preserved across turns so the agent remembers
    prior context within the session.
    """

    def __init__(
        self,
        agent: Agent,
        *,
        max_turns: int = 10,
        user_label: str = "You",
        agent_label: str = "Agent",
    ) -> None:
        self.agent = agent
        self.max_turns = max_turns
        self.user_label = user_label
        self.agent_label = agent_label
        self._logger = get_logger(f"session.{agent.name}")
        self._messages: list[Message] = [
            Message(role="system", content=agent.instructions),
        ]

    def start(self) -> None:
        """Block and run the REPL until the user exits."""
        tools_list = list(self.agent.tools.values()) or None

        print(f"\nedge-agent live session — {self.agent.name}")
        print("Type 'exit' or 'quit' to end the session.\n")

        try:
            while True:
                try:
                    user_input = input(f"{self.user_label}: ")
                except EOFError:
                    break

                stripped = user_input.strip()
                if stripped.lower() in ("exit", "quit"):
                    break
                if not stripped:
                    continue

                self._messages.append(
                    Message(role="user", content=user_input),
                )

                self._logger.info(
                    "user: %r", user_input[:80] + ("…" if len(user_input) > 80 else ""),
                )

                for _turn in range(1, self.max_turns + 1):
                    response = self.agent.provider.chat(
                        self._messages, tools_list,
                    )
                    self._messages.append(response)

                    if not response.tool_calls:
                        break

                    for tc in response.tool_calls:
                        print(f"  🔧 {tc.name}({tc.arguments})")
                        self._logger.info(
                            "turn %d — %d tool call(s)",
                            _turn,
                            len(response.tool_calls),
                        )

                    for tc in response.tool_calls:
                        result_msg, _ = self.agent._execute_tool(tc)
                        self._messages.append(result_msg)

                answer = response.content or ""
                print(f"{self.agent_label}: {answer}")

        except KeyboardInterrupt:
            pass

        print("\nGoodbye!")
