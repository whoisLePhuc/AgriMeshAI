"""EventQueueManager — async queued event dispatcher with DLQ and retry."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class EventEnvelope:
    event: str
    data: dict
    timestamp: float = field(default_factory=time.time)


@dataclass
class DeadLetter:
    id: str
    event: str
    data: dict
    handler_name: str
    handler: Callable | None = None   # reference để retry
    failed_at: datetime = field(default_factory=datetime.now)
    last_error: str = ""
    attempts: int = 0


class EventQueueManager:
    def __init__(self, maxsize: int = 100, max_retries: int = 3, handler_timeout: float = 10.0):
        self._queue: asyncio.Queue[EventEnvelope] = asyncio.Queue(maxsize)
        self._handlers: dict[str, list[Callable]] = {}
        self._dead_letters: list[DeadLetter] = []
        self._max_retries = max_retries
        self._handler_timeout = handler_timeout
        self._worker_task: asyncio.Task | None = None
        self._running = False
        self._stats = {"published": 0, "processed": 0, "failed": 0, "dlq": 0}

    def subscribe(self, event: str, handler: Callable) -> None:
        self._handlers.setdefault(event, []).append(handler)

    async def publish(self, event: str, **data: Any) -> bool:
        try:
            self._queue.put_nowait(EventEnvelope(event=event, data=data))
            self._stats["published"] += 1
            return True
        except asyncio.QueueFull:
            self._stats["failed"] += 1
            return False

    async def start(self) -> None:
        self._running = True
        self._worker_task = asyncio.create_task(self._worker_loop())

    async def stop(self) -> None:
        self._running = False
        try:
            await asyncio.wait_for(self._queue.join(), timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
        if self._worker_task:
            self._worker_task.cancel()
            try: await self._worker_task
            except: pass

    async def _worker_loop(self) -> None:
        while self._running:
            try:
                envelope = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                await self._dispatch(envelope)
                self._queue.task_done()
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    async def _dispatch(self, envelope: EventEnvelope):
        for handler in self._handlers.get(envelope.event, []):
            name = handler.__name__ if hasattr(handler, '__name__') else str(handler)
            await self._deliver(envelope, handler, name)

    async def _deliver(self, envelope: EventEnvelope, handler, handler_name: str, max_retries: int = 0) -> bool:
        """Deliver event to handler with retry. Returns True if success, False if DLQ."""
        if max_retries is None:
            max_retries = self._max_retries
        last_error = ""
        for attempt in range(max_retries + 1):
            try:
                await asyncio.wait_for(handler(**envelope.data), timeout=self._handler_timeout)
                self._stats["processed"] += 1
                return True
            except asyncio.TimeoutError:
                last_error = f"Timeout after {self._handler_timeout}s"
            except asyncio.CancelledError:
                raise
            except Exception as e:
                last_error = str(e)
            if attempt < max_retries:
                await asyncio.sleep(2 ** attempt)
        # All attempts failed → DLQ
        self._stats["dlq"] += 1
        self._dead_letters.append(DeadLetter(
            id=str(uuid.uuid4()),
            event=envelope.event,
            data=envelope.data,
            handler_name=handler_name,
            handler=handler,
            failed_at=datetime.now(),
            last_error=last_error,
            attempts=max_retries + 1,
        ))
        return False

    def get_dlq(self) -> list[DeadLetter]:
        return list(self._dead_letters)

    async def retry_dlq(self, max_retries: int | None = None) -> int:
        """Retry all events in DLQ. Returns count of successfully retried."""
        remaining, succeeded = [], 0
        for dl in self._dead_letters:
            if dl.handler is None:
                remaining.append(dl)
                continue
            env = EventEnvelope(event=dl.event, data=dl.data)
            ok = await self._deliver(env, dl.handler, dl.handler_name, max_retries or self._max_retries)
            if ok:
                succeeded += 1
            else:
                # Retry failed again — keep in DLQ
                remaining.append(dl)
        self._dead_letters = remaining
        return succeeded

    async def retry_event(self, event_id: str, max_retries: int | None = None) -> bool:
        """Retry a specific event from DLQ by ID."""
        for i, dl in enumerate(self._dead_letters):
            if dl.id == event_id:
                if dl.handler is None:
                    return False
                env = EventEnvelope(event=dl.event, data=dl.data)
                ok = await self._deliver(env, dl.handler, dl.handler_name, max_retries or self._max_retries)
                if ok:
                    self._dead_letters.pop(i)
                return ok
        return False

    def clear_dlq(self) -> None:
        self._dead_letters.clear()

    @property
    def stats(self) -> dict:
        return {
            **self._stats,
            "queue_size": self._queue.qsize(),
            "dlq_size": len(self._dead_letters),
        }
