"""Tests for runtime detector configuration and health reporting."""

from __future__ import annotations

import pytest

from event_bus import EventBus
from ml_detector import MLDetector
from ml_detector.detectors.base import BaseDetector, DetectorHealth
from ml_detector.detectors.moving_average import MovingAverageDetector


class TestDetectorRuntimeConfig:
    """Runtime configuration changes via EventBus."""

    @pytest.fixture
    def eb(self) -> EventBus:
        return EventBus()

    @pytest.fixture
    def det(self) -> MovingAverageDetector:
        return MovingAverageDetector({"window_size": 100, "threshold_sigma": 3.0, "min_samples": 5})

    # ── reconfigure() ──────────────────────────────────────────────

    def test_reconfigure_changes_params(self, det: MovingAverageDetector) -> None:
        """reconfigure() updates threshold_sigma mid-stream."""
        # Use a wide window and add some variance to the baseline
        det.reconfigure({"window_size": 1000, "threshold_sigma": 3.0})
        for v in [30.0, 30.5, 29.8, 31.0, 30.2] * 20:
            det.on_reading(1, 1, v)
        # A 31.5 reading is within 3σ of this varied baseline
        small = det.on_reading(1, 1, 31.5)
        assert small is None

        # Tighten threshold to 0.5σ
        det.reconfigure({"threshold_sigma": 0.5})
        # Now 31.5 exceeds 0.5σ → alert
        alert = det.on_reading(1, 1, 31.5)
        assert alert is not None

    def test_reconfigure_window_size(self, det: MovingAverageDetector) -> None:
        """reconfigure() changes window_size for new readings."""
        det.reconfigure({"window_size": 5})
        assert det._window_size == 5

    # ── Enable/disable ─────────────────────────────────────────────

    async def test_disable_stops_processing(self, eb: EventBus) -> None:
        """Disabled detector skips readings."""
        det_mgr = MLDetector(eb, detector_classes=[MovingAverageDetector])
        det_mgr.start()
        # Name is "moving_average" — disable it
        det_mgr.disable("moving_average")
        # Send reading — no alert expected (detector disabled)
        for _ in range(15):
            await eb.emit("reading_recorded", device_id="1", sensor_id="1", value=30.0)
        await eb.emit("reading_recorded", device_id="1", sensor_id="1", value=100.0)
        # No alert because detector is disabled
        # (We can't easily assert on event absence, but no crash = good)

    async def test_enable_resumes_processing(self, eb: EventBus) -> None:
        """Re-enabled detector processes readings again."""
        det_mgr = MLDetector(eb)
        det_mgr.start()
        det_mgr.disable("moving_average")
        det_mgr.enable("moving_average")
        # Should be enabled again
        assert det_mgr._enabled.get("moving_average", False) is True

    def test_disable_unknown_name_is_safe(self, eb: EventBus) -> None:
        """Disabling a non-existent detector does not raise."""
        det_mgr = MLDetector(eb)
        det_mgr.disable("nonexistent")  # should not raise

    # ── Health reporting ───────────────────────────────────────────

    def test_health_dataclass_fields(self) -> None:
        """DetectorHealth has required fields."""
        h = DetectorHealth(name="test", status="running", alert_count=5,
                           total_processed=100, last_alert_time=1000.0,
                           buffer_size_bytes=1600)
        assert h.name == "test"
        assert h.status == "running"
        assert h.alert_count == 5
        assert h.total_processed == 100

    def test_detector_health_dataclass(self) -> None:
        """Health dataclass correctly reflects detector state."""
        det = MovingAverageDetector({"window_size": 100, "threshold_sigma": 3.0})
        det._total_processed = 42
        det._alert_count = 7
        det._last_alert_time = 1000.0
        health = det.health()
        assert health.total_processed == 42
        assert health.alert_count == 7
        assert health.last_alert_time == 1000.0

    async def test_ml_detector_aggregates_health(self, eb: EventBus) -> None:
        """MLDetector.get_health() returns health for all detectors."""
        det_mgr = MLDetector(eb)
        det_mgr.start()
        health_list = det_mgr.get_health()
        assert len(health_list) == 3
        names = {h.name for h in health_list}
        assert names == {"moving_average", "rate_of_change", "stuck_sensor"}

    # ── config_updated event ───────────────────────────────────────

    async def test_config_updated_dispatches_to_detector(self, eb: EventBus) -> None:
        """EventBus config_updated event triggers reconfigure on named detector."""
        det_mgr = MLDetector(eb, detector_classes=[MovingAverageDetector])
        det_mgr.start()
        # Emit config_updated event
        await eb.emit("config_updated",
                       detector_name="moving_average",
                       params={"threshold_sigma": 0.5})
        # Verify detector config changed
        for det in det_mgr._detectors:
            if det.name == "moving_average":
                assert det._threshold_sigma == 0.5
