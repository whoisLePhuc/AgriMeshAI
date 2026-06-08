"""
Background recorder — polls devices at configured intervals and records readings.
Runs in daemon mode (agrimesh daemon), not in stdio mode.
"""

import asyncio
import logging
from mcp_server.aggregator import Aggregator
from recorder import Recorder

logger = logging.getLogger("agrimesh.bg_recorder")


class BackgroundRecorder:
    """Periodically polls devices and records readings to SQLite."""

    def __init__(self, aggregator: Aggregator, recorder: Recorder):
        self.aggregator = aggregator
        self.recorder = recorder
        self._tasks: list[asyncio.Task] = []

    def start(self):
        """Start background polling for all devices with recording enabled."""
        for name, device in self.aggregator.devices.items():
            cfg = device.model.recording
            if not cfg.enabled:
                logger.info(f"  {name}: recording disabled, skipping")
                continue
            interval = cfg.poll_interval_ms / 1000
            task = asyncio.create_task(self._poll_loop(name, device, interval))
            self._tasks.append(task)
            logger.info(f"  {name}: polling every {interval}s")

        if self._tasks:
            logger.info(f"  Background recording started: {len(self._tasks)} device(s)")
        else:
            logger.info("  No devices configured for background recording")

    async def stop(self):
        """Cancel all polling tasks."""
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _poll_loop(self, name: str, device, interval: float):
        """Poll a single device at the configured interval."""
        await asyncio.sleep(2)  # stagger initial polls
        while True:
            try:
                await self._poll_once(name, device)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"  {name}: poll error: {e}")
            await asyncio.sleep(interval)

    async def _poll_once(self, name: str, device):
        """Poll all tools on a device and record numeric readings."""
        for tool_def in device.model.tools:
            full_name = f"{name}.{tool_def.name}"
            result = await self.aggregator.call_tool(full_name)
            if result.success:
                try:
                    val = float(result.data.strip())
                    await self.recorder.record_reading(
                        node_id=hash(name) % 1000,
                        sensor_id=tool_def.name,
                        value=val,
                        unit="",
                        quality=100,
                    )
                    logger.debug(f"  {full_name}: {val}")
                except ValueError:
                    pass  # non-numeric response, skip
            else:
                logger.warning(f"  {full_name}: {result.error}")
