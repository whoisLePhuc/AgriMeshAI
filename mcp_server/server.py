"""
AgriMeshAI MCP Server — FastMCP-based server with stdio and HTTP transports.
Integrates with recorder, discovery, aggregator via FastMCP lifespan.
"""

import os
import json
import hashlib
from contextlib import asynccontextmanager
from mcp.server.fastmcp import FastMCP, Context
from recorder import Recorder, ReadingsStore
from mcp_server.discovery import discover_devices
from mcp_server.aggregator import Aggregator
from mcp_server.tools.fleet import handle_fleet_tool

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILES_DIR = os.path.join(ROOT, "devices")


@asynccontextmanager
async def lifespan(server: FastMCP):
    """Initialize recorder + aggregator on startup, clean up on shutdown."""
    store = ReadingsStore(os.path.join(ROOT, "data", "agrimesh.db"))
    recorder = Recorder(store)
    await recorder.start()

    discovered = discover_devices(PROFILES_DIR)
    aggregator = Aggregator()
    aggregator.register_all(discovered)

    print(f"  Recorder: ready", file=__import__('sys').stderr)
    print(f"  Devices: {len(discovered)} discovered ({len(aggregator.get_tools())} tools)", file=__import__('sys').stderr)

    try:
        yield {"recorder": recorder, "aggregator": aggregator}
    finally:
        await recorder.stop()


server = FastMCP("agrimesh-mcp", lifespan=lifespan)


# ── Fleet Tools ──────────────────────────────────────────────────────

@server.tool(name="fleet.list_devices", description="List all registered devices with current status and battery")
async def fleet_list_devices(ctx: Context) -> str:
    """List all registered devices."""
    recorder = ctx.request_context.lifespan_context["recorder"]
    result = await handle_fleet_tool("fleet.list_devices", {}, recorder)
    return json.dumps(result, indent=2, default=str)


@server.tool(name="fleet.get_all_readings", description="Get the latest reading from every sensor across all devices")
async def fleet_get_all_readings(ctx: Context) -> str:
    """Get latest reading from every sensor."""
    recorder = ctx.request_context.lifespan_context["recorder"]
    result = await handle_fleet_tool("fleet.get_all_readings", {}, recorder)
    return json.dumps(result, indent=2, default=str)


@server.tool(name="fleet.get_history", description="Get time-series history for a specific sensor on a device")
async def fleet_get_history(ctx: Context, node_id: int, sensor_id: str, hours: int = 24) -> str:
    """Get time-series history for a sensor.

    Args:
        node_id: Device node ID
        sensor_id: Sensor identifier (e.g. temperature, humidity, moisture)
        hours: Hours of history to retrieve (default 24)
    """
    recorder = ctx.request_context.lifespan_context["recorder"]
    result = await handle_fleet_tool("fleet.get_history", {
        "node_id": node_id, "sensor_id": sensor_id, "hours": hours
    }, recorder)
    return json.dumps(result, indent=2, default=str)


@server.tool(name="fleet.get_alerts", description="Get recent alerts, optionally filtered by severity level")
async def fleet_get_alerts(ctx: Context, hours: int = 24, severity: str | None = None) -> str:
    """Get recent alerts.

    Args:
        hours: Hours of history to search (default 24)
        severity: Filter by severity: INFO, WARNING, or CRITICAL
    """
    recorder = ctx.request_context.lifespan_context["recorder"]
    args: dict[str, str] = {"hours": str(hours)}
    if severity:
        args["severity"] = severity
    result = await handle_fleet_tool("fleet.get_alerts", args, recorder)
    return json.dumps(result, indent=2, default=str)


# ── Device Tools ─────────────────────────────────────────────────────

@server.tool(description="Call a device tool by device name and tool name")
async def call_device(ctx: Context, device: str, tool: str) -> str:
    """Execute a tool on a specific device.

    Args:
        device: Device name (e.g. farm_sensor, mock_sensor)
        tool: Tool name (e.g. get_temperature, get_humidity)
    """
    aggregator = ctx.request_context.lifespan_context["aggregator"]
    recorder = ctx.request_context.lifespan_context["recorder"]

    full_name = f"{device}.{tool}"
    result = await aggregator.call_tool(full_name)

    if not result.success:
        return json.dumps({"error": result.error})

    # Auto-record numeric readings
    try:
        val = float(result.data.strip())
        nid = int(hashlib.md5(device.encode()).hexdigest()[:8], 16) % 10000
        await recorder.record_reading(node_id=nid, sensor_id=tool, value=val, unit="")
    except ValueError:
        pass

    return result.data


# ── MCP Prompts ──────────────────────────────────────────────────────

@server.prompt()
async def device_query_guide() -> str:
    """Guide for querying devices and sensor data"""
    return (
        "When the user asks about devices or sensors:\n"
        "- Use fleet_list_devices to list all devices\n"
        "- Use fleet_get_all_readings for latest sensor data\n"
        "- Use fleet_get_history for time-series data\n"
        "- Use fleet_get_alerts for warnings and anomalies\n"
        "- Use call_device to read a specific sensor on a specific device"
    )


@server.prompt()
async def telemetry_guide() -> str:
    """Guide for checking sensor data"""
    return (
        "To check sensor data:\n"
        "1. First call fleet_list_devices to see available devices\n"
        "2. Then call fleet_get_all_readings for latest readings\n"
        "3. Or call call_device with device='farm_sensor' and tool='get_temperature'\n"
        "4. For history, use fleet_get_history with node_id and sensor_id"
    )
