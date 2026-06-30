"""Shared fixtures for detector tests."""

from __future__ import annotations

import pytest

from event_bus import EventBus
from ml_detector.detectors.moving_average import MovingAverageDetector
from ml_detector.detectors.rate_of_change import RateOfChangeDetector
from ml_detector.detectors.stuck_sensor import StuckSensorDetector


# ── EventBus ────────────────────────────────────────────────────────

@pytest.fixture
def event_bus() -> EventBus:
    """Fresh EventBus instance per test."""
    return EventBus()


# ── Detector instances ──────────────────────────────────────────────

@pytest.fixture
def moving_avg_detector() -> MovingAverageDetector:
    """MovingAverageDetector with default config (window=200, sigma=3.0)."""
    return MovingAverageDetector()


@pytest.fixture
def rate_of_change_detector() -> RateOfChangeDetector:
    """RateOfChangeDetector with default config (window=60min, max_rate=5.0)."""
    return RateOfChangeDetector()


@pytest.fixture
def stuck_sensor_detector() -> StuckSensorDetector:
    """StuckSensorDetector with default config (window=6h, threshold_var=0.005)."""
    return StuckSensorDetector()


# ── Mock reading data ───────────────────────────────────────────────

@pytest.fixture
def mock_temperature_readings() -> list[float]:
    """A stream of normal temperature readings around 30°C."""
    return [30.0 + (i % 10) * 0.5 for i in range(100)]


@pytest.fixture
def mock_humidity_readings() -> list[float]:
    """A stream of normal humidity readings around 65%."""
    return [65.0 + (i % 20) * 0.2 for i in range(100)]


@pytest.fixture
def mock_constant_readings() -> list[float]:
    """Constant readings for stuck sensor tests."""
    return [25.0] * 50
