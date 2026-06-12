"""Abstract base for all notification channels."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseNotifier(ABC):
    """Interface that every notification channel must implement."""

    @abstractmethod
    async def send_alert(
        self,
        rule_id: str,
        severity: str,
        message: str,
        device_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Deliver an alert triggered by the Rule Engine."""
        ...

    @abstractmethod
    async def send_report(self, title: str, body: str) -> None:
        """Deliver a periodic summary report."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable channel name (e.g. 'telegram', 'webhook')."""
        ...
