"""M01 — Moving Average ± 3σ Adaptive Baseline.

Maintains a sliding window (configurable) per (node_id, sensor_id).
Flags anomaly when the latest value deviates more than ``threshold_sigma``
standard deviations from the window mean.

Online — no training required.
"""

from __future__ import annotations

import math
import time
from collections import deque
from typing import Any

from .base import AlertData, BaseDetector


class MovingAverageDetector(BaseDetector):
    """Detect deviation from rolling average baseline.

    Config:
        window_size: int  — number of readings to keep per key (default 200)
        threshold_sigma: float — how many σ before flagging (default 3.0)
        min_samples: int — minimum readings before detection starts (default 10)
    """

    name: str = "moving_average"
    rule_id: str = "M01"
    severity: str = "WARNING"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        cfg = self._config
        self._window_size = cfg.get("window_size", 200)
        self._threshold_sigma = cfg.get("threshold_sigma", 3.0)
        self._min_samples = cfg.get("min_samples", 10)

        # buffers: key=(node_id, sensor_id) → deque[float] (most recent first)
        self._buffers: dict[tuple[int, int], deque[float]] = {}

    def on_reading(self, node_id: int, sensor_id: int, value: float,
                   timestamp: float | None = None) -> AlertData | None:
        key = (node_id, sensor_id)
        buf = self._buffers.get(key)
        if buf is None:
            buf = deque(maxlen=self._window_size)
            self._buffers[key] = buf

        buf.appendleft(value)

        if len(buf) < self._min_samples:
            return None

        mean = sum(buf) / len(buf)
        variance = sum((x - mean) ** 2 for x in buf) / len(buf)
        stddev = math.sqrt(variance) if variance > 0 else 0.0

        if stddev == 0.0:
            return None  # not enough variance yet

        sigma = abs(value - mean) / stddev
        if sigma < self._threshold_sigma:
            return None

        if not self._can_alert(key):
            return None

        return AlertData(
            rule_id=self.rule_id,
            severity=self.severity,
            message=(
                f"M01 Node {node_id} sensor {sensor_id}: "
                f"value {value:.2f} deviates {sigma:.1f}σ "
                f"from baseline {mean:.2f} (window={len(buf)})"
            ),
            node_id=node_id,
            sensor_id=sensor_id,
            value=value,
            baseline=mean,
            score=round(sigma, 2),
            extra={"stddev": round(stddev, 4), "window_size": len(buf)},
        )
