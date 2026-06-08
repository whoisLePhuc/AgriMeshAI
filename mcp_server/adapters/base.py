"""
Base adapter interface for hardware communication (LoRa, serial, MQTT).
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class AdapterResult:
    """Result from an adapter operation."""
    success: bool
    data: str = ""
    error: str = ""


class BaseAdapter:
    """Abstract base class for all device protocol adapters."""

    async def connect(self) -> AdapterResult:
        """Establish connection to the device."""
        raise NotImplementedError

    async def disconnect(self) -> AdapterResult:
        """Close connection to the device."""
        raise NotImplementedError

    async def send(self, data: str | bytes) -> AdapterResult:
        """Send data/command to the device."""
        raise NotImplementedError

    async def receive(self, length: Optional[int] = None, timeout: Optional[float] = None) -> AdapterResult:
        """Receive data/response from the device."""
        raise NotImplementedError

    async def health_check(self) -> AdapterResult:
        """Check if the device is responsive."""
        raise NotImplementedError
