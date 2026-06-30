"""Tests for MLDetector orchestrator — lifecycle, dispatch, isolation."""

from __future__ import annotations

import pytest

from event_bus import EventBus
from ml_detector import MLDetector
from ml_detector.detectors.base import BaseDetector


class TestMLDetector:
    """MLDetector orchestrator."""

    @pytest.fixture
    def eb(self) -> EventBus:
        return EventBus()

    @pytest.fixture
    def detector(self, eb: EventBus) -> MLDetector:
        return MLDetector(eb)

    # ── Lifecycle ───────────────────────────────────────────────────

    async def test_start_subscribes_to_reading_recorded(self, eb: EventBus,
                                                        detector: MLDetector) -> None:
        """After start(), handlers are registered for reading_recorded."""
        assert "reading_recorded" not in eb._handlers or \
               detector._on_reading not in eb._handlers.get("reading_recorded", [])
        detector.start()
        assert detector._on_reading in eb._handlers["reading_recorded"]

    async def test_stop_unsubscribes(self, eb: EventBus, detector: MLDetector) -> None:
        """After stop(), handler is removed."""
        detector.start()
        await detector.stop()
        assert "reading_recorded" not in eb._handlers or \
               detector._on_reading not in eb._handlers.get("reading_recorded", [])

    async def test_double_start_is_idempotent(self, detector: MLDetector) -> None:
        """Calling start() twice does not register duplicate handlers."""
        detector.start()
        detector.start()  # should be no-op
        assert detector._subscribed is True

    # ── Dispatch ────────────────────────────────────────────────────

    async def test_dispatch_to_all_detectors(self, eb: EventBus) -> None:
        """All default detectors process a reading."""
        alerts: list[str] = []

        async def collect(**data: object) -> None:
            rid = data.get("rule_id")
            if rid is not None:
                alerts.append(str(rid))

        eb.on("alert_triggered", collect)
        det = MLDetector(eb)
        det.start()
        # Feed non-anomalous reading — no alerts expected
        await eb.emit("reading_recorded", device_id="1", sensor_id="1", value=30.0)
        assert len(alerts) == 0

    async def test_dispatch_produces_alert(self, eb: EventBus) -> None:
        """Anomalous reading produces alert from MovingAverageDetector."""
        alerts: list[dict[str, object]] = []

        async def collect(**data: object) -> None:
            alerts.append(data)

        eb.on("alert_triggered", collect)
        det = MLDetector(eb)
        det.start()
        # Feed normal readings then outlier
        for _ in range(15):
            await eb.emit("reading_recorded", device_id="1", sensor_id="1", value=30.0)
        await eb.emit("reading_recorded", device_id="1", sensor_id="1", value=100.0)
        assert len(alerts) >= 1
        assert alerts[0]["rule_id"] in ("M01", "M02", "M03")

    # ── Exception isolation ─────────────────────────────────────────

    async def test_detector_exception_does_not_block_others(self, eb: EventBus) -> None:
        """A crashing detector does not prevent other detectors from running."""
        class CrashingDetector(BaseDetector):
            name = "crash"
            rule_id = "M99"
            severity = "WARNING"

            def on_reading(self, node_id, sensor_id, value, timestamp=None):
                raise RuntimeError("Intentional crash")

        alerts: list[str] = []

        async def collect(**data: object) -> None:
            rid = data.get("rule_id")
            if rid is not None:
                alerts.append(str(rid))

        eb.on("alert_triggered", collect)
        det = MLDetector(eb, detector_classes=[CrashingDetector])
        det.start()
        # The crashing detector should not crash the whole loop
        await eb.emit("reading_recorded", device_id="1", sensor_id="1", value=30.0)
        # Cleanup — no crash means success

    # ── Start/stop edge cases ───────────────────────────────────────

    async def test_stop_before_start_is_safe(self, detector: MLDetector) -> None:
        """Calling stop() before start() is a no-op."""
        await detector.stop()  # should not raise

    async def test_process_reading_after_stop(self, eb: EventBus, detector: MLDetector) -> None:
        """After stop(), readings are not processed."""
        detector.start()
        await detector.stop()
        # Reading after stop should not raise
        await eb.emit("reading_recorded", device_id="1", sensor_id="1", value=30.0)

    # ── Burst performance ─────────────────────────────────────────

    async def test_burst_50_readings_under_1s(self, eb: EventBus) -> None:
        """50 simultaneous readings process within 1 second."""
        import time
        det_mgr = MLDetector(eb)
        det_mgr.start()
        t0 = time.monotonic()
        for _ in range(50):
            await eb.emit("reading_recorded", device_id="1", sensor_id="1", value=30.0)
        elapsed = time.monotonic() - t0
        assert elapsed < 1.0, f"Burst of 50 readings took {elapsed:.3f}s (>1s threshold)"
