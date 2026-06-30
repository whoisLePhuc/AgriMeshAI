"""Tests for MovingAverageDetector — M01 ±σ baseline deviation."""

from __future__ import annotations

import pytest

from ml_detector.detectors.moving_average import MovingAverageDetector


class TestMovingAverageDetector:
    """Moving average ±σ baseline deviation."""

    @pytest.fixture
    def det(self) -> MovingAverageDetector:
        return MovingAverageDetector({"window_size": 20, "threshold_sigma": 3.0, "min_samples": 5})

    def test_normal_baseline_no_alert(self, det: MovingAverageDetector) -> None:
        """Readings within 3σ of rolling mean produce no alert."""
        for v in [30.0] * 20:
            assert det.on_reading(1, 1, v) is None

    def test_sigma_threshold_breach_triggers_alert(self) -> None:
        """Reading far beyond 3σ from pre-established mean returns an AlertData."""
        det = MovingAverageDetector({"window_size": 100, "threshold_sigma": 3.0, "min_samples": 5})
        # Establish stable baseline with 50 readings
        for _ in range(50):
            assert det.on_reading(1, 1, 30.0) is None
        # Massive outlier — shifts the mean but 1000.0 is far enough from 30.0
        # to still exceed 3σ even with window=50 (30+50 readings)
        alert = det.on_reading(1, 1, 1000.0)
        assert alert is not None
        assert alert.rule_id == "M01"
        assert alert.severity == "WARNING"
        assert alert.node_id == 1
        assert alert.sensor_id == 1
        assert alert.value == 1000.0

    def test_zero_stddev_no_alert(self, det: MovingAverageDetector) -> None:
        """When all readings are identical (stddev=0), no alert."""
        for v in [25.0] * 5:
            assert det.on_reading(1, 1, v) is None

    def test_configurable_window_size(self) -> None:
        """Smaller window with sufficient baseline still detects anomalies."""
        det = MovingAverageDetector({"window_size": 20, "threshold_sigma": 3.0, "min_samples": 5})
        for _ in range(15):
            assert det.on_reading(2, 1, 30.0) is None
        alert = det.on_reading(2, 1, 300.0)
        assert alert is not None

    def test_configurable_threshold_sigma(self) -> None:
        """Lower sigma = more sensitive."""
        sensitive = MovingAverageDetector({"window_size": 20, "threshold_sigma": 1.0, "min_samples": 5})
        for v in [30.0] * 5:
            assert sensitive.on_reading(3, 1, v) is None
        # Small deviation triggers alert at 1σ
        alert = sensitive.on_reading(3, 1, 32.0)
        assert alert is not None

    def test_multiple_sensors_independent(self, det: MovingAverageDetector) -> None:
        """Buffers per (node_id, sensor_id) are independent."""
        for _ in range(10):
            det.on_reading(1, 1, 30.0)
            det.on_reading(1, 2, 30.0)
        alert_a = det.on_reading(1, 1, 100.0)
        alert_b = det.on_reading(1, 2, 100.0)
        assert alert_a is not None
        assert alert_b is not None
