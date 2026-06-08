from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

from edge_agent.dotenv import load_dotenv
from edge_agent.logger import get_logger
from edge_agent.providers.base import Provider
from edge_agent.tool import Tool
from edge_agent.types import Message, ToolCall

_CONVERSE_URL = (
    "https://bedrock-runtime.{region}.amazonaws.com"
    "/model/{model_id}/converse"
)
_API_KEY_ENV_VAR = "AWS_BEARER_TOKEN_BEDROCK"
_MODEL_ENV_VAR = "BEDROCK_MODEL_ID"
_REGION_ENV_VAR = "AWS_DEFAULT_REGION"
_MODEL_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9.:/_-]*$")

logger = get_logger("providers.bedrock")

_JSON_SYSTEM_PROMPT = (
    "You MUST respond with valid JSON that conforms to the following "
    "JSON schema. Output ONLY the JSON object, no markdown fences, "
    "no explanation, no extra text.\n\nSchema:\n{schema}"
)


def _resolve_api_key() -> str | None:
    """Return the Bedrock API key from the environment, or ``None``."""
    load_dotenv()
    return os.environ.get(_API_KEY_ENV_VAR) or None


def _resolve_model_id() -> str | None:
    load_dotenv()
    return os.environ.get(_MODEL_ENV_VAR) or None


def _resolve_region() -> str | None:
    load_dotenv()
    return os.environ.get(_REGION_ENV_VAR) or None


