"""Base detector interface — all ML detectors implement this."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AlertData:
    """Anomaly alert emitted by a detector, consumed by Notifier via EventBus."""

    rule_id: str        # "M01" — matches prefix in RuleEngine
    severity: str       # "WARNING" | "CRITICAL"
    message: str        # human-readable
    node_id: int | None = None
    sensor_id: int | None = None
    value: float | None = None
    baseline: float | None = None   # expected value
    score: float | None = None      # anomaly severity (σ, slope, etc.)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class DetectorHealth:
    """Health snapshot for a single detector instance."""

    name: str
    status: str                     # "running" | "error" | "disabled"
    alert_count: int
    total_processed: int
    last_alert_time: float | None = None
    buffer_size_bytes: int = 0


class BaseDetector(ABC):
    """Abstract base for a single ML detection algorithm.

    Each detector implements ``on_reading`` for event-driven checks
    and optionally ``periodic_task`` for batch/time-driven checks.

    State is maintained internally (buffers, baselines) and is
    **per** (node_id, sensor_id) unless otherwise documented.
    """

    # ── metadata set by subclass ──────────────────────────────────
    name: str = "base"
    rule_id: str = "M00"
    severity: str = "WARNING"

    # ── cooldown to avoid alert storms ────────────────────────────
    cooldown_s: float = 300.0   # 5 min default

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}
        self._last_alert: dict[tuple[int, int], float] = {}  # (node,sensor) → timestamp
        # Health counters
        self._alert_count: int = 0
        self._total_processed: int = 0
        self._last_alert_time: float | None = None

    # ── alert throttling ──────────────────────────────────────────

    def _can_alert(self, key: tuple[int, int]) -> bool:
        import time
        now = time.monotonic()
        last = self._last_alert.get(key, 0.0)
        if now - last >= self.cooldown_s:
            self._last_alert[key] = now
            return True
        return False

    # ── runtime configuration ─────────────────────────────────────

    def reconfigure(self, params: dict[str, Any]) -> None:
        """Update detector parameters at runtime.

        Subclasses should override to handle parameter-specific updates.
        The default implementation stores params in ``self._config``.
        """
        self._config.update(params)
        # Subclasses should call super().reconfigure(params) then update
        # their specific instance variables in this method.

    # ── buffer management ─────────────────────────────────────────

    def _check_buffer_size(self, key: tuple[int, int], current_size: int,
                           max_expected: int = 1000) -> None:
        """Log a warning if a per-sensor buffer exceeds expected size."""
        if current_size > max_expected:
            logger = __import__("logging").getLogger(__name__)
            logger.warning(
                "%s buffer %s size %d exceeds %d (>10%% threshold)",
                self.name, key, current_size, max_expected,
            )

    # ── health reporting ──────────────────────────────────────────

    def health(self) -> DetectorHealth:
        """Return a health snapshot of this detector."""
        return DetectorHealth(
            name=self.name,
            status="running",
            alert_count=self._alert_count,
            total_processed=self._total_processed,
            last_alert_time=self._last_alert_time,
        )

    # ── subclass API ──────────────────────────────────────────────

    @abstractmethod
    def on_reading(self, node_id: int, sensor_id: int, value: float,
                   timestamp: float | None = None) -> AlertData | None:
        """Process a single reading. Return AlertData if anomalous, else None."""
        ...
