"""MLDetector — orchestrates anomaly detection on the Edge Gateway.

Subscribes to ``EventBus("reading_recorded")``, runs each registered
detector on every reading, and emits ``alert_triggered`` events when
anomalies are found — exactly like the RuleEngine, so the Notifier
handles both transparently.

Usage::

    from ml_detector import MLDetector

    detector = MLDetector(event_bus, store=None, config={})
    detector.start()          # subscribe to EventBus
    detector.stop()           # unsubscribe
"""

from __future__ import annotations

import logging
from typing import Any

from event_bus import EventBus
from ml_detector.detectors.base import BaseDetector
from ml_detector.detectors.moving_average import MovingAverageDetector
from ml_detector.detectors.rate_of_change import RateOfChangeDetector
from ml_detector.detectors.stuck_sensor import StuckSensorDetector

logger = logging.getLogger(__name__)

_DEFAULT_DETECTORS = [
    MovingAverageDetector,
    RateOfChangeDetector,
    StuckSensorDetector,
]


class MLDetector:
    """Orchestrator: subscribes to ``reading_recorded``, dispatches to detectors."""

    def __init__(
        self,
        event_bus: EventBus,
        config: dict[str, Any] | None = None,
        detector_classes: list[type[BaseDetector]] | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._config = config or {}
        self._detectors: list[BaseDetector] = []

        classes = detector_classes or _DEFAULT_DETECTORS
        for cls in classes:
            cfg = self._config.get(cls.__name__, {})
            self._detectors.append(cls(cfg))

        self._subscribed = False
        logger.info(
            "MLDetector initialised with %d detector(s): %s",
            len(self._detectors),
            [d.name for d in self._detectors],
        )

    # ── lifecycle ─────────────────────────────────────────────────

    def start(self) -> None:
        """Subscribe to EventBus ``reading_recorded``."""
        if self._subscribed:
            return
        self._event_bus.on("reading_recorded", self._on_reading)
        self._subscribed = True
        logger.info("MLDetector started")

    def stop(self) -> None:
        """Unsubscribe from EventBus."""
        if not self._subscribed:
            return
        self._event_bus.off("reading_recorded", self._on_reading)
        self._subscribed = False
        logger.info("MLDetector stopped")

    # ── event handler ─────────────────────────────────────────────

    async def _on_reading(
        self,
        device_id: str | None = None,
        sensor_id: str | None = None,
        value: float | None = None,
        **kwargs: Any,
    ) -> None:
        if value is None or sensor_id is None:
            return

        # Convert device_id (str) → node_id (int) if possible
        try:
            node_id = int(device_id) if device_id else 0
        except (ValueError, TypeError):
            node_id = 0

        if sensor_id is not None:
            try:
                sensor_id_int = int(sensor_id)
            except (ValueError, TypeError):
                sensor_id_int = 0
        else:
            sensor_id_int = 0

        for det in self._detectors:
            try:
                alert = det.on_reading(node_id, sensor_id_int, value)
                if alert is not None:
                    await self._event_bus.emit(
                        "alert_triggered",
                        rule_id=alert.rule_id,
                        severity=alert.severity,
                        message=alert.message,
                        device_id=str(alert.node_id) if alert.node_id else None,
                        sensor_id=str(alert.sensor_id) if alert.sensor_id else None,
                        value=alert.value,
                    )
            except Exception:
                logger.exception("detector %s failed on_reading", det.name)
