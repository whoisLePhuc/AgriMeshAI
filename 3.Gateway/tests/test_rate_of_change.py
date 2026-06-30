"""Tests for RateOfChangeDetector — M02 linear regression slope."""

from __future__ import annotations

import math
from collections import deque

import pytest

from ml_detector.detectors.rate_of_change import RateOfChangeDetector


class TestRateOfChangeDetector:
    """Rate of change (slope) detection."""

    @pytest.fixture
    def det(self) -> RateOfChangeDetector:
        return RateOfChangeDetector({"window_minutes": 60, "max_rate": 5.0, "min_samples": 5})

    def _feed_constant(self, det: RateOfChangeDetector, n: int = 10) -> None:
        """Feed constant-value readings with realistic timestamps."""
        base_ts = 1_000_000.0
        for i in range(n):
            det.on_reading(1, 1, 30.0, timestamp=base_ts + i * 60.0)

    def test_stable_slope_no_alert(self, det: RateOfChangeDetector) -> None:
        """Constant readings produce no alert."""
        self._feed_constant(det, 10)
        assert det.on_reading(1, 1, 30.0, timestamp=1_000_600.0) is None

    def test_rapid_rise_exceeds_max_rate(self, det: RateOfChangeDetector) -> None:
        """Rapid temperature rise exceeding 5°C/h triggers alert."""
        base_ts = 1_000_000.0
        # Stable period
        for i in range(10):
            det.on_reading(1, 1, 25.0, timestamp=base_ts + i * 60.0)
        # Rapid rise: +10°C over 30 min = 20°C/h
        alert = det.on_reading(1, 1, 35.0, timestamp=base_ts + 1800.0)
        assert alert is not None
        assert alert.rule_id == "M02"
        assert alert.node_id == 1
        assert alert.sensor_id == 1

    def test_rapid_fall_exceeds_max_rate(self, det: RateOfChangeDetector) -> None:
        """Rapid temperature fall exceeding 5°C/h triggers alert."""
        base_ts = 2_000_000.0
        for i in range(10):
            det.on_reading(1, 1, 35.0, timestamp=base_ts + i * 60.0)
        # Rapid fall: -10°C over 30 min = -20°C/h
        alert = det.on_reading(1, 1, 25.0, timestamp=base_ts + 1800.0)
        assert alert is not None

    def test_window_pruning_removes_old(self, det: RateOfChangeDetector) -> None:
        """Old readings beyond window_minutes are pruned."""
        base_ts = 3_000_000.0
        for i in range(10):
            det.on_reading(1, 1, 30.0, timestamp=base_ts + i * 60.0)
        # After pruning, the buffer should be trimmed
        det.on_reading(1, 1, 30.0, timestamp=base_ts + 7200.0)
        # The internal buffer should have been pruned
        buf = det._buffers.get((1, 1))
        assert buf is not None
        for ts, _ in buf:
            assert ts >= base_ts + 7200.0 - 3600.0

    def test_empty_buffer_returns_none(self, det: RateOfChangeDetector) -> None:
        """Before min_samples, detector returns None."""
        result = det.on_reading(1, 1, 30.0)
        assert result is None

    def test_configurable_max_rate(self) -> None:
        """Tighter max_rate triggers alerts on smaller slopes."""
        sensitive = RateOfChangeDetector({"window_minutes": 60, "max_rate": 1.0, "min_samples": 5})
        base_ts = 4_000_000.0
        for i in range(10):
            sensitive.on_reading(1, 1, 25.0, timestamp=base_ts + i * 60.0)
        # Small rise: +3°C over 30 min = 6°C/h — exceeds 1°C/h
        alert = sensitive.on_reading(1, 1, 28.0, timestamp=base_ts + 1800.0)
        assert alert is not None