class BedrockProvider(Provider):
    """Amazon Bedrock provider using the Converse API.

    Authenticates with a **Bedrock API key** (Bearer token) and uses only
    ``urllib`` from the stdlib — no ``boto3`` required.

    Generate an API key in the `Amazon Bedrock console
    <https://console.aws.amazon.com/bedrock>`_ under **API keys**, then pass
    it via *api_key* or the ``AWS_BEARER_TOKEN_BEDROCK`` environment variable.

    If *model_id* is ``None``, the ``BEDROCK_MODEL_ID`` env var is checked
    before falling back to ``DEFAULT_MODEL``.

    If *region_name* is ``None``, the ``AWS_DEFAULT_REGION`` env var is
    checked before falling back to ``DEFAULT_REGION``.
    """

    DEFAULT_MODEL = "us.anthropic.claude-sonnet-4-20250514-v1:0"
    DEFAULT_REGION = "us-east-1"
    DEFAULT_TIMEOUT = 120
    DEFAULT_MAX_RETRIES = 3
    DEFAULT_RETRY_BACKOFF = 2.0

    def __init__(
        self,
        api_key: str | None = None,
        model_id: str | None = None,
        region_name: str | None = None,
        *,
        inference_config: dict[str, Any] | None = None,
        additional_model_request_fields: dict[str, Any] | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_backoff: float = DEFAULT_RETRY_BACKOFF,
        supports_tool_use: bool = True,
        supports_structured_output: bool = False,
    ) -> None:
        self._api_key = api_key or _resolve_api_key()
        if not self._api_key:
            raise EnvironmentError(
                "No Bedrock API key found. Generate one in the Amazon "
                "Bedrock console (API keys section) and either pass it as "
                "api_key or set the AWS_BEARER_TOKEN_BEDROCK environment "
                "variable."
            )

        resolved_model = model_id or _resolve_model_id() or self.DEFAULT_MODEL
        if not _MODEL_ID_RE.match(resolved_model):
            raise ValueError(
                f"Invalid Bedrock model ID: {resolved_model!r}. "
                f"Model IDs must contain only alphanumeric characters, "
                f"dots, hyphens, underscores, colons, and forward slashes."
            )
        self.model_id = resolved_model

        self.region_name = region_name or _resolve_region() or self.DEFAULT_REGION
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.supports_tool_use = supports_tool_use
        self.supports_structured_output = supports_structured_output
        self._inference_config = inference_config
        self._additional_model_request_fields = additional_model_request_fields

    def __repr__(self) -> str:
        masked = "***" + self._api_key[-4:] if self._api_key else "<unset>"
        return (
            f"BedrockProvider(api_key={masked!r}, "
            f"model_id={self.model_id!r}, "
            f"region_name={self.region_name!r})"
        )

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
            len(
                payload.get("toolConfig", {})
                .get("tools", [])
            ),
        )

        data = self._request(payload)
        logger.debug("response stopReason=%s", data.get("stopReason"))

        return self._parse_response(data)

    # -- payload construction -------------------------------------------------

    def _build_payload(
        self,
        messages: list[Message],
        tools: list[Tool] | None,
        output_schema: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        if tools and not self.supports_tool_use:
            raise RuntimeError(
                f"Tool use is not supported by this Bedrock provider "
                f"configuration (model_id={self.model_id!r}). Set "
                f"supports_tool_use=True or remove tools from the agent."
            )

        payload: dict[str, Any] = {"modelId": self.model_id}
        system_blocks: list[dict[str, Any]] = []
        converse_messages: list[dict[str, Any]] = []

        for msg in messages:
            if msg.role == "system":
                system_blocks.append({"text": msg.content or ""})
            elif msg.role == "user":
                converse_messages.append({
                    "role": "user",
                    "content": [{"text": msg.content or ""}],
                })
            elif msg.role == "assistant":
                content: list[dict[str, Any]] = []
                if msg.content:
                    content.append({"text": msg.content})
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        content.append({
                            "toolUse": {
                                "toolUseId": tc.id or uuid.uuid4().hex[:8],
                                "name": tc.name,
                                "input": tc.arguments,
                            }
                        })
                if content:
                    converse_messages.append({
                        "role": "assistant",
                        "content": content,
                    })
            elif msg.role == "tool" and msg.tool_result is not None:
                tool_result_block = {
                    "toolResult": {
                        "toolUseId": msg.tool_result.tool_call_id or "",
                        "content": [{"text": msg.tool_result.content}],
                    }
                }
                # Bedrock requires alternating user/assistant roles, so
                # consecutive tool results must be grouped into one user
                # message.
                if (
                    converse_messages
                    and converse_messages[-1]["role"] == "user"
                    and any(
                        "toolResult" in block
                        for block in converse_messages[-1]["content"]
                    )
                ):
                    converse_messages[-1]["content"].append(tool_result_block)
                else:
                    converse_messages.append({
                        "role": "user",
                        "content": [tool_result_block],
                    })

        if output_schema is not None and not self.supports_structured_output:
            schema_text = json.dumps(output_schema, indent=2)
            system_blocks.append({
                "text": _JSON_SYSTEM_PROMPT.format(schema=schema_text),
            })

        if system_blocks:
            payload["system"] = system_blocks

        payload["messages"] = converse_messages

        if tools:
            payload["toolConfig"] = {
                "tools": [
                    {
                        "toolSpec": {
                            "name": t.name,
                            "description": t.description,
                            "inputSchema": {"json": t.parameters},
                        }
                    }
                    for t in tools
                ]
            }

        if self._inference_config:
            payload["inferenceConfig"] = self._inference_config

        if self._additional_model_request_fields:
            payload["additionalModelRequestFields"] = (
                self._additional_model_request_fields
            )

        return payload

    # -- response parsing -----------------------------------------------------

    def _parse_response(self, data: dict[str, Any]) -> Message:
        output = data.get("output")
        if not output or "message" not in output:
            raise RuntimeError(
                "Bedrock returned a malformed response: missing "
                "'output.message'. This may indicate the model is not "
                "compatible with the Converse API."
            )

        msg = output["message"]
        content_blocks = msg.get("content", [])

        tool_calls: list[ToolCall] = []
        text_parts: list[str] = []

        for block in content_blocks:
            if "text" in block:
                text_parts.append(block["text"])
            elif "toolUse" in block:
                tu = block["toolUse"]
                tool_calls.append(
                    ToolCall(
                        name=tu["name"],
                        arguments=tu.get("input", {}),
                        id=tu.get("toolUseId"),
                    )
                )

        return Message(
            role="assistant",
            content="\n".join(text_parts) if text_parts else None,
            tool_calls=tool_calls or None,
        )

    # -- HTTP -----------------------------------------------------------------

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = _CONVERSE_URL.format(
            region=self.region_name,
            model_id=self.model_id,
        )
        # modelId is part of the URL path; don't send it in the body
        body_payload = {k: v for k, v in payload.items() if k != "modelId"}
        body = json.dumps(body_payload).encode()

        last_exc: Exception | None = None

        for attempt in range(1 + self.max_retries):
            req = urllib.request.Request(
                url,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._api_key}",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(
                    req, timeout=self.timeout,
                ) as resp:
                    return json.loads(resp.read().decode())
            except urllib.error.HTTPError as exc:
                error_body = exc.read().decode() if exc.fp else ""
                last_exc = exc

                error_detail = _parse_error_body(error_body)
                error_type = error_detail.get("type", "")
                error_msg = error_detail.get(
                    "message", error_body[:200] or str(exc),
                )

                if exc.code == 403:
                    raise RuntimeError(
                        f"Bedrock access denied for model "
                        f"{self.model_id!r} in {self.region_name!r}. "
                        f"Ensure model access is enabled in the Amazon "
                        f"Bedrock console and your API key has the "
                        f"required permissions. ({error_msg})"
                    ) from exc

                if exc.code == 400:
                    raise RuntimeError(
                        f"Bedrock request validation failed: {error_msg}. "
                        f"Model {self.model_id!r} may not be available in "
                        f"region {self.region_name!r}, or the request "
                        f"parameters may be invalid."
                    ) from exc

                if exc.code == 404:
                    raise RuntimeError(
                        f"Bedrock model {self.model_id!r} not found in "
                        f"region {self.region_name!r}. Verify the model "
                        f"ID and region are correct."
                    ) from exc

                if exc.code == 429:
                    if attempt >= self.max_retries:
                        logger.error(
                            "Throttled (429), no retries left "
                            "(max_retries=%d)", self.max_retries,
                        )
                        raise RuntimeError(
                            f"Bedrock API request throttled after "
                            f"{self.max_retries} retries (429). "
                            f"Reduce request rate or request a quota "
                            f"increase."
                        ) from exc

                    delay = self.retry_backoff * (attempt + 1)
                    logger.warning(
                        "Throttled (429), retrying in %.1fs "
                        "(attempt %d/%d)",
                        delay, attempt + 1, self.max_retries,
                    )
                    time.sleep(delay)
                    continue

                logger.error("Bedrock API error %s", exc.code)
                logger.debug("error response body: %s", error_body)
                raise RuntimeError(
                    f"Bedrock API request failed ({exc.code}): {error_msg}"
                ) from exc

            except urllib.error.URLError as exc:
                raise RuntimeError(
                    f"Cannot connect to Bedrock in region "
                    f"{self.region_name!r}. Check your network "
                    f"connection and region name. ({exc.reason})"
                ) from exc

        raise RuntimeError("Unreachable")


def _parse_error_body(raw: str) -> dict[str, str]:
    """Best-effort parse of a Bedrock JSON error response."""
    try:
        data = json.loads(raw)
        return {
            "type": data.get("type", data.get("__type", "")),
            "message": data.get("message", data.get("Message", "")),
        }
    except (json.JSONDecodeError, AttributeError):
        return {"type": "", "message": raw[:200]}
