"""
SQLite storage for sensor readings, alerts, devices, and actuation logs.
WAL mode enabled for concurrent read/write.
"""

import os
import time
import json
import aiosqlite


DEFAULT_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "agrimesh.db")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS readings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id     INTEGER NOT NULL,
    sensor_id   TEXT    NOT NULL,
    value       REAL    NOT NULL,
    unit        TEXT    NOT NULL,
    timestamp   INTEGER NOT NULL,
    quality     INTEGER DEFAULT 100
);
CREATE INDEX IF NOT EXISTS idx_readings_lookup
    ON readings (node_id, sensor_id, timestamp DESC);

CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id     INTEGER NOT NULL,
    sensor_id   TEXT,
    rule_id     TEXT    NOT NULL,
    value       REAL,
    severity    TEXT    NOT NULL,
    message     TEXT    NOT NULL,
    timestamp   INTEGER NOT NULL,
    ack_at      INTEGER,
    ack_by      TEXT
);

CREATE TABLE IF NOT EXISTS devices (
    node_id      INTEGER PRIMARY KEY,
    type         TEXT    NOT NULL,
    name         TEXT    NOT NULL,
    location     TEXT,
    sensors      TEXT,
    config       TEXT,
    status       TEXT DEFAULT 'unknown',
    last_seen    INTEGER,
    battery_pct  INTEGER
);

CREATE TABLE IF NOT EXISTS actuation_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id      INTEGER NOT NULL,
    actuator_id  TEXT    NOT NULL,
    command      TEXT    NOT NULL,
    params       TEXT,
    duration_sec INTEGER,
    triggered_by TEXT    NOT NULL,
    confirmed_by TEXT,
    status       TEXT    NOT NULL,
    timestamp    INTEGER NOT NULL
);
"""


class ReadingsStore:
    """Async SQLite store for all sensor and system data."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db = None

    async def open(self):
        self.db = await aiosqlite.connect(self.db_path)
        self.db.row_factory = aiosqlite.Row
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.executescript(SCHEMA_SQL)

    async def close(self):
        if self.db:
            await self.db.close()

    # ── Readings ──────────────────────────────────────────────

    async def insert_reading(self, node_id: int, sensor_id: str, value: float,
                              unit: str, timestamp: int, quality: int = 100):
        await self.db.execute(
            "INSERT INTO readings (node_id, sensor_id, value, unit, timestamp, quality) VALUES (?, ?, ?, ?, ?, ?)",
            (node_id, sensor_id, value, unit, timestamp, quality),
        )
        await self.db.commit()

    async def get_readings(self, node_id: int, sensor_id: str,
                            hours: int = 24, limit: int = 1000):
        cutoff = int(time.time()) - hours * 3600
        cursor = await self.db.execute(
            "SELECT * FROM readings WHERE node_id=? AND sensor_id=? AND timestamp>=?"
            " ORDER BY timestamp DESC LIMIT ?",
            (node_id, sensor_id, cutoff, limit),
        )
        return await cursor.fetchall()

    async def get_latest_reading(self, node_id: int, sensor_id: str):
        cursor = await self.db.execute(
            "SELECT * FROM readings WHERE node_id=? AND sensor_id=? ORDER BY timestamp DESC LIMIT 1",
            (node_id, sensor_id),
        )
        return await cursor.fetchone()

    async def get_all_latest_readings(self):
        """Get the latest reading for every (node_id, sensor_id) pair."""
        cursor = await self.db.execute(
            "SELECT r.* FROM readings r "
            "INNER JOIN (SELECT node_id, sensor_id, MAX(timestamp) as maxts FROM readings GROUP BY node_id, sensor_id) l "
            "ON r.node_id = l.node_id AND r.sensor_id = l.sensor_id AND r.timestamp = l.maxts"
        )
        return await cursor.fetchall()

    # ── Alerts ────────────────────────────────────────────────

    async def insert_alert(self, node_id: int, rule_id: str, severity: str,
                            message: str, timestamp: int,
                            sensor_id: str = None, value: float = None):
        await self.db.execute(
            "INSERT INTO alerts (node_id, sensor_id, rule_id, value, severity, message, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (node_id, sensor_id, rule_id, value, severity, message, timestamp),
        )
        await self.db.commit()

    async def get_alerts(self, hours: int = 24, severity: str = None, limit: int = 100):
        cutoff = int(time.time()) - hours * 3600
        if severity:
            cursor = await self.db.execute(
                "SELECT * FROM alerts WHERE timestamp>=? AND severity=? ORDER BY timestamp DESC LIMIT ?",
                (cutoff, severity, limit),
            )
        else:
            cursor = await self.db.execute(
                "SELECT * FROM alerts WHERE timestamp>=? ORDER BY timestamp DESC LIMIT ?",
                (cutoff, limit),
            )
        return await cursor.fetchall()

    async def acknowledge_alert(self, alert_id: int, ack_by: str):
        await self.db.execute(
            "UPDATE alerts SET ack_at=?, ack_by=? WHERE id=?",
            (int(time.time()), ack_by, alert_id),
        )
        await self.db.commit()

    # ── Devices ───────────────────────────────────────────────

    async def register_device(self, node_id: int, dtype: str, name: str,
                               location: str = None, sensors: str = None,
                               config: str = None):
        await self.db.execute(
            "INSERT OR REPLACE INTO devices (node_id, type, name, location, sensors, config) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (node_id, dtype, name, location, sensors, config),
        )
        await self.db.commit()

    async def update_device_status(self, node_id: int, status: str,
                                    battery_pct: int = None):
        if battery_pct is not None:
            await self.db.execute(
                "UPDATE devices SET status=?, last_seen=?, battery_pct=? WHERE node_id=?",
                (status, int(time.time()), battery_pct, node_id),
            )
        else:
            await self.db.execute(
                "UPDATE devices SET status=?, last_seen=? WHERE node_id=?",
                (status, int(time.time()), node_id),
            )
        await self.db.commit()

    async def list_devices(self):
        cursor = await self.db.execute("SELECT * FROM devices ORDER BY node_id")
        return await cursor.fetchall()

    async def get_device(self, node_id: int):
        cursor = await self.db.execute("SELECT * FROM devices WHERE node_id=?", (node_id,))
        return await cursor.fetchone()

    # ── Actuation Log ─────────────────────────────────────────

    async def log_actuation(self, node_id: int, actuator_id: str, command: str,
                             triggered_by: str, status: str, timestamp: int,
                             params: str = None, duration_sec: int = None,
                             confirmed_by: str = None):
        await self.db.execute(
            "INSERT INTO actuation_log (node_id, actuator_id, command, params, duration_sec, "
            "triggered_by, confirmed_by, status, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (node_id, actuator_id, command, params, duration_sec,
             triggered_by, confirmed_by, status, timestamp),
        )
        await self.db.commit()

    # ── Retention ─────────────────────────────────────────────

    async def run_retention(self, full_resolution_days: int = 30):
        """Downsample/purge readings and alerts older than full_resolution_days."""
        cutoff = int(time.time()) - full_resolution_days * 86400
        await self.db.execute("DELETE FROM readings WHERE timestamp < ?", (cutoff,))
        await self.db.execute("DELETE FROM alerts WHERE timestamp < ?", (cutoff,))
        await self.db.commit()
