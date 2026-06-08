"""
Fleet-level MCP tools: cross-device queries using the recorder.
"""

from mcp.types import Tool


def get_fleet_tools() -> list[Tool]:
    return [
        Tool(
            name="fleet.list_devices",
            description="List all registered devices with current status and battery",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="fleet.get_all_readings",
            description="Get the latest reading from every sensor across all devices",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="fleet.get_history",
            description="Get time-series history for a specific sensor on a device",
            inputSchema={
                "type": "object",
                "properties": {
                    "node_id": {"type": "integer", "description": "Device node ID"},
                    "sensor_id": {"type": "string", "description": "Sensor identifier (e.g. temperature, humidity, moisture)"},
                    "hours": {"type": "integer", "description": "Hours of history to retrieve", "default": 24},
                },
                "required": ["node_id", "sensor_id"],
            },
        ),
        Tool(
            name="fleet.get_alerts",
            description="Get recent alerts, optionally filtered by severity level",
            inputSchema={
                "type": "object",
                "properties": {
                    "hours": {"type": "integer", "description": "Hours of history to search", "default": 24},
                    "severity": {"type": "string", "description": "Filter by severity: INFO, WARNING, or CRITICAL"},
                },
            },
        ),
    ]


async def handle_fleet_tool(name: str, args: dict, recorder) -> dict | None:
    """Handle fleet tool calls. Returns None if the tool name is not a fleet tool."""
    store = recorder.store

    if name == "fleet.list_devices":
        devices = await store.list_devices()
        result = []
        for d in devices:
            result.append({
                "node_id": d["node_id"],
                "name": d["name"],
                "type": d["type"],
                "status": d["status"],
                "battery_pct": d["battery_pct"],
                "last_seen": d["last_seen"],
                "location": d["location"],
            })
        return {"devices": result}

    if name == "fleet.get_all_readings":
        readings = await store.get_all_latest_readings()
        result = []
        for r in readings:
            result.append({
                "node_id": r["node_id"],
                "sensor_id": r["sensor_id"],
                "value": r["value"],
                "unit": r["unit"],
                "timestamp": r["timestamp"],
                "quality": r["quality"],
            })
        return {"readings": result}

    if name == "fleet.get_history":
        readings = await store.get_readings(
            args["node_id"],
            args["sensor_id"],
            hours=args.get("hours", 24),
        )
        result = []
        for r in readings:
            result.append({
                "timestamp": r["timestamp"],
                "value": r["value"],
                "unit": r["unit"],
                "quality": r["quality"],
            })
        return {"readings": result}

    if name == "fleet.get_alerts":
        alerts = await store.get_alerts(
            hours=args.get("hours", 24),
            severity=args.get("severity"),
        )
        result = []
        for a in alerts:
            result.append({
                "id": a["id"],
                "node_id": a["node_id"],
                "rule_id": a["rule_id"],
                "severity": a["severity"],
                "message": a["message"],
                "timestamp": a["timestamp"],
                "ack_at": a["ack_at"],
            })
        return {"alerts": result}

    return None
