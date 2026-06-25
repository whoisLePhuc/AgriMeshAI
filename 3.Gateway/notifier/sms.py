"""SMS notifier — sends SMS alerts via a GSM module (SIM800/SIM7600) over serial.

Requires a serial-connected GSM module. Uses the standard AT command set.
"""

from __future__ import annotations

import logging
from typing import Any

from notifier.base import BaseNotifier

logger = logging.getLogger(__name__)

try:
    import serial_asyncio
except ImportError:
    serial_asyncio = None  # type: ignore[assignment]


class SMSNotifier(BaseNotifier):
    """Sends SMS via GSM module over serial AT commands.

    Config (notifiers.yaml)::

        sms:
          enabled: true
          port: "/dev/ttyUSB2"
          baud_rate: 115200
          to: "+84901234567"               # recipient phone number
          pin: ""                           # SIM PIN (if required)
    """

    def __init__(self, config: dict[str, Any]) -> None:
        if serial_asyncio is None:
            raise ImportError("SMS notifier requires: pip install pyserial-asyncio")

        self._port = str(config.get("port", "/dev/ttyUSB2"))
        self._baud = int(config.get("baud_rate", 115200))
        self._to = str(config.get("to", ""))
        self._pin = str(config.get("pin", ""))
        self._reader = None
        self._writer = None

        if not self._to:
            raise ValueError("SMS: 'to' (recipient number) is required")

        logger.info("sms: configured for %s via %s", self._to, self._port)

    @property
    def name(self) -> str:
        return "sms"

    async def _connect(self) -> bool:
        """Open serial connection to GSM module."""
        if self._reader is not None:
            return True
        try:
            import serial_asyncio as sa
            self._reader, self._writer = await sa.open_serial_connection(
                url=self._port, baudrate=self._baud,
            )
            await self._send_at("AT")
            await self._send_at(f'AT+CMGF=1')  # text mode
            if self._pin:
                await self._send_at(f'AT+CPIN="{self._pin}"')
            return True
        except Exception as e:
            logger.warning("sms: connect failed: %s", e)
            return False

    async def _send_at(self, cmd: str) -> str | None:
        """Send AT command and return response."""
        if not self._writer or not self._reader:
            return None
        import asyncio
        self._writer.write((cmd + "\r").encode())
        await self._writer.drain()
        await asyncio.sleep(0.5)
        try:
            resp = await asyncio.wait_for(self._reader.readline(), timeout=2)
            return resp.decode(errors="replace").strip()
        except asyncio.TimeoutError:
            return None

    async def _disconnect(self) -> None:
        if self._writer:
            self._writer.close()
            self._writer = None
            self._reader = None

    async def send_alert(
        self,
        rule_id: str,
        severity: str,
        message: str,
        device_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        text = f"[{severity}] {rule_id}: {message[:120]}"
        await self._send_sms(text)

    async def send_report(self, title: str, body: str) -> None:
        text = f"{title}: {body[:120]}"
        await self._send_sms(text)

    async def _send_sms(self, text: str) -> None:
        """Send an SMS via AT commands."""
        if not await self._connect():
            return
        try:
            await self._send_at(f'AT+CMGS="{self._to}"')
            import asyncio
            self._writer.write((text + "\x1a").encode())  # Ctrl+Z to send
            await self._writer.drain()
            await asyncio.sleep(1)
        except Exception as e:
            logger.warning("sms: send failed: %s", e)
        finally:
            await self._disconnect()

    async def close(self) -> None:
        await self._disconnect()
