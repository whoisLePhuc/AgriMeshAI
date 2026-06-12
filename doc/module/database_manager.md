# Database Manager — Thiết kế module

> **Module:** `database_manager/`
> **Phiên bản:** 2.0
> **Ngày:** 12/06/2026

---

## 1. Tổng quan

### Mục đích

`database_manager` là điểm phối hợp ghi trung tâm. Tất cả thao tác ghi SQLite đi qua module này.

- `DatabaseManager` subscribe event `db_write` từ `EventQueueManager`
- `ReadingStore` là class duy nhất nói chuyện trực tiếp với SQLite (cả read và write)
- `retention.py` dọn dữ liệu cũ qua downsample và purge
- Read path đi tắt — `FleetTools` gọi `ReadingStore` trực tiếp

Module đảm bảo:
- Một điểm ghi duy nhất — không có race condition, không có multiple writer
- Phân tách lỗi rõ ràng — store fail thì raise để retry, emit fail thì log
- Dữ liệu an toàn — WAL mode, atomic transaction

### Vị trí trong hệ thống

```
sensor_poller — publish "db_write"
    │
    ▼
EventQueueManager
    │
    ▼
DatabaseManager — _handle_write()
    │
    ├── Validate fields (None check)
    │
    ├── ReadingStore.record() → SQLite (WAL)
    │   └── INSERT INTO readings (timestamp, device_id, sensor_id, value, unit)
    │
    └── Emit "reading_recorded" → EventBus
            │
            ▼
            rule_engine — evaluate alert
            notifier — send telegram

FleetTools — read path (đi tắt)
    │
    ▼
ReadingStore.get_history() / get_all_latest() — SELECT (read-only)
```

### Ràng buộc thiết kế

- Một điểm ghi duy nhất — chỉ `DatabaseManager` gọi `ReadingStore.record()`
- WAL mode — đọc và ghi song song không block nhau
- Phân tách lỗi — store fail raise để retry, emit fail log only
- Atomic retention — downsample và purge trong transaction, dùng connection riêng
- Read path đi tắt — `FleetTools` không qua `DatabaseManager`

---

## 2. Kiến trúc

### Các thành phần

```
database_manager/
├── __init__.py      Export: DatabaseManager, ReadingStore, run_cleanup, AnomalyResult, Reading
├── manager.py       DatabaseManager — event subscriber + coordinator
├── store.py         ReadingStore — async SQLite operations
└── retention.py     run_cleanup — downsample + purge
```

### Luồng dữ liệu chi tiết

```
EventQueueManager.publish("db_write", device_id, sensor_id, value, unit)
    │
    ▼
DatabaseManager._handle_write(device_id, sensor_id, value, unit)
    │
    ├── Validate: device_id, sensor_id, value != None
    │   └── Thiếu field → log error, return (không raise — retry vô ích)
    │
    ├── ReadingStore.record(device_id, sensor_id, value, unit, timestamp=None)
    │   ├── INSERT INTO readings (timestamp, device_id, sensor_id, value, unit)
    │   ├── COMMIT
    │   └── Return Reading(timestamp, device_id, sensor_id, value, unit)
    │       ├── Thành công → tiếp tục
    │       └── Fail → raise Exception → EventQueue retry (max 3×)
    │
    └── EventBus.emit("reading_recorded", device_id, sensor_id, value, unit)
        ├── Thành công → done
        └── Fail → log warning (dữ liệu đã an toàn trong SQLite)
```

---

## 3. Các thành phần

### 3.1 manager.py — DatabaseManager

```python
class DatabaseManager:
    def __init__(self, store: ReadingStore, event_queue: EventQueueManager, event_bus: EventBus)
        # Subscribe "db_write" ngay trong __init__

    # Event handler (internal)
    async def _handle_write(self, device_id=None, sensor_id=None, value=None, unit=None, **kw)
        # Validate → store.record() → event_bus.emit()
```

**Công dụng:** Subscriber duy nhất cho event `db_write`. Phối hợp validate, ghi, và emit.

**Không có lifecycle methods** (`start`/`stop`) — `DatabaseManager` hoạt động ngay sau `__init__`.

**Sử dụng bởi:** `SystemManager` khởi tạo và inject store, event_queue, event_bus.

### 3.2 store.py — ReadingStore

