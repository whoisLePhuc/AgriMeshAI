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
from ml_detector.detectors.base import BaseDetector, DetectorHealth
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
        self._enabled: dict[str, bool] = {}  # detector name → enabled flag

        classes = detector_classes or _DEFAULT_DETECTORS
        for cls in classes:
            cfg = self._config.get(cls.__name__, {})
            det = cls(cfg)
            self._detectors.append(det)
            self._enabled[det.name] = True

        self._subscribed = False
        logger.info(
            "MLDetector initialised with %d detector(s): %s",
            len(self._detectors),
            [d.name for d in self._detectors],
        )

    # ── enable / disable ──────────────────────────────────────────

    def enable(self, name: str) -> None:
        """Re-enable a previously disabled detector by name."""
        if name in self._enabled:
            self._enabled[name] = True
            logger.info("detector %s enabled", name)

    def disable(self, name: str) -> None:
        """Disable a detector by name. It will skip readings until re-enabled."""
        if name in self._enabled:
            self._enabled[name] = False
            logger.info("detector %s disabled", name)

    # ── health ────────────────────────────────────────────────────

    def get_health(self) -> list[DetectorHealth]:
        """Return health snapshot for all detectors."""
        return [d.health() for d in self._detectors]

    # ── lifecycle ─────────────────────────────────────────────────

    def start(self) -> None:
        """Subscribe to EventBus ``reading_recorded`` and ``config_updated``."""
        if self._subscribed:
            return
        self._event_bus.on("reading_recorded", self._on_reading)
        self._event_bus.on("config_updated", self._handle_config_updated)
        self._subscribed = True
        logger.info("MLDetector started")

    async def stop(self) -> None:
        """Unsubscribe from EventBus."""
        if not self._subscribed:
            return
        self._event_bus.off("reading_recorded", self._on_reading)
        self._event_bus.off("config_updated", self._handle_config_updated)
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

        import time
        _t0 = time.monotonic()
        for det in self._detectors:
            if not self._enabled.get(det.name, True):
                continue  # skip disabled detectors
            try:
                det._total_processed += 1
                alert = det.on_reading(node_id, sensor_id_int, value)
                if alert is not None:
                    det._alert_count += 1
                    det._last_alert_time = time.time()
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
        _elapsed = time.monotonic() - _t0
        if _elapsed > 1.0:
            logger.warning("MLDetector._on_reading took %.2fs (>1s burst threshold)", _elapsed)

    # ── config updated handler ────────────────────────────────────

    async def _handle_config_updated(
        self,
        detector_name: str | None = None,
        params: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Handle a ``config_updated`` EventBus event.

        Looks up the named detector and calls ``reconfigure(params)``.
        """
        if not detector_name or not params:
            return
        for det in self._detectors:
            if det.name == detector_name:
                det.reconfigure(params)
                logger.info("reconfigured %s: %s", detector_name, params)
                return
        logger.warning("config_updated: unknown detector %s", detector_name)
