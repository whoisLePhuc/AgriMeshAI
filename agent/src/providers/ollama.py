from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from src.dotenv import load_dotenv
from src.logger import get_logger
from src.providers.base import Provider
from src.tool import Tool
from src.types import Message, ToolCall

_DEFAULT_BASE_URL = "http://localhost:11434"
_MODEL_ENV_VAR = "OLLAMA_MODEL"
_HOST_ENV_VAR = "OLLAMA_HOST"

logger = get_logger("providers.ollama")


def _resolve_base_url() -> str:
    load_dotenv()
    return os.environ.get(_HOST_ENV_VAR, _DEFAULT_BASE_URL).rstrip("/")


def _resolve_model() -> str | None:
    load_dotenv()
    return os.environ.get(_MODEL_ENV_VAR) or None


class OllamaProvider(Provider):
    """Ollama provider using the OpenAI-compatible ``/v1/chat/completions``
    endpoint, implemented with only ``urllib`` from the stdlib.

    No API key is required — Ollama runs locally.

    If *model* is ``None`` (the default), the ``OLLAMA_MODEL`` env var is
    checked before falling back to ``DEFAULT_MODEL``.

    If *base_url* is ``None``, the ``OLLAMA_HOST`` env var is checked before
    falling back to ``http://localhost:11434``.
    """

    DEFAULT_MODEL = "llama3.2"
    DEFAULT_TIMEOUT = 120

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        temperature: float | None = None,
    ) -> None:
        self.model = model or _resolve_model() or self.DEFAULT_MODEL
        self._base_url = (base_url or _resolve_base_url()).rstrip("/")
        self.timeout = timeout
        self.temperature = temperature

    def __repr__(self) -> str:
        return f"OllamaProvider(model={self.model!r}, base_url={self._base_url!r})"

    # -- public API -----------------------------------------------------------

    def chat(
        self,
        messages: list[Message],
        tools: list[Tool] | None = None,
        output_schema: dict[str, object] | None = None,
    ) -> Message:
        payload = self._build_payload(messages, tools, output_schema)
        logger.debug(
            "request payload (%d message(s), %d tool decl(s))",
            len(payload.get("messages", [])),
            len(payload.get("tools", [])),
        )

        data = self._request(payload)
        logger.debug(
            "response payload (%d choice(s))",
            len(data.get("choices", [])),
        )

        return self._parse_response(data)

    # -- payload construction -------------------------------------------------

    def _build_payload(
        self,
        messages: list[Message],
        tools: list[Tool] | None,
        output_schema: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "stream": False,
        }
        if self.temperature is not None:
            payload["temperature"] = self.temperature

        oai_messages: list[dict[str, Any]] = []

        for msg in messages:
            if msg.role == "system":
                oai_messages.append({
                    "role": "system",
                    "content": msg.content or "",
                })
            elif msg.role == "user":
                oai_messages.append({
                    "role": "user",
                    "content": msg.content or "",
                })
            elif msg.role == "assistant":
                m: dict[str, Any] = {"role": "assistant"}
                if msg.content:
                    m["content"] = msg.content
                if msg.tool_calls:
                    m["tool_calls"] = [
                        {
                            "id": tc.id or "",
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments),
                            },
                        }
                        for tc in msg.tool_calls
                    ]
                oai_messages.append(m)
            elif msg.role == "tool" and msg.tool_result is not None:
                oai_messages.append({
                    "role": "tool",
                    "tool_call_id": msg.tool_result.tool_call_id or "",
                    "content": msg.tool_result.content,
                })

        payload["messages"] = oai_messages

        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]

        if output_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "response",
                    "strict": True,
                    "schema": output_schema,
                },
            }

        return payload

    # -- response parsing -----------------------------------------------------

    def _parse_response(self, data: dict[str, Any]) -> Message:
        choice = data["choices"][0]
        message = choice["message"]

        content = message.get("content") or None
        raw_tool_calls = message.get("tool_calls")

        tool_calls: list[ToolCall] | None = None
        if raw_tool_calls:
            parsed: list[ToolCall] = []
            for tc in raw_tool_calls:
                fn = tc["function"]
                raw_args = fn.get("arguments", "{}")
                try:
                    arguments = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except json.JSONDecodeError as exc:
                    raise RuntimeError(
                        f"Ollama returned invalid JSON in tool call "
                        f"arguments: {exc}"
                    ) from exc
                parsed.append(ToolCall(
                    name=fn["name"],
                    arguments=arguments,
                    id=tc.get("id"),
                ))
            tool_calls = parsed or None

        return Message(
            role="assistant",
            content=content,
            tool_calls=tool_calls,
        )

    # -- HTTP -----------------------------------------------------------------

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base_url}/v1/chat/completions"
        body = json.dumps(payload).encode()

        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode() if exc.fp else ""
            logger.error("Ollama API error %s", exc.code)
            logger.debug("error response body: %s", error_body)
            if exc.code == 404:
                raise RuntimeError(
                    f"Ollama model {self.model!r} not found (404). "
                    f"Run: ollama pull {self.model}"
                ) from exc
            raise RuntimeError(
                f"Ollama API request failed ({exc.code})"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Cannot connect to Ollama at {self._base_url}. "
                f"Is Ollama running?"
            ) from exc
