"""
Serial adapter — UART communication via pyserial-asyncio.
"""

import asyncio
from .base import BaseAdapter, AdapterResult


class SerialAdapter(BaseAdapter):
    """Async UART serial adapter for LoRa module communication."""

    def __init__(self, port: str = "/dev/ttyUSB0", baud_rate: int = 115200,
                 timeout_ms: int = 3000):
        self.port = port
        self.baud_rate = baud_rate
        self.timeout_ms = timeout_ms
        self._serial = None
        self._lock = asyncio.Lock()

    async def connect(self) -> AdapterResult:
        try:
            import serial_asyncio
            self._serial = await serial_asyncio.open_serial_connection(
                url=self.port, baudrate=self.baud_rate,
            )
            return AdapterResult(success=True, data=f"Connected to {self.port} @ {self.baud_rate}")
        except ImportError:
            return AdapterResult(success=False, error="serial_asyncio not installed")
        except Exception as e:
            return AdapterResult(success=False, error=str(e))

    async def disconnect(self) -> AdapterResult:
        if self._serial:
            self._serial.close()
            self._serial = None
        return AdapterResult(success=True, data="Disconnected")

    async def send(self, data: str | bytes) -> AdapterResult:
        if not self._serial:
            return AdapterResult(success=False, error="Not connected")
        async with self._lock:
            try:
                payload = data.encode() if isinstance(data, str) else data
                self._serial.write(payload + b"\n")
                await self._serial.drain()
                return AdapterResult(success=True, data=f"Sent: {len(payload)} bytes")
            except Exception as e:
                return AdapterResult(success=False, error=str(e))

    async def receive(self, length: int | None = None, timeout: float | None = None) -> AdapterResult:
        if not self._serial:
            return AdapterResult(success=False, error="Not connected")
        async with self._lock:
            try:
                t = timeout if timeout else self.timeout_ms / 1000
                data = await asyncio.wait_for(self._serial.readline(), timeout=t)
                return AdapterResult(success=True, data=data.decode().strip())
            except asyncio.TimeoutError:
                return AdapterResult(success=False, error="Timeout")
            except Exception as e:
                return AdapterResult(success=False, error=str(e))

    async def health_check(self) -> AdapterResult:
        """Ping the device to check if it's responsive."""
        return await self.send("PING")
