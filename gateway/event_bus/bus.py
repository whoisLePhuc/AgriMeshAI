"""Simple pub/sub event bus for decoupled inter-module communication.

Modules publish events without knowing who subscribes.
Subscribers register handlers without modifying the publisher.

Usage::

    bus = EventBus()
    bus.on("reading_recorded", my_handler)
    await bus.emit("reading_recorded", device_id="sensor_1", value=32.5)
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

Handler = Callable[..., Awaitable[Any]]


class EventBus:
    """Lightweight pub/sub event bus. Zero dependencies."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = {}

    def on(self, event: str, handler: Handler) -> None:
        """Register a handler for an event type."""
        self._handlers.setdefault(event, []).append(handler)
        logger.debug("registered handler %s for %s", handler.__name__, event)

    def off(self, event: str, handler: Handler) -> None:
        """Remove a previously registered handler. No-op if not found."""
        handlers = self._handlers.get(event)
        if handlers and handler in handlers:
            handlers.remove(handler)

    async def emit(self, event: str, **data: Any) -> None:
        """Fire an event. All registered handlers are called with **data.

        Each handler runs in sequence. A failing handler does not prevent
        others from running — the error is logged and the next handler
        proceeds.
        """
        for handler in self._handlers.get(event, []):
            try:
                await handler(**data)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("handler %s failed for event %s", handler.__name__, event)
