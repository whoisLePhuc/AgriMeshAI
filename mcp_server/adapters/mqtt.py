"""
MQTT adapter — communicates with devices over MQTT pub/sub.
Uses paho-mqtt with asyncio bridge via call_soon_threadsafe.
"""

import asyncio
import logging
import paho.mqtt.client as mqtt
from .base import BaseAdapter, AdapterResult

logger = logging.getLogger(__name__)
_MAX_QUEUE_SIZE = 100


class MQTTAdapter(BaseAdapter):
    """Async MQTT adapter using paho-mqtt with asyncio bridge.
    
    Topic layout:
      {topic_prefix}/cmd      — adapter publishes commands here
      {topic_prefix}/response — adapter subscribes here for responses
    """

    def __init__(self, broker: str = "localhost", port: int = 1883,
                 topic_prefix: str = "agrimesh/device", timeout_ms: int = 5000):
        self.broker = broker
        self.port = port
        self.topic_prefix = topic_prefix
        self.timeout_ms = timeout_ms
        self._client: mqtt.Client | None = None
        self._response_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=_MAX_QUEUE_SIZE)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connected_event: asyncio.Event | None = None

    @property
    def _cmd_topic(self) -> str:
        return f"{self.topic_prefix}/cmd"

    @property
    def _response_topic(self) -> str:
        return f"{self.topic_prefix}/response"

    def _drain_queue(self):
        while not self._response_queue.empty():
            try:
                self._response_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    def _mark_disconnected(self):
        client = self._client
        self._client = None
        self._loop = None
        if client is not None:
            try:
                client.disconnect()
                client.loop_stop()
            except Exception:
                pass
        self._drain_queue()

    async def connect(self) -> AdapterResult:
        if self._client and self._client.is_connected():
            return AdapterResult(success=False, error="already connected")
        self._loop = asyncio.get_running_loop()
        self._connected_event = asyncio.Event()
        self._drain_queue()
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        timeout = self.timeout_ms / 1000

        def on_connect(client_, userdata, flags, rc, properties=None):
            if not rc.is_failure:
                client_.subscribe(self._response_topic)
                if self._loop and self._connected_event:
                    self._loop.call_soon_threadsafe(self._connected_event.set)

        def on_disconnect(client_, userdata, flags, rc, properties=None):
            logger.warning("MQTT disconnected: %s", rc)
            self._client = None

        def on_message(client_, userdata, msg):
            text = msg.payload.decode("utf-8", errors="replace").strip()
            if self._loop:
                try:
                    self._loop.call_soon_threadsafe(self._response_queue.put_nowait, text)
                except Exception:
                    pass

        client.on_connect = on_connect
        client.on_disconnect = on_disconnect
        client.on_message = on_message

        try:
            client.connect(self.broker, self.port)
            client.loop_start()
        except Exception as e:
            return AdapterResult(success=False, error=f"connection failed: {e}")

        self._client = client
        try:
            await asyncio.wait_for(self._connected_event.wait(), timeout=timeout)
        except TimeoutError:
            self._mark_disconnected()
            return AdapterResult(success=False, error=f"timed out: {self.broker}:{self.port}")

        logger.info("connected to MQTT %s:%d", self.broker, self.port)
        return AdapterResult(success=True, data=f"Connected to {self.broker}:{self.port}")

    async def disconnect(self) -> AdapterResult:
        if not self._client or not self._client.is_connected():
            return AdapterResult(success=True, data="already disconnected")
        client = self._client
        self._client = None
        self._loop = None
        try:
            if client:
                client.disconnect()
                client.loop_stop()
        except Exception as e:
            logger.warning("disconnect error: %s", e)
        self._drain_queue()
        return AdapterResult(success=True, data="disconnected")

    async def send(self, data: str | bytes) -> AdapterResult:
        if not self._client or not self._client.is_connected():
            return AdapterResult(success=False, error="not connected")
        try:
            info = self._client.publish(self._cmd_topic, data)
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                self._mark_disconnected()
                return AdapterResult(success=False, error=f"publish failed: rc={info.rc}")
        except Exception as e:
            self._mark_disconnected()
            return AdapterResult(success=False, error=f"send failed: {e}")
        return AdapterResult(success=True, data=f"sent to {self._cmd_topic}")

    async def receive(self, length: int | None = None, timeout: float | None = None) -> AdapterResult:
        if not self._client or not self._client.is_connected():
            return AdapterResult(success=False, error="not connected")
        t = timeout if timeout else self.timeout_ms / 1000
        try:
            text = await asyncio.wait_for(self._response_queue.get(), timeout=t)
        except TimeoutError:
            return AdapterResult(success=False, error="receive timed out")
        except Exception as e:
            self._mark_disconnected()
            return AdapterResult(success=False, error=f"receive failed: {e}")
        if not text:
            return AdapterResult(success=False, error="empty response")
        return AdapterResult(success=True, data=text)

    async def health_check(self) -> AdapterResult:
        result = await self.send("PING")
        if not result.success:
            return result
        return await self.receive()
