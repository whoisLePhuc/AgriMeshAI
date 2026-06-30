"""M03 — Stuck Sensor Detector.

Flags a sensor as potentially stuck/faulty when its reported value
has remained effectively constant for a prolonged window.

Online — no training required.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from .base import AlertData, BaseDetector


class StuckSensorDetector(BaseDetector):
    """Detect sensors that appear stuck (zero/very low variance).

    Config:
        window_hours: float — lookback window (default 6.0)
        threshold_var: float — max variance to consider stuck (default 0.005)
        min_samples: int — minimum points before checking (default 10)
    """

    name: str = "stuck_sensor"
    rule_id: str = "M03"
    severity: str = "WARNING"
    cooldown_s: float = 1800.0   # 30 min — no need to repeat often

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        cfg = self._config
        self._window_s = (cfg.get("window_hours", 6.0)) * 3600.0
        self._threshold_var = cfg.get("threshold_var", 0.005)
        self._min_samples = cfg.get("min_samples", 10)

        # key=(node,sensor) → deque[(timestamp, value)]
        self._buffers: dict[tuple[int, int], deque[tuple[float, float]]] = {}

        # track how long the sensor has been stuck (re-entrant check)
        self._stuck_since: dict[tuple[int, int], float] = {}

    def reconfigure(self, params: dict[str, Any]) -> None:
        """Apply new config at runtime."""
        super().reconfigure(params)
        if "window_hours" in params:
            self._window_s = float(params["window_hours"]) * 3600.0
        if "threshold_var" in params:
            self._threshold_var = float(params["threshold_var"])
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

        cutoff = ts - self._window_s
        while buf and buf[0][0] < cutoff:
            buf.popleft()

        buf.append((ts, value))
        self._check_buffer_size(key, len(buf))

        if len(buf) < self._min_samples:
            self._stuck_since.pop(key, None)
            return None

        mean = sum(v for _, v in buf) / len(buf)
        variance = sum((v - mean) ** 2 for _, v in buf) / len(buf)

        if variance > self._threshold_var:
            self._stuck_since.pop(key, None)
            return None  # not stuck

        # Sensor looks stuck — track duration
        stuck_start = self._stuck_since.get(key)
        if stuck_start is None:
            self._stuck_since[key] = ts
            return None  # first detection, don't alert yet

        stuck_hours = (ts - stuck_start) / 3600.0
        if stuck_hours < 1.99:
            return None  # wait at least 2h before alerting

        if not self._can_alert(key):
            return None

        return AlertData(
            rule_id=self.rule_id,
            severity=self.severity,
            message=(
                f"M03 Node {node_id} sensor {sensor_id}: "
                f"seems STUCK for {stuck_hours:.1f}h "
                f"(variance={variance:.4f}, mean={mean:.2f}, {len(buf)} pts)"
            ),
            node_id=node_id,
            sensor_id=sensor_id,
            value=value,
            baseline=mean,
            score=round(variance, 5),
            extra={"stuck_hours": round(stuck_hours, 2), "variance": round(variance, 6)},
        )
