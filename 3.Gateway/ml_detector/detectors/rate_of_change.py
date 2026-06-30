"""M02 — Rate of Change Detector.

Computes the rate of change (slope in units/hour) over a sliding window
using simple linear regression. Flags when abs(slope) > max_rate.

Online — no training required.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from .base import AlertData, BaseDetector

from collections import deque
from typing import Any


class RateOfChangeDetector(BaseDetector):
    """Detect rapid value changes via linear regression slope.

    Config:
        window_minutes: int — lookback window in minutes (default 60)
        max_rate: float — max acceptable change per hour (default 5.0)
        min_samples: int — minimum points for regression (default 5)
    """

    name: str = "rate_of_change"
    rule_id: str = "M02"
    severity: str = "WARNING"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        cfg = self._config
        self._window_s = (cfg.get("window_minutes", 60)) * 60.0
        self._max_rate = cfg.get("max_rate", 5.0)  # units/h
        self._min_samples = cfg.get("min_samples", 5)

        # key=(node,sensor) → deque[(timestamp, value)]
        self._buffers: dict[tuple[int, int], deque[tuple[float, float]]] = {}

    def reconfigure(self, params: dict[str, Any]) -> None:
        """Apply new config at runtime."""
        super().reconfigure(params)
        if "window_minutes" in params:
            self._window_s = float(params["window_minutes"]) * 60.0
        if "max_rate" in params:
            self._max_rate = float(params["max_rate"])
        if "min_samples" in params:
            self._min_samples = int(params["min_samples"])

    def on_reading(self, node_id: int, sensor_id: int, value: float,
                   timestamp: float | None = None) -> AlertData | None:
        import time
        ts = timestamp if timestamp is not None else time.time()
        key = (node_id, sensor_id)
        buf = self._buffers.get(key)
        if buf is None:
            buf = deque()
            self._buffers[key] = buf

        # Prune old entries
        cutoff = ts - self._window_s
        while buf and buf[0][0] < cutoff:
            buf.popleft()

        buf.append((ts, value))
        self._check_buffer_size(key, len(buf))

        if len(buf) < self._min_samples:
            return None

        # Linear regression on normalized timestamps (subtract mean_t)
        # to avoid catastrophic cancellation with large unix timestamps.
        n = len(buf)
        mean_t = sum(t for t, _ in buf) / n
        mean_v = sum(v for _, v in buf) / n

        num = denom = 0.0
        for t, v in buf:
            dt = t - mean_t
            num += dt * (v - mean_v)
            denom += dt * dt

        if abs(denom) < 1e-12:
            return None

        slope = num / denom          # units/second
        slope_per_h = slope * 3600.0                  # units/hour

        if abs(slope_per_h) < self._max_rate:
            return None

        if not self._can_alert(key):
            return None

        direction = "rising" if slope > 0 else "falling"
        return AlertData(
            rule_id=self.rule_id,
            severity=self.severity,
            message=(
                f"M02 Node {node_id} sensor {sensor_id}: "
                f"{direction} {abs(slope_per_h):.1f} units/h "
                f"(threshold={self._max_rate}, window={n} pts)"
            ),
            node_id=node_id,
            sensor_id=sensor_id,
            value=value,
            score=round(abs(slope_per_h), 2),
            extra={"slope_per_h": round(slope_per_h, 3), "n_points": n},
        )
