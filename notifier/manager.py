"""NotifierManager — subscribes to alert_triggered, dispatches to all channels."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from event_bus import EventBus
from notifier.base import BaseNotifier
from notifier.console import ConsoleNotifier
from notifier.telegram import TelegramNotifier
from notifier.webhook import WebhookNotifier

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, type[BaseNotifier]] = {
    "console": ConsoleNotifier,
    "telegram": TelegramNotifier,
    "webhook": WebhookNotifier,
}


def register_notifier(name: str, cls: type[BaseNotifier]) -> None:
    """Register a custom notifier channel."""
    _REGISTRY[name] = cls


class NotifierManager:
    """Loads notifier config, instantiates enabled channels, dispatches alerts.

    Usage::

        manager = NotifierManager(bus, "config/notifiers.yaml")
        # Auto-subscribes to alert_triggered on the EventBus
    """

    def __init__(
        self,
        bus: EventBus,
        config_path: str = "config/notifiers.yaml",
    ) -> None:
        self._notifiers: list[BaseNotifier] = []
        self._load(config_path)

        if self._notifiers:
            bus.on("alert_triggered", self._on_alert)
            logger.info(
                "notifier manager ready: %d channel(s)", len(self._notifiers),
            )

    def _load(self, config_path: str) -> None:
        """Load notifier config from YAML and instantiate enabled channels."""
        path = Path(config_path)
        if not path.exists():
            logger.warning("notifier config not found: %s", config_path)
            return

        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        for name, cfg in data.get("notifiers", {}).items():
            if not cfg.get("enabled", False):
                continue
            cls = _REGISTRY.get(name)
            if cls is None:
                logger.warning("unknown notifier channel: %s", name)
                continue
            try:
                instance = cls(cfg)
                self._notifiers.append(instance)
                logger.info("  notifier: %s enabled", name)
            except Exception as e:
                logger.warning("failed to init %s: %s", name, e)

    async def _on_alert(
        self,
        rule_id: str | None = None,
        severity: str | None = None,
        message: str | None = None,
        device_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Dispatch an alert to every enabled channel."""
        for n in self._notifiers:
            try:
                await n.send_alert(
                    rule_id=rule_id or "",
                    severity=severity or "INFO",
                    message=message or "",
                    device_id=device_id,
                )
            except Exception:
                logger.exception("%s failed to send alert", n.name)

    async def send_report(self, title: str, body: str) -> None:
        """Send a periodic report to every enabled channel."""
        for n in self._notifiers:
            try:
                await n.send_report(title=title, body=body)
            except Exception:
                logger.exception("%s failed to send report", n.name)

    @property
    def channels(self) -> list[str]:
        """Names of all active channels."""
        return [n.name for n in self._notifiers]
