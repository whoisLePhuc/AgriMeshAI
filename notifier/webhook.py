"""Webhook notifier — sends alerts as HTTP POST JSON to a configurable URL.

Useful for integrating with external systems: Blynk, IFTTT, Home Assistant,
custom dashboards, etc.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from notifier.base import BaseNotifier

logger = logging.getLogger(__name__)


class WebhookNotifier(BaseNotifier):
    """HTTP POST alerts as JSON to a configurable URL.

    Config (notifiers.yaml)::

        webhook:
          enabled: true
          url: "https://hooks.example.com/alert"
          headers:
            Authorization: "Bearer secret123"
    """

    def __init__(self, config: dict[str, Any]) -> None:
        url = config.get("url")
        if not url:
            raise ValueError("Webhook: 'url' is required")
        self._url = str(url)
        self._headers: dict[str, str] = {
            k: str(v) for k, v in config.get("headers", {}).items()
        }
        self._client = httpx.AsyncClient(timeout=10)
        logger.info("webhook: configured for %s", self._url)

    @property
    def name(self) -> str:
        return "webhook"

    async def send_alert(
        self,
        rule_id: str,
        severity: str,
        message: str,
        device_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        payload = {
            "event": "alert_triggered",
            "rule_id": rule_id,
            "severity": severity,
            "message": message,
            "device_id": device_id,
        }
        await self._post(payload)

    async def send_report(self, title: str, body: str) -> None:
        payload = {"event": "report", "title": title, "body": body}
        await self._post(payload)

    async def _post(self, payload: dict[str, Any]) -> None:
        try:
            resp = await self._client.post(
                self._url, json=payload, headers=self._headers,
            )
            if resp.status_code >= 400:
                logger.warning(
                    "webhook %s returned %s", self._url, resp.status_code,
                )
        except httpx.RequestError as e:
            logger.warning("webhook request failed: %s", e)

    async def close(self) -> None:
        await self._client.aclose()
