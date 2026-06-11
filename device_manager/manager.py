"""DeviceManager — consolidated device lifecycle, catalog, and tool routing."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp.types import Tool

logger = logging.getLogger(__name__)

from mcp_server.adapters.base import AdapterResult
from device_manager.discovery import DiscoveredDevice
from device_manager.catalog import (
    DeviceCatalog,
    ToolRoute,
)


@dataclass
class DeviceStatus:
    """Tracks a device's connection state and health."""

    device: DiscoveredDevice
    connected: bool = False
    healthy: bool | None = None  # None = chưa kiểm tra
    error: str | None = None


class DeviceManager:
    """Consolidates device discovery, catalog building, connection lifecycle,
    tool routing, and health checks into a single class.
    """

    def __init__(self, profiles_dir: str | Path) -> None:
        self._profiles_dir = Path(profiles_dir) if isinstance(profiles_dir, str) else profiles_dir
        self._catalog: DeviceCatalog | None = None
        self._tools: list[Tool] = []
        self._routes: dict[str, ToolRoute] = {}
        self._device_locks: dict[str, asyncio.Lock] = {}
        self._devices: dict[str, DiscoveredDevice] = {}
        self._status: dict[str, DeviceStatus] = {}
        self._init_catalog()

    def _init_catalog(self) -> None:
        """Build the device catalog from the profiles directory."""
        self._catalog = DeviceCatalog.from_profiles_dir(self._profiles_dir)
        self._tools = self._catalog.tools
        self._routes = self._catalog.routes
        self._device_locks = self._catalog.locks
        self._devices = self._catalog.devices
        self._status = {
            name: DeviceStatus(device=d)
            for name, d in self._devices.items()
        }

    def reload_catalog(self) -> None:
        """Hot-reload profiles at runtime (for future use)."""
        self._init_catalog()

    @property
    def catalog(self) -> DeviceCatalog:
        if self._catalog is None:
            raise RuntimeError("catalog not built — call _init_catalog() first")
        return self._catalog

    @property
    def tools(self) -> list[Tool]:
        """The full unified tool catalog."""
        return list(self._tools)

    @property
    def device_names(self) -> list[str]:
        return list(self._devices.keys())

    @property
    def route_names(self) -> list[str]:
        """All namespaced tool names in the routing table."""
        return list(self._routes.keys())

    def get_route(self, namespaced_name: str) -> ToolRoute | None:
        return self._routes.get(namespaced_name)

    def get_status(self, device_name: str) -> DeviceStatus | None:
        return self._status.get(device_name)

    def all_statuses(self) -> dict[str, DeviceStatus]:
        return dict(self._status)

    async def connect_all(self) -> dict[str, AdapterResult]:
        """Connect all device adapters concurrently."""
        results: dict[str, AdapterResult] = {}

        async def _connect(name: str, device: DiscoveredDevice) -> None:
            try:
                result = await device.adapter.connect()
            except Exception as e:
                result = AdapterResult.fail(str(e))
            results[name] = result
            status = self._status[name]
            if result.success:
                status.connected = True
                status.error = None
            else:
                status.connected = False
                status.error = result.error

        await asyncio.gather(
            *[_connect(name, d) for name, d in self._devices.items()]
        )
        return results

    async def disconnect_all(self) -> dict[str, AdapterResult]:
        """Disconnect all device adapters concurrently."""
        results: dict[str, AdapterResult] = {}

        async def _disconnect(name: str, device: DiscoveredDevice) -> None:
            try:
                result = await device.adapter.disconnect()
            except Exception as e:
                result = AdapterResult.fail(str(e))
            results[name] = result
            status = self._status[name]
            if result.success:
                status.connected = False
                status.healthy = False
            else:
                status.error = result.error

        await asyncio.gather(
            *[_disconnect(name, d) for name, d in self._devices.items()]
        )
        return results

    async def call_tool(
        self, namespaced_name: str, arguments: dict[str, Any]
    ) -> AdapterResult:
        """Route a tool call to the correct device adapter.

        Acquires a per-device lock to prevent interleaved send/receive on
        the same adapter from concurrent calls.
        """
        route = self._routes.get(namespaced_name)
        if route is None:
            return AdapterResult.fail(f"unknown tool: {namespaced_name}")

        device = route.device
        status = self._status[device.name]

        if not status.connected:
            return AdapterResult.fail(f"device {device.name!r} is not connected")

        if status.healthy is False:
            logger.warning("device %s unhealthy, proceeding anyway", device.name)

        async with self._device_locks[device.name]:
            # If the tool has a command, interpolate arguments and send
            if route.command is not None:
                command = route.command.format(**(arguments or {}))
                send_result = await device.adapter.send(command)
                if not send_result.success:
                    return send_result
                return await device.adapter.receive()

            # Tools without a command (handler-based) — not yet implemented
            return AdapterResult.fail(
                f"tool {namespaced_name!r} has no command"
                " and handler dispatch is not yet implemented"
            )
````
    async def health_check_all(self) -> dict[str, AdapterResult]:
        """Run health checks on all connected devices concurrently.

        Acquires the per-device lock to avoid interleaving health check
        commands with in-flight tool calls on the same serial port.
        """
        results: dict[str, AdapterResult] = {}

        async def _check(name: str, device: DiscoveredDevice) -> None:
            status = self._status[name]
            if not status.connected:
                results[name] = AdapterResult.fail("not connected")
                return
            async with self._device_locks[name]:
                try:
                    result = await device.adapter.health_check()
                except Exception as e:
                    result = AdapterResult.fail(str(e))
            results[name] = result
            status.healthy = result.success
            if result.success:
                status.error = None
            else:
                status.error = result.error

        await asyncio.gather(
            *[_check(name, d) for name, d in self._devices.items()]
        )
        return results
