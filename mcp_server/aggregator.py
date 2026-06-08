"""
Aggregator — central device registry, tool routing, per-device locking.
"""

import asyncio
from mcp_server.discovery import DiscoveredDevice
from mcp_server.adapters.base import AdapterResult


class Aggregator:
    """Manages discovered devices, routes tool calls, enforces per-device locks."""

    def __init__(self):
        self.devices: dict[str, DiscoveredDevice] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def register(self, name: str, device: DiscoveredDevice):
        """Register a single device."""
        self.devices[name] = device
        self._locks[name] = asyncio.Lock()

    def register_all(self, discovered: list[DiscoveredDevice]):
        """Register multiple devices from discovery results."""
        for d in discovered:
            self.register(d.model.name, d)

    def get_tools(self) -> list:
        """Get all MCP tools from all registered devices (for list_tools)."""
        tools = []
        for d in self.devices.values():
            tools.extend(d.tools)
        return tools

    async def call_tool(self, name: str, args: dict = None) -> AdapterResult:
        """Route a tool call to the correct device and execute it.
        
        Tool name format: "device_name.tool_name"
        """
        if "." not in name:
            return AdapterResult(success=False, error=f"Invalid tool name: {name}")

        device_name, tool_name = name.split(".", 1)
        device = self.devices.get(device_name)
        if not device:
            return AdapterResult(success=False, error=f"Device '{device_name}' not found")

        # Find tool definition
        tool_def = None
        for t in device.model.tools:
            if t.name == tool_name:
                tool_def = t
                break
        if not tool_def:
            return AdapterResult(
                success=False,
                error=f"Tool '{tool_name}' not found on device '{device_name}'",
            )

        # Per-device lock — prevents concurrent conflicting commands
        async with self._locks[device_name]:
            # Health check before executing
            health = await device.adapter.health_check()
            if not health.success:
                return AdapterResult(
                    success=False,
                    error=f"Device '{device_name}' unreachable: {health.error}",
                )

            # Execute command
            return await device.adapter.send(tool_def.command)

    async def health_check_all(self) -> dict[str, str]:
        """Check health of all registered devices."""
        results = {}
        for name, device in self.devices.items():
            result = await device.adapter.health_check()
            results[name] = "alive" if result.success else f"error: {result.error}"
        return results
