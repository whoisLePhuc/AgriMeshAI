from __future__ import annotations

import json
import os
import re
import ssl
import time
import urllib.request
import urllib.error
from typing import Any

from edge_agent.dotenv import load_dotenv
from edge_agent.logger import get_logger
from edge_agent.providers.base import Provider
from edge_agent.tool import Tool
from edge_agent.types import Message, ToolCall

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
_API_KEY_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
_MODEL_ENV_VARS = ("EDGE_AGENT_MODEL", "TINYAGENT_MODEL")
_MODEL_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/-]*$")

logger = get_logger("providers.gemini")


def _resolve_model_from_env() -> str | None:
    """Prefer ``EDGE_AGENT_MODEL``, then legacy ``TINYAGENT_MODEL``."""
    load_dotenv()
    for var in _MODEL_ENV_VARS:
        value = os.environ.get(var)
        if value:
            return value
    return None

_SUPPORTED_SCHEMA_KEYS = frozenset({
    "type", "description", "properties", "required", "items",
    "enum", "format", "nullable", "anyOf",
})


def _sanitize_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Strip JSON Schema keys that Gemini's API does not accept.

    MCP servers (and other sources) may include standard JSON Schema
    fields like ``additionalProperties``, ``$schema``, ``title``, etc.
    Gemini rejects unknown fields, so we keep only the supported subset
    and recurse into nested schemas.
    """
    cleaned: dict[str, Any] = {}
    for key, value in schema.items():
        if key not in _SUPPORTED_SCHEMA_KEYS:
            continue
        if key == "properties" and isinstance(value, dict):
            cleaned[key] = {
                k: _sanitize_schema(v) if isinstance(v, dict) else v
                for k, v in value.items()
            }
        elif key == "items" and isinstance(value, dict):
            cleaned[key] = _sanitize_schema(value)
        elif key == "anyOf" and isinstance(value, list):
            cleaned[key] = [
                _sanitize_schema(v) if isinstance(v, dict) else v
                for v in value
            ]
        else:
            cleaned[key] = value
    return cleaned


def _resolve_api_key() -> str:
    """Load .env and return the first available API key, or raise."""
    load_dotenv()
    for var in _API_KEY_VARS:
        value = os.environ.get(var)
        if value:
            return value
    raise EnvironmentError(
        f"No API key found. Set one of {', '.join(_API_KEY_VARS)} "
        f"in your environment or in a .env file."
    )


class GeminiProvider(Provider):
    """Gemini REST API provider using only ``urllib`` from the stdlib.

    If *api_key* is ``None`` (the default), it is resolved automatically
    from ``GEMINI_API_KEY`` or ``GOOGLE_API_KEY`` environment variables
    (a ``.env`` file is loaded first).

    If *model* is ``None`` (the default), the ``EDGE_AGENT_MODEL`` env var
    is checked, then legacy ``TINYAGENT_MODEL``, before falling back to
    ``DEFAULT_MODEL``.
    """

    DEFAULT_MODEL = "gemini-3.1-flash-lite-preview"
    DEFAULT_TIMEOUT = 60
    DEFAULT_MAX_RETRIES = 3
    DEFAULT_RETRY_BACKOFF = 2.0

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        verify_ssl: bool | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_backoff: float = DEFAULT_RETRY_BACKOFF,
    ) -> None:
        self._api_key = api_key or _resolve_api_key()
        resolved_model = model or _resolve_model_from_env() or self.DEFAULT_MODEL
        if not _MODEL_RE.match(resolved_model):
            raise ValueError(
                f"Invalid model name: {resolved_model!r}. "
                f"Model names must contain only alphanumeric characters, "
                f"dots, hyphens, underscores, and forward slashes."
            )
        self.model = resolved_model
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        if verify_ssl is None:
            env_val = os.environ.get("EDGE_AGENT_VERIFY_SSL", "").lower()
            verify_ssl = env_val not in ("false", "0", "no") if env_val else True
        self._ssl_context: ssl.SSLContext | None = None
        if not verify_ssl:
            self._ssl_context = ssl.create_default_context()
            self._ssl_context.check_hostname = False
            self._ssl_context.verify_mode = ssl.CERT_NONE

    def __repr__(self) -> str:
        masked = "***" + self._api_key[-4:] if self._api_key else "<unset>"
        return f"GeminiProvider(api_key={masked!r}, model={self.model!r})"

    # -- public API -----------------------------------------------------------

    def chat(
        self,
        messages: list[Message],
        tools: list[Tool] | None = None,
        output_schema: dict[str, object] | None = None,
    ) -> Message:
        payload = self._build_payload(messages, tools, output_schema)
        logger.debug(
            "request payload (%d content part(s), %d tool decl(s))",
            len(payload.get("contents", [])),
            sum(len(td.get("functionDeclarations", []))
                for td in payload.get("tools", [])),
        )

        data = self._request(payload)
        logger.debug(
            "response payload (%d candidate(s))",
            len(data.get("candidates", [])),
        )

        return self._parse_response(data)

    # -- payload construction -------------------------------------------------

    def _build_payload(
        self,
        messages: list[Message],
        tools: list[Tool] | None,
        output_schema: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}

        system_parts: list[dict[str, str]] = []
        contents: list[dict[str, Any]] = []

        for msg in messages:
            if msg.role == "system":
                system_parts.append({"text": msg.content or ""})
                continue

            if msg.role == "user":
                contents.append({
                    "role": "user",
                    "parts": [{"text": msg.content or ""}],
                })
            elif msg.role == "assistant":
                parts: list[dict[str, Any]] = []
                if msg.content:
                    parts.append({"text": msg.content})
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        fc: dict[str, Any] = {
                            "name": tc.name,
                            "args": tc.arguments,
                        }
                        if tc.id is not None:
                            fc["id"] = tc.id
                        part_dict: dict[str, Any] = {"functionCall": fc}
                        if tc.thought_signature is not None:
                            part_dict["thoughtSignature"] = tc.thought_signature
                        parts.append(part_dict)
                contents.append({"role": "model", "parts": parts})
            elif msg.role == "tool" and msg.tool_result is not None:
                fr: dict[str, Any] = {
                    "name": msg.tool_result.tool_name or "",
                    "response": {"result": msg.tool_result.content},
                }
                contents.append({
                    "role": "user",
                    "parts": [{"functionResponse": fr}],
                })

        if system_parts:
            payload["system_instruction"] = {"parts": system_parts}

        payload["contents"] = contents

        if tools:
            payload["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": t.name,
                            "description": t.description,
                            "parameters": _sanitize_schema(t.parameters),
                        }
                        for t in tools
                    ]
                }
            ]

        if output_schema is not None:
            payload["generationConfig"] = {
                "responseMimeType": "application/json",
                "responseSchema": _sanitize_schema(output_schema),
            }

        return payload

    # -- response parsing -----------------------------------------------------

    def _parse_response(self, data: dict[str, Any]) -> Message:
        candidate = data["candidates"][0]
        parts = candidate["content"]["parts"]

        tool_calls: list[ToolCall] = []
        text_parts: list[str] = []

        for part in parts:
            if "functionCall" in part:
                fc = part["functionCall"]
                tool_calls.append(
                    ToolCall(
                        name=fc["name"],
                        arguments=fc.get("args", {}),
                        id=fc.get("id"),
                        thought_signature=part.get("thoughtSignature"),
                    )
                )
            elif "text" in part:
                text_parts.append(part["text"])

        return Message(
            role="assistant",
            content="\n".join(text_parts) if text_parts else None,
            tool_calls=tool_calls or None,
        )

    # -- HTTP -----------------------------------------------------------------

    _RETRY_DELAY_RE = re.compile(r"Please retry in ([\d.]+)s")

    def _parse_retry_delay(self, error_body: str) -> float | None:
        """Extract the retry delay from a 429 error body, if present."""
        match = self._RETRY_DELAY_RE.search(error_body)
        if match:
            return float(match.group(1))
        return None

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{_BASE_URL}/{self.model}:generateContent"
        body = json.dumps(payload).encode()

        last_exc: Exception | None = None

        for attempt in range(1 + self.max_retries):
            req = urllib.request.Request(
                url,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": self._api_key,
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(
                    req, timeout=self.timeout, context=self._ssl_context,
                ) as resp:
                    return json.loads(resp.read().decode())
            except urllib.error.HTTPError as exc:
                error_body = exc.read().decode() if exc.fp else ""

                if exc.code != 429:
                    logger.error("Gemini API error %s", exc.code)
                    logger.debug("error response body: %s", error_body)
                    raise RuntimeError(
                        f"Gemini API request failed ({exc.code})"
                    ) from exc

                if attempt >= self.max_retries:
                    logger.error(
                        "Rate limited (429), no retries left "
                        "(max_retries=%d)", self.max_retries,
                    )
                    logger.debug("error response body: %s", error_body)
                    raise RuntimeError(
                        f"Gemini API request failed after "
                        f"{self.max_retries} retries (429)"
                    ) from exc

                delay = (
                    self._parse_retry_delay(error_body)
                    or self.retry_backoff * (attempt + 1)
                )
                logger.warning(
                    "Rate limited (429), retrying in %.1fs "
                    "(attempt %d/%d)",
                    delay, attempt + 1, self.max_retries,
                )
                time.sleep(delay)

        raise RuntimeError("Unreachable")
