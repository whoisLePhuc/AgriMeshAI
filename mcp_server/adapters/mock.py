"""
Mock adapter for testing without hardware.
Returns canned responses for development and testing.
"""

import asyncio
from .base import BaseAdapter, AdapterResult


class MockAdapter(BaseAdapter):
    """Simulated device adapter with configurable canned responses."""

    _DEFAULT_RESPONSES = {
        "TEMP": "32.5",
        "HUM": "68.2",
        "BAT": "85",
        "READ_TEMP": "32.5",
        "READ_HUMID": "68.2",
        "READ_ALL": "32.5,68.2",
        "STATUS": "OK",
        "PING": "PONG",
        "READ": "25.3",
    }

    def __init__(self, responses: dict[str, str] | None = None):
        self.responses = {**self._DEFAULT_RESPONSES, **(responses or {})}
        self._connected = False

    async def connect(self) -> AdapterResult:
        self._connected = True
        return AdapterResult(success=True, data="Mock device connected")

    async def disconnect(self) -> AdapterResult:
        self._connected = False
        return AdapterResult(success=True, data="Mock device disconnected")

    async def send(self, data: str | bytes) -> AdapterResult:
        await asyncio.sleep(0.05)  # Simulate latency
        cmd = data.decode() if isinstance(data, bytes) else str(data)
        resp = self.responses.get(cmd, f"mock:ack:{cmd}")
        return AdapterResult(success=True, data=str(resp))

    async def receive(self, length: int | None = None, timeout: float | None = None) -> AdapterResult:
        return AdapterResult(success=True, data="mock:ok")

    async def health_check(self) -> AdapterResult:
        return AdapterResult(success=True, data="mock:alive")
