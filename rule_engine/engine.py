"""Rule Engine — evaluates threshold/rate/stuck rules against sensor readings.

Integrates with EventBus: subscribes to ``reading_recorded``, emits
``alert_triggered``. Rules are loaded from ``config/rules.yaml``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

import yaml

from mcp_server.event_bus import EventBus

if True:  # TYPE_CHECKING workaround
    from recorder.store import ReadingStore

logger = logging.getLogger(__name__)

# ── Alert deduplication ───────────────────────────────────────────────────
# Same rule + device + sensor won't re-fire within this window
_ALERT_COOLDOWN_S = 300  # 5 minutes


# ── Rule types ──────────────────────────────────────────────────────────────

class Rule:
    """A single rule loaded from YAML config."""

    __slots__ = (
        "id", "name", "type", "sensor_type", "operator", "value",
        "severity", "message", "window_minutes", "hours",
    )

    def __init__(self, **data: Any) -> None:
        self.id: str = data["id"]
        self.name: str = data.get("name", "")
        self.type: str = data.get("type", "threshold")
        self.sensor_type: str = data.get("sensor_type", "*")
        self.operator: str | None = data.get("operator")
        self.value: float | None = data.get("value")
        self.severity: str = data.get("severity", "WARNING")
        self.message: str = data.get("message", "")
        self.window_minutes: int = data.get("window_minutes", 60)
        self.hours: int = data.get("hours", 6)

    def applies_to(self, sensor_id: str) -> bool:
        """Check if this rule applies to a given sensor type."""
        if self.sensor_type == "*":
            return True
        return self.sensor_type in sensor_id.lower()

    def format_message(self, device_id: str, sensor_id: str, value: float,
                       rate: float | None = None) -> str:
        """Format alert message with context values."""
        return self.message.format(
            device_id=device_id,
            sensor_id=sensor_id,
            value=value,
            rate=rate or 0,
        )


# ── Rule Engine ─────────────────────────────────────────────────────────────

class RuleEngine:
    """Evaluates rules against sensor readings via EventBus.

    Usage::

        engine = RuleEngine(bus, store, "config/rules.yaml")
        # Automatic: subscribes to bus ``reading_recorded``
        # Periodic: call await engine.check_missing() every 5 minutes
    """

    def __init__(
        self,
        bus: EventBus,
        store: ReadingStore,
        rules_path: str = "config/rules.yaml",
    ) -> None:
        self._bus = bus
        self._store = store
        self._rules: list[Rule] = []
        self._cooldowns: dict[str, float] = {}  # "rule:device:sensor" → timestamp

        # Load rules
        path = Path(rules_path)
        if path.exists():
            self._load(path)
            logger.info("rule engine loaded %d rule(s)", len(self._rules))
        else:
            logger.warning("rules file not found: %s", rules_path)

        # Subscribe to events
        bus.on("reading_recorded", self._on_reading)

    def _load(self, path: Path) -> None:
        """Load rules from YAML file."""
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        for item in data.get("rules", []):
            self._rules.append(Rule(**item))

    # ── event handler ─────────────────────────────────────────────────────

    async def _on_reading(
        self,
        device_id: str | None = None,
        sensor_id: str | None = None,
        value: float | None = None,
        unit: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Called when a sensor reading is recorded."""
        if value is None or sensor_id is None:
            return

        for rule in self._rules:
            if not rule.applies_to(sensor_id):
                continue

            triggered = await self._evaluate(rule, device_id or "", sensor_id, value)

    async def _evaluate(
        self, rule: Rule, device_id: str, sensor_id: str, value: float
    ) -> bool:
        """Evaluate a single rule. Returns True if triggered."""
        try:
            if rule.type == "threshold":
                triggered = self._check_threshold(rule, value)
            elif rule.type == "rate":
                triggered = await self._check_rate(rule, device_id, sensor_id, value)
            elif rule.type == "stuck":
                triggered = await self._check_stuck(rule, device_id, sensor_id)
            else:
                return False

            if triggered:
                await self._fire(rule, device_id, sensor_id, value)
            return triggered
        except Exception:
            logger.exception("rule %s failed", rule.id)
            return False

    # ── rule checks ───────────────────────────────────────────────────────

    @staticmethod
    def _check_threshold(rule: Rule, value: float) -> bool:
        """Threshold comparison: value > 40, value < 20, etc."""
        if rule.operator == ">":
            return value > (rule.value or 0)
        elif rule.operator == "<":
            return value < (rule.value or 0)
        elif rule.operator == ">=":
            return value >= (rule.value or 0)
        elif rule.operator == "<=":
            return value <= (rule.value or 0)
        elif rule.operator == "==":
            return value == (rule.value or 0)
        return False

    async def _check_rate(
        self, rule: Rule, device_id: str, sensor_id: str, value: float
    ) -> bool:
        """Check rate of change over the lookback window."""
        hours = max(rule.window_minutes / 60, 0.1)
        history = await self._store.get_history(
            device_id=device_id,
            sensor_id=sensor_id,
            start=time.time() - hours * 3600,
            limit=50,
        )
        if len(history) < 2:
            return False

        oldest = history[-1]  # oldest is last in DESC order
        newest = history[0]
        dt = newest.timestamp - oldest.timestamp
        if dt <= 0:
            return False

        rate_per_hour = (newest.value - oldest.value) / (dt / 3600)
        max_delta = rule.value or 0
        return abs(rate_per_hour) > max_delta

    async def _check_stuck(
        self, rule: Rule, device_id: str, sensor_id: str
    ) -> bool:
        """Check if sensor value hasn't changed for N hours."""
        history = await self._store.get_history(
            device_id=device_id,
            sensor_id=sensor_id,
            start=time.time() - (rule.hours or 6) * 3600,
            limit=100,
        )
        if len(history) < 5:
            return False

        values = {round(r.value, 2) for r in history}
        return len(values) <= 1  # only one unique value = stuck

    # ── alert emission ────────────────────────────────────────────────────

    async def _fire(self, rule: Rule, device_id: str, sensor_id: str, value: float) -> None:
        """Emit alert if not in cooldown."""
        cooldown_key = f"{rule.id}:{device_id}:{sensor_id}"
        now = time.time()

        last = self._cooldowns.get(cooldown_key, 0)
        if now - last < _ALERT_COOLDOWN_S:
            return

        self._cooldowns[cooldown_key] = now
        message = rule.format_message(device_id, sensor_id, value)

        logger.info(
            "alert %s [%s] %s = %s: %s",
            rule.id, rule.severity, device_id, value, message,
        )

        await self._bus.emit(
            "alert_triggered",
            rule_id=rule.id,
            severity=rule.severity,
            message=message,
            device_id=device_id,
            sensor_id=sensor_id,
            value=value,
        )

    # ── missing data check (timer-based) ──────────────────────────────────

    async def check_missing(self, hours: float = 1.0) -> None:
        """Check for devices that haven't reported in N hours.

        Should be called periodically by a timer (not event-driven).
        """
        cutoff = time.time() - hours * 3600
        try:
            latest = await self._store.get_all_latest()
        except Exception:
            logger.warning("check_missing: store query failed", exc_info=True)
            return

        for reading in latest:
            if reading.timestamp < cutoff:
                rule_id = "R09"
                msg = f"{reading.device_id}: no data for {hours:.0f}h"
                logger.info("alert R09 [WARNING] %s", msg)
                await self._bus.emit(
                    "alert_triggered",
                    rule_id=rule_id,
                    severity="WARNING",
                    message=msg,
                    device_id=reading.device_id,
                    sensor_id=reading.sensor_id,
                    value=reading.value,
                )

    # ── utilities ─────────────────────────────────────────────────────────

    @property
    def rules(self) -> list[Rule]:
        """Loaded rules (read-only)."""
        return list(self._rules)

    def reload(self, rules_path: str = "config/rules.yaml") -> None:
        """Reload rules from YAML (useful for hot-reload)."""
        self._rules.clear()
        path = Path(rules_path)
        if path.exists():
            self._load(path)
            logger.info("reloaded %d rule(s)", len(self._rules))
