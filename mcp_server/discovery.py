"""
Device discovery — scans TOML profiles, instantiates adapters, generates tools.
"""

from mcp_server.profiles.parser import parse_profiles_dir
from mcp_server.profiles.generator import generate_tools
from mcp_server.devices.model import DeviceModel
from mcp_server.adapters.base import BaseAdapter
from mcp_server.adapters.mock import MockAdapter
from mcp_server.adapters.serial import SerialAdapter

_ADAPTER_REGISTRY = {
    "mock": MockAdapter,
    "serial": SerialAdapter,
}


class DiscoveredDevice:
    """A device discovered from a profile, with its adapter and MCP tools."""

    def __init__(self, model: DeviceModel, adapter: BaseAdapter):
        self.model = model
        self.tools = generate_tools(model)
        self.adapter = adapter


def discover_devices(profiles_dir: str) -> list[DiscoveredDevice]:
    """Scan profiles directory, parse TOML files, instantiate adapters."""
    devices = []
    models = parse_profiles_dir(profiles_dir)
    for m in models:
        adapter_cls = _ADAPTER_REGISTRY.get(m.connection.protocol)
        if not adapter_cls:
            print(f"  ⚠ Unknown protocol '{m.connection.protocol}' for {m.name}")
            continue
        # Create adapter with connection params
        conn = m.connection
        if conn.protocol == "serial":
            adapter = adapter_cls(port=conn.port, baud_rate=conn.baud_rate, timeout_ms=conn.timeout_ms)
        else:
            adapter = adapter_cls()
        devices.append(DiscoveredDevice(model=m, adapter=adapter))
    return devices
