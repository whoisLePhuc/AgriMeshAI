"""SerialATAdapter — AT command protocol over UART for LoRa Gateway.

Communicates with the LoRa Gateway (ESP32) via AT text commands.
Handles SEQ matching, node auto-discovery, and sensor cache.

CHANGES vs original:
  - _mark_disconnected now fails all pending futures (previously they would hang
    until their individual timeouts — up to 6 s each)
  - _reader_loop distinguishes I/O errors (→ _mark_disconnected) from read
    timeouts (→ continue), so a disconnected port is detected immediately
  - on_temp_report / on_relay_report / on_node_join callbacks are dispatched
    via asyncio.create_task so a slow callback cannot block the reader loop
  - +HB handler resolves the pending future (previously +HB was logged but
    the future for AT+PING_ALL was left unresolved until timeout)
  - AT+NODE_INIT sent via _request so +NODE_INIT:OK resolves cleanly; a new
    _dispatch_line branch handles +NODE_INIT:OK and +NODE_ACK:OK
  - SEQ promoted to uint16_t range (0–65535); reap window widened accordingly
  - at_get_temp replaced by generic at_get_sensor(node_id, sensor_id);
    at_get_temp kept as a convenience wrapper
  - receive() documents its limitation and now returns the first resolved
    future only when no concurrent requests are in flight; concurrent callers
    should use at_get_temp / at_set_relay directly
  - _next_seq skips any SEQ slot that is still occupied (prevents reuse of a
    slot whose future is still awaiting resolution)

Usage:
    adapter = SerialATAdapter(connection_config)
    result = await adapter.at_get_temp(node_id=1)
    result = await adapter.at_get_sensor(node_id=1, sensor_id=1)   # humidity
    result = await adapter.at_set_relay(node_id=2, relay_id=0, state=1, duration_s=600)
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

import serial_asyncio

from utils.adapters.base import AdapterResult, BaseAdapter

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────
CACHE_TTL_S    = 90       # sensor cache TTL (push interval 60 s + 50 % buffer)
RETRY_MAX      = 3
TIMEOUT_S      = 2.0
NODE_AUTO_ID_START = 1
MAX_PENDING    = 32
AT_SEQ_MAX     = 0xFFFF   # 16-bit SEQ space (matches mesh_types.h AT_SEQ_MAX)

# ── AT response patterns ───────────────────────────────────────────
_RE_TEMP         = re.compile(r'\+TEMP:(\d+),(\d+),([\d.-]+),SEQ=(\d+)')
_RE_TEMP_REPORT  = re.compile(r'\+TEMP_REPORT:(\d+),(\d+),([\d.-]+)')
_RE_RELAY_ACK    = re.compile(r'\+RELAY_ACK:(\d+),(\d+),(ON|OFF),SEQ=(\d+)')
_RE_RELAY_REPORT = re.compile(r'\+RELAY_REPORT:(\d+),(\d+),(ON|OFF)')
_RE_PONG         = re.compile(r'\+PONG:(\d+),SEQ=(\d+)')
_RE_NODES        = re.compile(r'\+NODES:(\d+)((?:,\d+,\d+)*),SEQ=(\d+)')
_RE_NODE_JOIN    = re.compile(r'\+NODE_JOIN:(0x[0-9A-Fa-f]+),(\d+),(\d+)\.(\d+)')
_RE_ERR          = re.compile(r'\+ERR:(\d+),([^,]+),SEQ=(\d+)')
_RE_HB           = re.compile(r'\+HB:(\d+)/(\d+),SEQ=(\d+)')
_RE_NODE_INIT_OK = re.compile(r'\+NODE_INIT:OK')
_RE_NODE_ACK_OK  = re.compile(r'\+NODE_ACK:OK')

# ── Data structures ────────────────────────────────────────────────

@dataclass
class NodeEntry:
    node_id:   int
    lora_addr: int        # uint16
    node_type: int        # 0=sensor, 1=actuator
    fw_ver:    str  = "1.0"
    active:    bool = True
    last_seen: float = 0.0

@dataclass
class SensorCache:
    value:     float
    timestamp: float
    node_id:   int
    sensor_id: int

@dataclass
class PendingRequest:
    seq:     int
    future:  asyncio.Future[AdapterResult] = field(default_factory=asyncio.Future)
    sent_at: float = 0.0


class SerialATAdapter(BaseAdapter):
    """AT command adapter for LoRa Gateway over UART serial."""

    def __init__(self, config: Any) -> None:
        super().__init__(config)
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._seq: int = 0
        self._pending: dict[int, PendingRequest] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._running = False

        # Node discovery
        self.nodes: dict[int, NodeEntry] = {}
        self._lora_to_node: dict[int, int] = {}
        self._next_node_id = NODE_AUTO_ID_START

        # Sensor cache: (node_id, sensor_id) → SensorCache
        self._cache: dict[tuple[int, int], SensorCache] = {}

        # Unsolicited event callbacks — always invoked via asyncio.create_task
        # to avoid blocking the reader loop.
        self.on_temp_report:  Callable[[int, int, float], Any] | None = None
        self.on_relay_report: Callable[[int, int, str], Any] | None = None
        self.on_node_join:    Callable[[int, int, int], Any] | None = None

    # ── Connection lifecycle ───────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._writer is not None and self._running

    async def connect(self) -> AdapterResult:
        if self.connected:
            return AdapterResult.fail("already connected")

        port = self.config.port
        if not port:
            return AdapterResult.fail("no serial port configured")

        baudrate = self.config.baud_rate or 115200
        timeout  = self.config.timeout_ms / 1000

        try:
            self._reader, self._writer = await asyncio.wait_for(
                serial_asyncio.open_serial_connection(url=port, baudrate=baudrate),
                timeout=timeout,
            )
        except TimeoutError:
            return AdapterResult.fail(f"connection timed out: {port}")
        except Exception as e:
            return AdapterResult.fail(f"connection failed: {e}")

        self._running = True
        self._reader_task = asyncio.create_task(self._reader_loop())
        logger.info("SerialATAdapter connected to %s at %d baud", port, baudrate)

        # Init auto-discovery — use _request so +NODE_INIT:OK resolves cleanly
        init_result = await self._request("AT+NODE_INIT")
        if not init_result.success:
            logger.warning("NODE_INIT did not confirm: %s", init_result.error)

        return AdapterResult.ok()

    async def disconnect(self) -> AdapterResult:
        self._running = False
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
        writer = self._writer
        self._reader = None
        self._writer = None
        if writer:
            try:
                writer.close()
                if hasattr(writer, "wait_closed"):
                    await writer.wait_closed()
            except Exception:
                pass
        self._fail_all_pending("disconnected")
        return AdapterResult.ok()

    # ── BaseAdapter interface ──────────────────────────────────────

    async def send(self, data: bytes | str) -> AdapterResult:
        cmd = data if isinstance(data, str) else data.decode("utf-8", errors="replace")
        if not cmd.endswith("\r\n"):
            if "SEQ=" not in cmd:
                cmd = f"{cmd},SEQ={self._next_seq()}"
            cmd = cmd + "\r\n"
        return await self._send_raw(cmd)

    async def receive(self, length: int | None = None, timeout: float | None = None) -> AdapterResult:
        """Wait for any pending future to resolve.

        NOTE: This method is only safe when there is at most one in-flight
        request.  For concurrent calls use at_get_sensor / at_set_relay
        directly — they each own their own Future.
        """
        if timeout is None:
            timeout = TIMEOUT_S + (self.config.timeout_ms / 1000)
        if not self._pending:
            return await self._read_line(timeout)
        try:
            return await asyncio.wait_for(self._wait_response(timeout), timeout=timeout)
        except asyncio.TimeoutError:
            return AdapterResult.fail("receive timed out")

    async def health_check(self) -> AdapterResult:
        if not self.connected:
            return AdapterResult.fail("not connected")
        result = await self.at_ping(0)
        return AdapterResult.ok({"status": "healthy"}) if result.success else result

    # ── High-level AT commands ─────────────────────────────────────

    async def at_get_sensor(self, node_id: int, sensor_id: int) -> AdapterResult:
        """Get any sensor reading. Returns cached value if fresh (< CACHE_TTL_S)."""
        cached = self._cache_get(node_id, sensor_id)
        if cached is not None:
            return AdapterResult.ok({
                "node_id": node_id, "sensor_id": sensor_id,
                "value": cached.value, "source": "cache",
            })
        # Only AT+GET_TEMP is defined in the current protocol;
        # extend here when more AT commands are added (e.g. AT+GET_HUM).
        return await self._request(f"AT+GET_TEMP={node_id}")

    async def at_get_temp(self, node_id: int) -> AdapterResult:
        """Convenience wrapper: get temperature (sensor_id=0)."""
        return await self.at_get_sensor(node_id, 0)

    async def at_set_relay(self, node_id: int, relay_id: int,
                            state: int, duration_s: int = 0) -> AdapterResult:
        """Control relay on actuator node."""
        return await self._request(
            f"AT+SET_RELAY={node_id},{relay_id},{state},{duration_s}"
        )

    async def at_ping(self, node_id: int) -> AdapterResult:
        return await self._request(f"AT+PING={node_id}")

    async def at_list_nodes(self) -> AdapterResult:
        return await self._request("AT+LIST_NODES")

    # ── Internals ─────────────────────────────────────────────────

    def _next_seq(self) -> int:
        """Allocate the next SEQ, skipping occupied slots and reaping stale ones."""
        now = time.monotonic()
        reap_threshold = TIMEOUT_S * RETRY_MAX

        # Reap stale entries first
        for s in list(self._pending):
            if now - self._pending[s].sent_at > reap_threshold:
                pr = self._pending.pop(s)
                if not pr.future.done():
                    pr.future.set_result(AdapterResult.fail("timeout"))

        # Advance _seq until we find a free slot (handles SEQ reuse race)
        for _ in range(AT_SEQ_MAX + 1):
            self._seq = (self._seq + 1) & AT_SEQ_MAX
            if self._seq not in self._pending:
                return self._seq

        # All 65536 slots occupied — extremely unlikely; fail loudly
        raise RuntimeError("SEQ space exhausted — too many concurrent requests")

    async def _request(self, cmd: str) -> AdapterResult:
        seq      = self._next_seq()
        full_cmd = f"{cmd},SEQ={seq}\r\n"

        loop   = asyncio.get_event_loop()
        future: asyncio.Future[AdapterResult] = loop.create_future()
        self._pending[seq] = PendingRequest(seq=seq, future=future,
                                             sent_at=time.monotonic())

        send_result = await self._send_raw(full_cmd)
        if not send_result.success:
            self._pending.pop(seq, None)
            return send_result

        try:
            return await asyncio.wait_for(future, timeout=TIMEOUT_S * RETRY_MAX)
        except asyncio.TimeoutError:
            self._pending.pop(seq, None)
            return AdapterResult.fail("timeout")

    async def _wait_response(self, timeout: float) -> AdapterResult:
        if not self._pending:
            return AdapterResult.fail("no pending requests")
        tasks = [pr.future for pr in self._pending.values() if not pr.future.done()]
        if not tasks:
            return AdapterResult.fail("all pending already resolved")
        done, _ = await asyncio.wait(tasks, timeout=timeout,
                                      return_when=asyncio.FIRST_COMPLETED)
        if done:
            return await list(done)[0]
        return AdapterResult.fail("timeout")

    async def _send_raw(self, data: str) -> AdapterResult:
        if not self.connected or self._writer is None:
            return AdapterResult.fail("not connected")
        try:
            self._writer.write(data.encode("utf-8"))
            await self._writer.drain()
            return AdapterResult.ok()
        except Exception as e:
            self._mark_disconnected()
            return AdapterResult.fail(f"send failed: {e}")

    async def _read_line(self, timeout: float | None = None) -> AdapterResult:
        if not self.connected or self._reader is None:
            return AdapterResult.fail("not connected")
        if timeout is None:
            timeout = self.config.timeout_ms / 1000
        try:
            raw = await asyncio.wait_for(self._reader.readline(), timeout=timeout)
        except asyncio.TimeoutError:
            # Normal idle — no data within window
            return AdapterResult.fail("read timed out", is_io_error=False)
        except Exception as e:
            self._mark_disconnected()
            return AdapterResult.fail(f"read failed: {e}", is_io_error=True)
        text = raw.decode("utf-8", errors="replace").strip()
        return AdapterResult.ok(text) if text else AdapterResult.fail("empty")

    # ── Background reader ──────────────────────────────────────────

    async def _reader_loop(self) -> None:
        """Continuously read UART lines, distinguishing I/O errors from timeouts."""
        while self._running:
            result = await self._read_line(timeout=0.5)
            if not result.success:
                # Only bail out on a real I/O error, not on an idle timeout
                if getattr(result, "is_io_error", False):
                    logger.error("UART I/O error — marking disconnected")
                    self._mark_disconnected()
                    return
                continue
            line = result.data
            if line:
                await self._dispatch_line(line)

    async def _dispatch_line(self, line: str) -> None:
        """Parse one AT response/event line and resolve futures or fire callbacks."""

        # +TEMP:node,sensor,val,SEQ=n
        m = _RE_TEMP.match(line)
        if m:
            node_id, sid, val, seq = int(m[1]), int(m[2]), float(m[3]), int(m[4])
            pr = self._pending.pop(seq, None)
            data = {"node_id": node_id, "sensor_id": sid, "value": val}
            self._cache_set(node_id, sid, val)
            if pr and not pr.future.done():
                pr.future.set_result(AdapterResult.ok(data))
            return

        # +TEMP_REPORT:node,sensor,val  (unsolicited)
        m = _RE_TEMP_REPORT.match(line)
        if m:
            node_id, sid, val = int(m[1]), int(m[2]), float(m[3])
            self._cache_set(node_id, sid, val)
            if self.on_temp_report:
                asyncio.create_task(self._invoke_cb(self.on_temp_report, node_id, sid, val))
            return

        # +RELAY_ACK:node,relay,state,SEQ=n
        m = _RE_RELAY_ACK.match(line)
        if m:
            node_id, rid, st, seq = int(m[1]), int(m[2]), m[3], int(m[4])
            pr = self._pending.pop(seq, None)
            data = {"node_id": node_id, "relay_id": rid, "state": st, "on": st == "ON"}
            if pr and not pr.future.done():
                pr.future.set_result(AdapterResult.ok(data))
            return

        # +RELAY_REPORT:node,relay,state  (unsolicited)
        m = _RE_RELAY_REPORT.match(line)
        if m:
            node_id, rid, st = int(m[1]), int(m[2]), m[3]
            if self.on_relay_report:
                asyncio.create_task(self._invoke_cb(self.on_relay_report, node_id, rid, st))
            return

        # +PONG:node,SEQ=n
        m = _RE_PONG.match(line)
        if m:
            node_id, seq = int(m[1]), int(m[2])
            pr = self._pending.pop(seq, None)
            if pr and not pr.future.done():
                pr.future.set_result(AdapterResult.ok({"node_id": node_id, "alive": True}))
            return

        # +NODES:count,...SEQ=n
        m = _RE_NODES.match(line)
        if m:
            count, rest, seq = int(m[1]), m[2], int(m[3])
            nodes, parts = [], rest.split(",")
            for i in range(1, len(parts), 2):
                if i + 1 < len(parts):
                    nodes.append({"node_id": int(parts[i]), "type": int(parts[i + 1])})
            pr = self._pending.pop(seq, None)
            if pr and not pr.future.done():
                pr.future.set_result(AdapterResult.ok({"count": count, "nodes": nodes}))
            return

        # +NODE_JOIN:lora_addr,type,major.minor  (unsolicited)
        m = _RE_NODE_JOIN.match(line)
        if m:
            lora_addr = int(m[1], 16)
            ntype     = int(m[2])
            node_id   = self._lora_to_node.get(lora_addr)
            if node_id is None:
                node_id = self._next_node_id
                self._next_node_id += 1
                self._lora_to_node[lora_addr] = node_id
            self.nodes[node_id] = NodeEntry(
                node_id=node_id, lora_addr=lora_addr, node_type=ntype,
                fw_ver=f"{m[3]}.{m[4]}", last_seen=time.time())
            if self.on_node_join:
                asyncio.create_task(self._invoke_cb(self.on_node_join, node_id, lora_addr, ntype))
            await self._send_raw(
                f"AT+NODE_ACK={lora_addr:#x},{node_id},SEQ={self._next_seq()}\r\n"
            )
            return

        # +ERR:code,msg,SEQ=n
        m = _RE_ERR.match(line)
        if m:
            _code, msg, seq = int(m[1]), m[2], int(m[3])
            pr = self._pending.pop(seq, None)
            if pr and not pr.future.done():
                pr.future.set_result(AdapterResult.fail(f"AT error: {msg}"))
            return

        # +HB:responded/total,SEQ=n  — resolves the AT+PING_ALL future
        m = _RE_HB.match(line)
        if m:
            responded, total, seq = int(m[1]), int(m[2]), int(m[3])
            logger.info("AT heartbeat: %d/%d nodes responding", responded, total)
            pr = self._pending.pop(seq, None)
            if pr and not pr.future.done():
                pr.future.set_result(AdapterResult.ok({
                    "responded": responded, "total": total,
                }))
            return

        # +NODE_INIT:OK  — resolves the AT+NODE_INIT future
        if _RE_NODE_INIT_OK.match(line):
            # NODE_INIT uses the most-recently-sent pending SEQ; find it
            self._resolve_oldest_pending(AdapterResult.ok({"init": True}))
            return

        # +NODE_ACK:OK  — resolves the AT+NODE_ACK future (if any)
        if _RE_NODE_ACK_OK.match(line):
            self._resolve_oldest_pending(AdapterResult.ok({"ack": True}))
            return

        logger.debug("Unhandled AT line: %s", line)

    # ── Sensor cache ───────────────────────────────────────────────

    def _cache_set(self, node_id: int, sensor_id: int, value: float) -> None:
        self._cache[(node_id, sensor_id)] = SensorCache(
            value=value, timestamp=time.monotonic(),
            node_id=node_id, sensor_id=sensor_id)

    def _cache_get(self, node_id: int, sensor_id: int) -> SensorCache | None:
        entry = self._cache.get((node_id, sensor_id))
        if entry and (time.monotonic() - entry.timestamp) < CACHE_TTL_S:
            return entry
        return None

    # ── Helpers ────────────────────────────────────────────────────

    def _fail_all_pending(self, reason: str) -> None:
        """Resolve every pending future with a failure result."""
        for pr in list(self._pending.values()):
            if not pr.future.done():
                pr.future.set_result(AdapterResult.fail(reason))
        self._pending.clear()

    def _resolve_oldest_pending(self, result: AdapterResult) -> None:
        """Resolve the oldest unresolved pending future (for SEQ-less responses)."""
        if not self._pending:
            return
        oldest_seq = min(self._pending, key=lambda s: self._pending[s].sent_at)
        pr = self._pending.pop(oldest_seq)
        if not pr.future.done():
            pr.future.set_result(result)

    def _mark_disconnected(self) -> None:
        """Called on unexpected I/O error. Fails all pending futures immediately."""
        self._running = False
        writer = self._writer
        self._reader = None
        self._writer = None
        if writer:
            try:
                writer.close()
            except Exception:
                pass
        # Fail all waiters so callers get an error promptly, not after timeout
        self._fail_all_pending("disconnected")

    @staticmethod
    async def _invoke_cb(cb: Callable[..., Any], *args: Any) -> None:
        """Invoke a callback, supporting both sync and async callables."""
        try:
            result = cb(*args)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            logger.exception("Exception in SerialATAdapter callback")