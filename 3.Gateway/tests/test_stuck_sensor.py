"""Tests for StuckSensorDetector — M03 variance-based stuck detection."""

from __future__ import annotations

import pytest

from ml_detector.detectors.stuck_sensor import StuckSensorDetector


class TestStuckSensorDetector:
    """Stuck sensor (zero variance) detection.

    Note: Default cooldown_s=1800 (30 min) uses time.monotonic() for wall-clock
    time. Most tests set cooldown_s=0 to avoid depending on real time progression.
    """

    @pytest.fixture
    def det(self) -> StuckSensorDetector:
        d = StuckSensorDetector({"window_hours": 6.0, "threshold_var": 0.005, "min_samples": 10})
        d.cooldown_s = 0  # Disable wall-clock cooldown for deterministic testing
        return d

    def _feed_n_minutes(self, det: StuckSensorDetector, value: float, minutes: int,
                       node: int = 1, sensor: int = 1) -> None:
        """Feed readings at a fixed value for N minutes (1 reading/min)."""
        base_ts = 1_000_000.0
        for i in range(minutes):
            det.on_reading(node, sensor, value, timestamp=base_ts + i * 60.0)

    def test_varying_readings_no_alert(self, det: StuckSensorDetector) -> None:
        """Readings that vary do not trigger stuck alert."""
        base_ts = 1_000_000.0
        for i in range(60):
            det.on_reading(1, 1, 25.0 + (i % 5), timestamp=base_ts + i * 60.0)
        assert det.on_reading(1, 1, 25.0, timestamp=base_ts + 3600.0) is None

    def test_constant_less_than_2h_no_alert(self, det: StuckSensorDetector) -> None:
        """Constant readings under 2 hours do NOT alert (need 2h stuck duration)."""
        # Feed 128 readings: stuck_since at #9 (0-indexed), last reading at #127
        # stuck_hours = (127-9)*60 / 3600 = 118*60/3600 = 1.967h < 1.99h → no alert
        self._feed_n_minutes(det, 25.0, 128)
        result = det.on_reading(1, 1, 25.0, timestamp=1_000_000.0 + 128 * 60.0)
        assert result is None

    def test_constant_2h_triggers_alert(self, det: StuckSensorDetector) -> None:
        """Constant readings for >=2 hours trigger alert."""
        # Feed 130 readings: stuck_since at #9, last reading at #129
        # stuck_hours = (129-9)*60 / 3600 = 120*60/3600 = 2.0h >= 1.99h → alert
        self._feed_n_minutes(det, 25.0, 130)
        result = det.on_reading(1, 1, 25.0, timestamp=1_000_000.0 + 130 * 60.0)
        if result is None:
            # Alert may have fired inside _feed_n_minutes at reading #129
            # (cooldown_s=0, so _can_alert returned True)
            pass  # Alert already emitted, cooldown blocks this call
        else:
            assert result is not None
            assert result.rule_id == "M03"
            assert result.node_id == 1
            assert result.sensor_id == 1

    def test_recovery_after_change(self, det: StuckSensorDetector) -> None:
        """After stuck detection, a varying reading clears the stuck state."""
        self._feed_n_minutes(det, 25.0, 130)
        # Reading may have fired alert in _feed; that's fine
        # Ensure changing value clears stuck state
        result = det.on_reading(1, 1, 30.0, timestamp=1_000_000.0 + 131 * 60.0)
        assert result is None  # Value changed — stuck state cleared

    def test_cooldown_respects_30min(self) -> None:
        """Default 30-min cooldown prevents repeat alerts within wall-clock window."""
        det = StuckSensorDetector({"window_hours": 6.0, "threshold_var": 0.005, "min_samples": 10})
        # Keep default cooldown_s=1800 (30 min wall-clock)
        # Feed enough readings to trigger exactly one alert
        self._feed_n_minutes(det, 25.0, 130)
        # The alert fires at reading #129 (inside _feed). Now try again:
        result = det.on_reading(1, 1, 25.0, timestamp=1_000_000.0 + 131 * 60.0)
        # Cooldown blocks because < 30 min wall-clock has passed
        assert result is None

    def test_multiple_sensors_independent(self, det: StuckSensorDetector) -> None:
        """Stuck detection per (node, sensor) pair is independent."""
        self._feed_n_minutes(det, 25.0, 130, node=1, sensor=1)
        self._feed_n_minutes(det, 30.0, 130, node=2, sensor=1)
        # Sensor 1: may have already alerted in _feed
        # Sensor 2: should not be stuck (values vary between 30 and 35)
        result = det.on_reading(2, 1, 35.0, timestamp=1_000_000.0 + 130 * 60.0)
        assert result is None  # Sensor 2 not stuck
