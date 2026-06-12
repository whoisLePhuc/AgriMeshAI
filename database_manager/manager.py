"""DatabaseManager — consumes db_write events, records to SQLite, emits reading_recorded."""

import logging

from event_bus import EventBus, EventQueueManager
from database_manager.store import ReadingStore

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Central write coordinator. All SQLite writes go through here.
    
    Subscribes to EventQueueManager "db_write" events.
    After successful write, emits "reading_recorded" on EventBus.
    Read queries still go directly to ReadingStore.
    """

    def __init__(self, store: ReadingStore, event_queue: EventQueueManager, event_bus: EventBus):
        self._store = store
        self._event_bus = event_bus
        event_queue.subscribe("db_write", self._handle_write)

    async def _handle_write(self, device_id=None, sensor_id=None, value=None, unit=None, **kw):
        if device_id is None or sensor_id is None or value is None:
            logger.error(
                "db_write missing fields: device_id=%r sensor_id=%r value=%r",
                device_id, sensor_id, value,
            )
            return  # Không raise — retry vô ích

        try:
            await self._store.record(
                device_id=device_id,
                sensor_id=sensor_id,
                value=value,
                unit=unit or "",
            )
        except Exception as e:
            logger.error("db_write failed: %s", e)
            raise  # Retry hợp lý — chưa ghi được

        try:
            await self._event_bus.emit(
                "reading_recorded",
                device_id=device_id,
                sensor_id=sensor_id,
                value=value,
                unit=unit or "",
            )
        except Exception as e:
            logger.warning("reading_recorded emit failed: %s", e)
            # Không raise — data đã an toàn trong SQLite