```python
class Reading(BaseModel):
    timestamp: float       # epoch seconds
    device_id: str
    sensor_id: str
    value: float
    unit: str

class AnomalyResult(BaseModel):
    device_id: str
    sensor_id: str
    current_value: float
    mean: float
    stddev: float
    sigma_distance: float
    unit: str

class ReadingStore:
    def __init__(self, db_path: str | Path = "data/agrimesh.db")

    # Lifecycle
    async def init()                  # Open SQLite, create tables, WAL mode
    async def close()                 # Close connection

    # Write
    async def record(device_id, sensor_id, value, unit, timestamp=None) -> Reading
    async def record_batch(readings: list[tuple]) -> int  # list of (device_id, sensor_id, value, unit, timestamp|None)

    # Read (được FleetTools gọi trực tiếp)
    async def get_history(device_id, sensor_id, start=None, end=None, limit=1000) -> list[Reading]
    async def get_latest(device_id, sensor_id) -> Reading | None
    async def get_all_latest() -> list[Reading]      # latest reading per device/sensor
    async def search_anomalies(threshold_sigma=2.0, baseline_days=30) -> list[AnomalyResult]

    # Retention support
    async def open_retention_conn() -> tuple[Connection, bool]  # Separate connection for retention
```

**Công dụng:** Class duy nhất mở kết nối SQLite và thực thi SQL. Tất cả read/write đi qua đây.

**Sử dụng bởi:**
- `DatabaseManager._handle_write()` — ghi dữ liệu mới
- `FleetTools` — đọc history, anomalies, all_latest (đi tắt)
- `SystemManager.start()` — gọi `init()` khi khởi động

**Schema SQLite thực tế:**

```sql
CREATE TABLE IF NOT EXISTS readings (
    timestamp   REAL    NOT NULL,  -- epoch seconds
    device_id   TEXT    NOT NULL,
    sensor_id   TEXT    NOT NULL,
    value       REAL    NOT NULL,
    unit        TEXT    NOT NULL,
    downsampled INTEGER NOT NULL DEFAULT 0  -- 0 = raw, 1 = hourly average
);

CREATE INDEX IF NOT EXISTS idx_readings_device_sensor_time
    ON readings (device_id, sensor_id, timestamp);

CREATE INDEX IF NOT EXISTS idx_readings_downsampled
    ON readings (downsampled, timestamp);
```

Không có `id` hoặc `created_at` — mỗi dòng là một reading, timestamp làm key chính.

### 3.3 retention.py — run_cleanup

```python
async def run_cleanup(
    store: ReadingStore,
    full_res_days: int = 30,
    keep_downsampled_days: int = 365,
) -> dict[str, int]:
    # Returns {"downsampled": int, "purged": int}
```

**Công dụng:** Dọn dữ liệu cũ qua 2 bước:
1. **Downsample** — dữ liệu > 30 ngày gộp thành giá trị trung bình mỗi giờ (flag `downsampled = 1`)
2. **Purge** — xóa dữ liệu downsampled > 1 năm

**Đặc điểm:**
- Mở connection riêng qua `store.open_retention_conn()`, không dùng chung với main connection
- Chạy trong transaction `BEGIN IMMEDIATE`
- Dùng flag `downsampled` trong cùng bảng `readings`, không tạo bảng riêng

---

## 4. Luồng ghi dữ liệu

### Từ event đến SQLite

```
sensor_poller publish "db_write" → EventQueueManager
    │
    ▼
DatabaseManager._handle_write(device_id="s1", sensor_id="temp", value=32.5, unit="celsius")
    │
    ├── Validate
    │   ├── device_id != None ✓
    │   ├── sensor_id != None ✓
    │   └── value != None ✓
    │
    ├── ReadingStore.record(device_id="s1", sensor_id="temp", value=32.5, unit="celsius")
    │   ├── INSERT INTO readings (timestamp, device_id, sensor_id, value, unit)
    │   │   VALUES (1718000000.0, 's1', 'temp', 32.5, 'celsius')
    │   └── Return Reading(timestamp=1718000000.0, device_id='s1', ...)
    │
    └── EventBus.emit("reading_recorded",
            device_id="s1",
            sensor_id="temp",
            value=32.5,
            unit="celsius"
        )
```

**Thứ tự quan trọng:**
1. Validate trước — tránh gọi store với dữ liệu null
2. Store trước, emit sau — đảm bảo dữ liệu an toàn trước khi thông báo
3. Emit fail không raise — dữ liệu đã lưu, chỉ notifier không nhận được

