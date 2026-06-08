"""Telegram notifier — sends alerts via Telegram Bot HTTP API.

Requires: bot_token and chat_id in config (or env vars).
No extra dependency — uses httpx (already installed for edge-agent).
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from notifier.base import BaseNotifier

logger = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org/bot{token}/sendMessage"

# Severity → emoji prefix
_PREFIXES = {
    "CRITICAL": "🚨 *CRITICAL*",
    "WARNING": "⚠️ *WARNING*",
    "INFO": "ℹ️ *INFO*",
}


class TelegramNotifier(BaseNotifier):
    """Sends alerts to a Telegram chat via bot API.

    Config (notifiers.yaml)::

        telegram:
          enabled: true
          bot_token: "${TELEGRAM_BOT_TOKEN}"   # or literal token
          chat_id: "${TELEGRAM_CHAT_ID}"        # or literal chat ID
    """

    def __init__(self, config: dict[str, Any]) -> None:
        token = self._resolve(config, "bot_token", "TELEGRAM_BOT_TOKEN")
        chat_id = self._resolve(config, "chat_id", "TELEGRAM_CHAT_ID")

        if not token or not chat_id:
            raise ValueError(
                "Telegram: bot_token and chat_id required "
                "(set in config or env TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)"
            )

        self._token = token
        self._chat_id = chat_id
        self._api_url = _API_BASE.format(token=token)
        self._client = httpx.AsyncClient(timeout=10)
        logger.info("telegram: configured for chat %s", chat_id)

    @staticmethod
    def _resolve(config: dict[str, Any], key: str, env_var: str) -> str | None:
        """Resolve a config value — inline string or env var reference."""
        val = config.get(key)
        if not val:
            return os.environ.get(env_var)
        s = str(val)
        # Support ${ENV_VAR} syntax
        if s.startswith("${") and s.endswith("}"):
            return os.environ.get(s[2:-1])
        return s

    @property
    def name(self) -> str:
        return "telegram"

    async def _send(self, text: str, parse_mode: str = "Markdown") -> None:
        """Send a message to the configured chat."""
        try:
            resp = await self._client.post(
                self._api_url,
                json={
                    "chat_id": self._chat_id,
                    "text": text,
                    "parse_mode": parse_mode,
                    "disable_notification": False,
                },
            )
            if resp.status_code != 200:
                logger.warning(
                    "telegram API error %s: %s", resp.status_code, resp.text,
                )
        except httpx.RequestError as e:
            logger.warning("telegram request failed: %s", e)

    async def send_alert(
        self,
        rule_id: str,
        severity: str,
        message: str,
        device_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        prefix = _PREFIXES.get(severity, f"*{severity}*")
        header = f"{prefix} `{rule_id}`"
        if device_id:
            header += f" | `{device_id}`"
        text = f"{header}\n{message}"
        await self._send(text)

    async def send_report(self, title: str, body: str) -> None:
        text = f"*📊 {title}*\n\n{body}"
        await self._send(text)

    async def close(self) -> None:
        await self._client.aclose()
