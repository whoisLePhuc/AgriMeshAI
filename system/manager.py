"""SystemManager — central orchestrator for all AgriMeshAI modules.

Knows nothing about MCP, HTTP, or CLI.  Pure orchestration.
"""

from __future__ import annotations

import logging
from typing import Any

from mcp.types import Tool

from event_bus import EventBus, EventQueueManager
from device_manager.manager import DeviceManager
from device_manager.discovery import DiscoveryResult
from recorder.store import ReadingStore
from rule_engine import RuleEngine
from notifier import NotifierManager
from mcp_server.fleet import FleetTools
from system.module import HealthStatus, Module
from system.config import Config

logger = logging.getLogger(__name__)

_FLEET_PREFIX = "fleet."
_MAX_DLQ_SIZE = 10


class SystemManager:
    """Orchestrator duy nhất. Không biết MCP/HTTP/CLI."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._modules: dict[str, Module] = {}
        self._running = False

        # ── Khởi tạo tất cả module ──
        self.event_bus = EventBus()
        self.event_queue = EventQueueManager(maxsize=100)
        self.store = ReadingStore(config.db_path)
        self.device_manager = DeviceManager(config.profiles_dir)
        self.rule_engine = RuleEngine(
            self.event_bus,
            self.store,
            rules_path=str(config.rules_path),
        )
        self.notifier = NotifierManager(
            self.event_bus,
            config_path=str(config.notifiers_path),
        )
        self.fleet = FleetTools(self.device_manager, self.store)

    def register_module(self, name: str, module: Module) -> None:
        """Register an additional module (for extensions)."""
        self._modules[name] = module

    async def start(self) -> DiscoveryResult:
        """Khởi động theo thứ tự: event → store → device → rules → notifier."""
        if self._running:
            raise RuntimeError("SystemManager already started — call stop() first")

        try:
            # 1. Event queue
            await self.event_queue.start()

            # 2. Store
            await self.store.init()

            # 3. Device discovery & connect
            self.device_manager.reload_catalog()
            catalog = self.device_manager.catalog
            for path, error in catalog.errors:
                logger.warning("skipped profile %s: %s", path, error)

            results = await self.device_manager.connect_all()
            for name, result in results.items():
                if result.success:
                    logger.info("connected: %s", name)
                else:
                    logger.warning("failed to connect %s: %s", name, result.error)

            # 4. Rule engine + notifier (auto-subscribe to EventBus)
            # Already initialized in __init__; bridge event_queue → event_bus
            self.event_queue.subscribe(
                "reading_recorded",
                lambda **data: self.event_bus.emit("reading_recorded", **data),
            )
            self.event_queue.subscribe(
                "alert_triggered",
                lambda **data: self.event_bus.emit("alert_triggered", **data),
            )

            # 5. Start registered modules
            for name, module in self._modules.items():
                if hasattr(module, "start"):
                    await module.start()

            self._running = True
            logger.info("SystemManager started")

            return DiscoveryResult(
                devices=list(catalog.devices.values()),
                errors=catalog.errors,
            )
        except Exception:
            await self.stop()
            raise

    async def stop(self) -> None:
        """Dừng theo thứ tự ngược lại."""
        errors = []

        # Stop registered modules in reverse order
        for name, module in reversed(list(self._modules.items())):
            if hasattr(module, "stop"):
                try:
                    await module.stop()
                except Exception as e:
                    errors.append(f"module.{name}: {e}")
                    logger.error("stop module.%s failed: %s", name, e)

        for step, name in [
            (self.device_manager.disconnect_all(), "device_manager"),
            (self.event_queue.stop(), "event_queue"),
            (self.store.close(), "store"),
        ]:
            try:
                await step
            except Exception as e:
                errors.append(f"{name}: {e}")
                logger.error("stop %s failed: %s", name, e)

        self._running = False
        if errors:
            logger.warning("stop completed with %d error(s): %s", len(errors), errors)
        else:
            logger.info("SystemManager stopped")

    async def health(self) -> dict[str, HealthStatus]:
        """Health check tất cả module."""
        result = {}
        checks = [
            ("store", self._check_store()),
            ("device_manager", self._check_devices()),
            ("event_queue", self._check_queue()),
            ("rule_engine", self._check_rule_engine()),
            ("notifier", self._check_notifier()),
        ]
        for name, coro in checks:
            try:
                result[name] = await coro
            except Exception as e:
                result[name] = HealthStatus(healthy=False, message=str(e))

        for name, module in self._modules.items():
            try:
                h = await module.health() if hasattr(module, "health") else HealthStatus(healthy=True)
                result[f"module.{name}"] = h
            except Exception as e:
                result[f"module.{name}"] = HealthStatus(healthy=False, message=str(e))

        return result

    async def _check_store(self) -> HealthStatus:
        return HealthStatus(healthy=self.store is not None)

    async def _check_devices(self) -> HealthStatus:
        statuses = self.device_manager.all_statuses()
        if not statuses:
            return HealthStatus(healthy=False, message="no devices found")
        disconnected = [n for n, s in statuses.items() if not s.connected]
        healthy = len(disconnected) == 0
        msg = f"{len(statuses)} devices"
        if disconnected:
            msg += f"; disconnected: {disconnected}"
            return HealthStatus(healthy=False, message=msg)
        return HealthStatus(healthy=True, message=msg)

    async def _check_queue(self) -> HealthStatus:
        s = self.event_queue.stats
        return HealthStatus(healthy=s["dlq_size"] < _MAX_DLQ_SIZE, message=f"DLQ: {s['dlq_size']}")

    async def _check_rule_engine(self) -> HealthStatus:
        return HealthStatus(healthy=self.rule_engine is not None)

    async def _check_notifier(self) -> HealthStatus:
        channels = self.notifier.channels if self.notifier else []
        return HealthStatus(healthy=len(channels) > 0, message=str(channels))

    # ── Delegation methods ──

    def list_tools(self) -> list[Tool]:
        """Return the unified tool catalog from device + fleet tools."""
        return self.device_manager.tools + self.fleet.tools

    async def call_tool(self, name: str, args: dict[str, Any]) -> "AdapterResult":  # type: ignore[name-defined]
        """Route a tool call to the correct handler.

        Fleet tools are dispatched to FleetTools.
        Device tools are dispatched to DeviceManager.
        """
        from utils.adapters.base import AdapterResult
        if not name or "." not in name:
            return AdapterResult.fail(f"invalid tool name: {name}")
        if name.startswith(_FLEET_PREFIX):
            try:
                result = await self.fleet.call(name, args)
                if isinstance(result, dict) and "error" in result:
                    return AdapterResult.fail(result["error"])
                return AdapterResult.ok(result)
            except Exception as e:
                return AdapterResult.fail(str(e))
        return await self.device_manager.call_tool(name, args)