---

## 5. Phân tách lỗi store và emit

### Tại sao cần phân tách

```
_handle_write()
    │
    ├── ReadingStore.record() → FAIL
    │   └── Raise exception
    │       └── EventQueueManager retry 3×
    │           └── Hết retry → DLQ
    │               └── Dữ liệu CHƯA vào SQLite
    │
    └── EventBus.emit() → FAIL
        └── Log warning
            └── Dữ liệu ĐÃ an toàn trong SQLite
```

**Hai loại lỗi khác nhau:**

| Lỗi | Hành vi | Lý do |
|-----|---------|-------|
| Store fail | Raise → retry qua EventQueue | Dữ liệu chưa an toàn, phải thử lại |
| Emit fail | Log warning, không raise | Dữ liệu đã lưu, chỉ notifier không nhận được |

**Kết quả:**
- Store fail: event ở lại queue, retry tự động (max 3 lần, backoff exponential)
- Emit fail: event được xử lý xong, chỉ mất notification

### Code thực tế

```python
async def _handle_write(self, device_id=None, sensor_id=None, value=None, unit=None, **kw):
    # 1. Validate — None fields → log error, không retry
    if device_id is None or sensor_id is None or value is None:
        logger.error("db_write missing fields: ...")
        return  # Không raise — retry vô ích

    # 2. Store — nếu fail, raise để EventQueue retry
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

    # 3. Emit — nếu fail, chỉ log (dữ liệu đã an toàn)
    try:
        await self._event_bus.emit("reading_recorded",
            device_id=device_id,
            sensor_id=sensor_id,
            value=value,
            unit=unit or "",
        )
    except Exception as e:
        logger.warning("reading_recorded emit failed: %s", e)
        # Không raise — dữ liệu đã an toàn trong SQLite
```

---

## 6. Retention và downsample

### Chiến lược dữ liệu theo thời gian

| Khoảng thời gian | Độ chi tiết | downsampled flag |
|------------------|-------------|------------------|
| 0-30 ngày | Raw (mỗi lần đọc) | `0` |
| 30 ngày - 1 năm | Hourly (trung bình) | `1` |
| > 1 năm | Xóa | — |

### Downsample chi tiết

Dùng chung bảng `readings`, phân biệt bằng cột `downsampled`:

```sql
-- Bước 1: Chèn hourly averages với downsampled = 1
INSERT INTO readings (timestamp, device_id, sensor_id, value, unit, downsampled)
SELECT
    CAST(CAST(timestamp / 3600 AS INTEGER) * 3600 AS REAL),  -- đầu giờ
    device_id,
    sensor_id,
    AVG(value),
    unit,
    1
FROM readings
WHERE timestamp < ?                    -- cutoff = now - 30 days
  AND downsampled = 0                  -- chỉ xử lý raw data
GROUP BY device_id, sensor_id, unit, CAST(timestamp / 3600 AS INTEGER);

-- Bước 2: Xóa raw data đã được downsample
DELETE FROM readings WHERE timestamp < ? AND downsampled = 0;
```

### Purge

```sql
-- Xóa tất cả downsampled readings > 1 năm
DELETE FROM readings WHERE timestamp < ? AND downsampled = 1;
```

### Atomic execution

```python
async def run_cleanup(store: ReadingStore, full_res_days=30, keep_downsampled_days=365):
    db, should_close = await store.open_retention_conn()
    try:
        await db.execute("BEGIN IMMEDIATE")
        # Downsample
        await db.execute("INSERT INTO readings (...) SELECT ... WHERE ...")
        await db.execute("DELETE FROM readings WHERE ... AND downsampled = 0")
        # Purge
        await db.execute("DELETE FROM readings WHERE ... AND downsampled = 1")
        await db.commit()
    except BaseException:
        await asyncio.shield(db.rollback())
        raise
    finally:
        if should_close:
            await db.close()
```

**Lưu ý:** Retention dùng connection riêng (không phải main connection của `ReadingStore`) để không block read/write path. Giao dịch `BEGIN IMMEDIATE` để tránh deadlock.

---

## 7. Đường dẫn đọc trực tiếp

### Tại sao read path đi tắt

```
FleetTools.get_history() / get_all_latest()
    │
    ▼
ReadingStore.get_history() / get_all_latest()  ← Đi tắt, không qua DatabaseManager
    │
    ▼
SQLite — SELECT (read-only)
```

