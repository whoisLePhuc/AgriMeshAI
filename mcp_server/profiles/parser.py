"""
TOML profile parser — converts .toml files to DeviceModel instances.
"""

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib
from pathlib import Path
from mcp_server.devices.model import (
    DeviceModel, ConnectionConfig, ToolDefinition,
    ToolParam, ToolReturns, HealthConfig, RecordingConfig,
)


def parse_profile(path: str | Path) -> DeviceModel:
    """Parse a single TOML profile file into a DeviceModel."""
    with open(path, "rb") as f:
        data = tomllib.load(f)

    device = data["device"]
    conn = data.get("connection", {})
    tools_data = data.get("tools", [])
    health_data = data.get("health", {})
    recording_data = data.get("recording", {})

    tools = []
    for t in tools_data:
        params = []
        for p in t.get("params", []):
            params.append(ToolParam(**p))
        tools.append(ToolDefinition(
            name=t["name"],
            description=t.get("description", ""),
            command=t.get("command", t["name"]),
            params=params,
            returns=ToolReturns(**(t.get("returns", {}))),
        ))

    return DeviceModel(
        name=device["name"],
        description=device.get("description", ""),
        connection=ConnectionConfig(**conn),
        tools=tools,
        health=HealthConfig(**health_data),
        recording=RecordingConfig(**recording_data),
    )


def parse_profiles_dir(profiles_dir: str) -> list[DeviceModel]:
    """Parse all .toml files in a directory into DeviceModel list."""
    path = Path(profiles_dir)
    if not path.exists():
        return []
    devices = []
    for f in sorted(path.glob("*.toml")):
        try:
            devices.append(parse_profile(f))
            print(f"  ✓ Loaded profile: {f.name}")
        except Exception as e:
            print(f"  ⚠ Failed to parse {f.name}: {e}")
    return devices
