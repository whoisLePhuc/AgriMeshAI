"""Device catalog — builds a unified MCP tool catalog from device profiles."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from mcp.types import Tool

from device_manager.discovery import DiscoveredDevice, discover_devices
from device_manager.tool_builder import generate_tools
from device_manager.model import ToolParam, ToolReturns


@dataclass
class ToolRoute:
    """Maps a namespaced tool name back to its device and tool definition."""

    device: DiscoveredDevice
    tool_name: str  # original un-namespaced name
    command: str | None = None  # the command string to send over the adapter
    returns: ToolReturns | None = None  # return type spec for storage


@dataclass
class DeviceCatalog:
    """Unified device catalog containing tools, routes, devices, locks, and errors."""

    tools: list[Tool] = field(default_factory=list)
    routes: dict[str, ToolRoute] = field(default_factory=dict)
    devices: dict[str, DiscoveredDevice] = field(default_factory=dict)
    locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    errors: list[tuple[Path, str]] = field(default_factory=list)

    @classmethod
    def from_profiles_dir(cls, profiles_dir: str | Path) -> DeviceCatalog:
        profiles_dir = Path(profiles_dir) if isinstance(profiles_dir, str) else profiles_dir
        """Build a DeviceCatalog from a directory of TOML device profiles."""
        result = discover_devices(profiles_dir)
        catalog = cls(errors=result.errors)
        for device in result.devices:
            catalog.devices[device.name] = device
            catalog.locks[device.name] = asyncio.Lock()
            generated = generate_tools(device.model)
            catalog.tools.extend(generated)
            for mcp_tool, tool_def in zip(generated, device.model.tools):
                catalog.routes[mcp_tool.name] = ToolRoute(
                    device=device,
                    tool_name=tool_def.name,
                    command=tool_def.command,
                    returns=tool_def.returns,
                )
        return catalog
