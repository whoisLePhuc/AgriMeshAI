"""Console notifier — prints alerts to stderr with severity-coloring."""

from __future__ import annotations

import logging
import sys
from typing import Any

from notifier.base import BaseNotifier

logger = logging.getLogger(__name__)

# Severity → icon mapping
_ICONS = {
    "CRITICAL": "🔴",
    "WARNING": "🟡",
    "INFO": "🔵",
}


class ConsoleNotifier(BaseNotifier):
    """Prints alerts to terminal stderr. Always enabled unless explicitly disabled."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}

    @property
    def name(self) -> str:
        return "console"

    async def send_alert(
        self,
        rule_id: str,
        severity: str,
        message: str,
        device_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        icon = _ICONS.get(severity, "•")
        line = f"{icon} [{severity}] {rule_id}: {message}"
        print(line, file=sys.stderr)

    async def send_report(self, title: str, body: str) -> None:
        print(f"\n=== {title} ===", file=sys.stderr)
        print(body, file=sys.stderr)
        print("=" * (len(title) + 8), file=sys.stderr)