**Lý do:**
- `DatabaseManager` chỉ phối hợp ghi — không cần tham gia read
- Read-only query không cần validate, không cần emit event
- Giảm latency — bỏ qua một lớp abstraction
- `ReadingStore` đã là async, fleet gọi trực tiếp không block

### So sánh read vs write path

| | Write path | Read path |
|---|------------|-----------|
| Đi qua | EventQueue → DatabaseManager → ReadingStore | FleetTools → ReadingStore |
| Validate | Có — None check | Không — query param |
| Emit event | Có — `reading_recorded` | Không |
| Retry | Có — qua EventQueue (3×) | Không — fail trả về client |
| WAL mode | Write connection | Read connection (song song) |

---

## 8. WAL và SQLite

### Tại sao WAL mode

```python
await self._db.execute("PRAGMA journal_mode=WAL")
```

| Chế độ | Đọc-ghi song song | Crash recovery | Hiệu suất |
|--------|-------------------|----------------|-----------|
| DELETE (mặc định) | Không — write block read | Tốt | Thấp |
| WAL | Có — read không block write | Tốt | Cao |

**Lợi ích cho AgriMeshAI:**
- `sensor_poller` ghi liên tục không block `FleetTools` đọc history
- `retention.py` chạy dọn dữ liệu không làm treo query
- Jetson Nano với SD card — WAL giảm fsync, tăng tuổi thọ thẻ nhớ

### Lưu ý WAL

WAL file (`*.db-wal`, `*.db-shm`) tồn tại song song với DB chính. Nếu app crash, WAL chứa transaction chưa commit có thể recover khi mở lại database.

---

## 9. Giới hạn

- **Chỉ SQLite** — không hỗ trợ PostgreSQL, MySQL, InfluxDB
- **Một write connection** — không có connection pool
- **Retention chạy trong daemon loop** — 6 giờ một lần, không có lịch tùy chỉnh
- **Không có partition** — bảng readings lớn có thể chậm sau 1 năm
- **Downsample chỉ có hourly** — không có daily, weekly aggregation
- **Không có backup** — chỉ một file DB, chưa có replicate hoặc export
- **Read path không có cache** — mỗi query đều hit SQLite
- **Không có migration** — schema thay đổi cần script thủ công

---

## 10. Ví dụ

### Khởi tạo DatabaseManager

```python
from database_manager import DatabaseManager, ReadingStore
from event_bus import EventQueueManager, EventBus

queue = EventQueueManager()
bus = EventBus()
store = ReadingStore("data/agrimesh.db")

manager = DatabaseManager(store, queue, bus)
# Đã subscribe "db_write" ngay trong __init__
```

### Ghi dữ liệu qua event

```python
# sensor_poller publish event
await queue.publish("db_write",
    device_id="sensor_01",
    sensor_id="temperature",
    value=32.5,
    unit="celsius",
)

# DatabaseManager tự động xử lý:
# 1. Validate fields
# 2. store.record() → SQLite
# 3. Emit "reading_recorded"
```

### Đọc history (đi tắt)

```python
# FleetTools gọi trực tiếp — không qua DatabaseManager
history = await store.get_history(
    device_id="sensor_01",
    sensor_id="temperature",
    start=time.time() - 86400,  # 24 hours ago
    limit=100,
)
# [Reading(timestamp=..., device_id='sensor_01', sensor_id='temperature', value=32.5, unit='celsius'), ...]
```

### Chạy retention

```python
from database_manager.retention import run_cleanup

# Chạy một lần (thường gọi từ daemon loop mỗi 6h)
result = await run_cleanup(store, full_res_days=30, keep_downsampled_days=365)
# result = {"downsampled": 1500, "purged": 200}
```

### Xử lý lỗi store

```python
# Nếu SQLite bị lỗi, store.record() raise
# EventQueueManager tự động retry 3 lần với backoff (1s, 2s, 4s)
# Sau 3 lần → DLQ

dlq = queue.get_dlq()
for item in dlq:
    print(f"{item.id}: {item.event} → {item.last_error}")
# 550e8400: db_write → database is locked
```

---

## Tham khảo

- SQLite WAL mode — sqlite.org/wal.html
- aiosqlite — github.com/omnilib/aiosqlite
- Event-driven architecture — docs.python.org/asyncio
