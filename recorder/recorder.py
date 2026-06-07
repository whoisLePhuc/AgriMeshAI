"""
Recorder pipeline: receives sensor readings, alerts, actuation data
and writes them to SQLite via ReadingsStore.
"""

import json
import time
from recorder.store import ReadingsStore


class Recorder:
    """High-level recorder that components (MCP, LoRa, ML) call to persist data."""

    def __init__(self, store: ReadingsStore):
        self.store = store
        self._running = False

    async def start(self):
        await self.store.open()
        self._running = True
        return self

    async def stop(self):
        await self.store.close()
        self._running = False

    # ── Sensor data ───────────────────────────────────────────

    async def record_reading(self, node_id: int, sensor_id: str, value: float,
                              unit: str, timestamp: int = None, quality: int = 100):
        ts = timestamp or int(time.time())
        await self.store.insert_reading(node_id, sensor_id, value, unit, ts, quality)

    # ── Alerts ────────────────────────────────────────────────

    async def record_alert(self, node_id: int, rule_id: str, severity: str,
                            message: str, sensor_id: str = None,
                            value: float = None, timestamp: int = None):
        ts = timestamp or int(time.time())
        await self.store.insert_alert(node_id, rule_id, severity, message, ts, sensor_id, value)

    # ── Device management ─────────────────────────────────────

    async def register_device(self, node_id: int, dtype: str, name: str,
                               location: str = None, sensors: list = None,
                               config: dict = None):
        sensors_json = json.dumps(sensors) if sensors else None
        config_json = json.dumps(config) if config else None
        await self.store.register_device(node_id, dtype, name, location, sensors_json, config_json)

    async def update_device_health(self, node_id: int, status: str,
                                    battery_pct: int = None):
        await self.store.update_device_status(node_id, status, battery_pct)

    # ── Actuation ─────────────────────────────────────────────

    async def record_actuation(self, node_id: int, actuator_id: str, command: str,
                                triggered_by: str, status: str,
                                timestamp: int = None, params: dict = None,
                                duration_sec: int = None):
        ts = timestamp or int(time.time())
        params_json = json.dumps(params) if params else None
        await self.store.log_actuation(
            node_id, actuator_id, command, triggered_by, status, ts,
            params_json, duration_sec,
        )

    # ── Maintenance ───────────────────────────────────────────

    async def run_retention(self, full_resolution_days: int = 30):
        await self.store.run_retention(full_resolution_days)
