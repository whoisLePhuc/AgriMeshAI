# Database Manager — Thiết kế module

> **Module:** `database_manager/`
> **Phiên bản:** 1.0
> **Ngày:** 12/06/2026

---

## 1. Tổng quan

### Mục đích

`database_manager` là điểm phối hợp ghi trung tâm. Tất cả thao tác ghi SQLite đi qua module này.

- `DatabaseManager` subscribe event `db_write` từ `EventQueueManager`
- `ReadingStore` là file duy nhất nói chuyện trực tiếp với SQLite
- `retention.py` dọn dữ liệu cũ qua downsample và purge
- Read path đi tắt — `fleet.py` gọi `ReadingStore` trực tiếp

Module đảm bảo:
- Một điểm ghi duy nhất — không có race condition, không có multiple writer
- Phân tách lỗi rõ ràng — store fail thì retry, emit fail thì log
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
    ├── Validate fields
    │
    ├── ReadingStore.record() → SQLite (WAL)
    │   └── INSERT INTO readings (...)
    │
    └── Emit "reading_recorded" → EventBus
            │
            ▼
            rule_engine — evaluate alert
            notifier — send telegram

FleetTools — read path (đi tắt)
    │
    ▼
ReadingStore.get_history() — SELECT (read-only)
```

### Ràng buộc thiết kế

- Một điểm ghi duy nhất — chỉ `ReadingStore` mở write connection
- WAL mode — đọc và ghi song song không block nhau
- Phân tách lỗi — store fail raise để retry, emit fail log only
- Atomic retention — downsample và purge trong transaction
- Read path đi tắt — `fleet.py` không qua `DatabaseManager`

---

## 2. Kiến trúc

### Các thành phần

```
database_manager/
├── __init__.py      Export: DatabaseManager, ReadingStore
├── manager.py       DatabaseManager — event subscriber + coordinator
├── store.py         ReadingStore — async SQLite operations
└── retention.py    Downsample + purge (30d → hourly, 1y purge)
```

### Luồng dữ liệu chi tiết

```
EventQueueManager.publish("db_write", ...)
    │
    ▼
DatabaseManager._handle_write(envelope)
    │
    ├── Validate: device_id, sensor_id, value, timestamp
    │   └── Thiếu field → raise ValueError → retry qua EventQueue
    │
    ├── ReadingStore.record(device_id, sensor_id, value, unit, timestamp)
    │   ├── BEGIN TRANSACTION
    │   ├── INSERT INTO readings (...)
    │   └── COMMIT
    │       ├── Thành công → tiếp tục
    │       └── Fail → raise → retry qua EventQueue
    │
    └── EventBus.emit("reading_recorded", ...)
        ├── Thành công → done
        └── Fail → log warning (dữ liệu đã an toàn trong SQLite)
```

---

## 3. Các thành phần

### 3.1 manager.py — DatabaseManager

```python
class DatabaseManager:
    def __init__(self, event_queue: EventQueueManager, event_bus: EventBus, db_path: str)

    # Lifecycle
    async def start()     # Subscribe "db_write" vào EventQueueManager
    async def stop()      # Unsubscribe, đóng connection

    # Event handler
    async def _handle_write(self, **data)  # Validate → store → emit
```

**Công dụng:** Subscriber duy nhất cho event `db_write`. Phối hợp validate, ghi, và emit.

**Sử dụng bởi:** `main.py` khởi tạo và gọi `start()`. `EventQueueManager` gọi `_handle_write()` qua worker loop.

### 3.2 store.py — ReadingStore

```python
class ReadingStore:
    def __init__(self, db_path: str)

    # Write
    async def record(device_id, sensor_id, value, unit, timestamp) -> int  # rowid

    # Read (được FleetTools gọi trực tiếp)
    async def get_history(device_id, sensor_id, hours) -> list[dict]
    async def search_anomalies(device_id, sensor_id, threshold) -> list[dict]
    async def get_all_readings() -> list[dict]
    async def list_devices() -> list[dict]
```

**Công dụng:** File duy nhất mở kết nối SQLite và thực thi SQL. Tất cả read/write đi qua đây.

**Sử dụng bởi:**
- `DatabaseManager._handle_write()` — ghi dữ liệu mới
- `FleetTools` — đọc history, anomalies, list devices (đi tắt)

**Schema SQLite:**

```sql
CREATE TABLE readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    sensor_id TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT,
    timestamp TEXT NOT NULL,  -- ISO 8601
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_readings_device_sensor_time
ON readings(device_id, sensor_id, timestamp);
```

### 3.3 retention.py

```python
async def run_retention(db_path: str)
```

**Công dụng:** Dọn dữ liệu cũ qua 2 bước:
1. **Downsample** — dữ liệu > 30 ngày gộp thành giá trị trung bình mỗi giờ
2. **Purge** — xóa dữ liệu > 1 năm

**Đặc điểm:**
- Mở connection riêng, không dùng chung với `ReadingStore`
- Chạy trong transaction atomic
- Không block read path

---

## 4. Luồng ghi dữ liệu

### Từ event đến SQLite

```
EventQueueManager worker loop
    │
    ▼
_handle_write(device_id="s1", sensor_id="temp", value=32.5, unit="celsius", timestamp="2026-06-12T10:00:00")
    │
    ├── Validate
    │   ├── device_id: str ✓
    │   ├── sensor_id: str ✓
    │   ├── value: numeric ✓
    │   └── timestamp: ISO 8601 ✓
    │
    ├── ReadingStore.record(...)
    │   ├── INSERT INTO readings (...) VALUES (...)
    │   └── RETURNING id
    │       └── rowid = 15023
    │
    └── EventBus.emit("reading_recorded",
            device_id="s1",
            sensor_id="temp",
            value=32.5,
            rowid=15023
        )
```

**Thứ tự quan trọng:**
1. Validate trước — tránh ghi dữ liệu rác
2. Store trước, emit sau — đảm bảo dữ liệu an toàn trước khi thông báo
3. Emit fail không rollback store — dữ liệu đã lưu, chỉ notifier không nhận được

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
- Store fail: event ở lại queue, retry tự động
- Emit fail: event được xử lý xong, chỉ mất notification

### Code minh họa

```python
async def _handle_write(self, **data):
    # 1. Validate
    self._validate_fields(data)

    # 2. Store — nếu fail, raise để EventQueue retry
    rowid = await self.store.record(
        device_id=data["device_id"],
        sensor_id=data["sensor_id"],
        value=data["value"],
        unit=data.get("unit"),
        timestamp=data["timestamp"]
    )

    # 3. Emit — nếu fail, chỉ log (dữ liệu đã an toàn)
    try:
        await self.event_bus.emit("reading_recorded",
            device_id=data["device_id"],
            sensor_id=data["sensor_id"],
            value=data["value"],
            rowid=rowid
        )
    except Exception as e:
        logger.warning(f"emit reading_recorded failed: {e}")
        # Không raise — event đã xử lý xong
```

---

## 6. Retention và downsample

### Chiến lược dữ liệu theo thời gian

| Khoảng thời gian | Độ chi tiết | Hành động |
|------------------|-------------|-----------|
| 0-30 ngày | Raw (mỗi lần đọc) | Giữ nguyên |
| 30 ngày - 1 năm | Hourly (trung bình) | Downsample |
| > 1 năm | Xóa | Purge |

### Downsample chi tiết

```sql
-- Bước 1: Tạo bảng hourly mới (nếu chưa có)
CREATE TABLE IF NOT EXISTS readings_hourly (
    device_id TEXT NOT NULL,
    sensor_id TEXT NOT NULL,
    hour TEXT NOT NULL,  -- YYYY-MM-DD HH:00:00
    avg_value REAL NOT NULL,
    min_value REAL,
    max_value REAL,
    count INTEGER,
    PRIMARY KEY (device_id, sensor_id, hour)
);

-- Bước 2: Gộp dữ liệu cũ
INSERT OR REPLACE INTO readings_hourly
SELECT
    device_id,
    sensor_id,
    strftime('%Y-%m-%d %H:00:00', timestamp) as hour,
    AVG(value) as avg_value,
    MIN(value) as min_value,
    MAX(value) as max_value,
    COUNT(*) as count
FROM readings
WHERE timestamp < datetime('now', '-30 days')
GROUP BY device_id, sensor_id, hour;

-- Bước 3: Xóa raw data đã gộp
DELETE FROM readings
WHERE timestamp < datetime('now', '-30 days');
```

### Purge

```sql
-- Xóa dữ liệu > 1 năm (cả raw và hourly)
DELETE FROM readings WHERE timestamp < datetime('now', '-1 year');
DELETE FROM readings_hourly WHERE hour < datetime('now', '-1 year');
```

### Atomic execution

```python
async def run_retention(db_path: str):
    conn = await aiosqlite.connect(db_path)
    try:
        await conn.execute("BEGIN EXCLUSIVE")
        # Downsample
        await conn.execute("INSERT OR REPLACE INTO readings_hourly ...")
        await conn.execute("DELETE FROM readings WHERE ...")
        # Purge
        await conn.execute("DELETE FROM readings WHERE ...")
        await conn.execute("DELETE FROM readings_hourly WHERE ...")
        await conn.commit()
    except Exception:
        await conn.rollback()
        raise
    finally:
        await conn.close()
```

**Lưu ý:** `BEGIN EXCLUSIVE` để tránh xung đột với write path. Retention chạy đêm khuya khi load thấp.

---

## 7. Đường dẫn đọc trực tiếp

### Tại sao read path đi tắt

```
FleetTools.get_history()
    │
    ▼
ReadingStore.get_history()  ← Đi tắt, không qua DatabaseManager
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
| Validate | Có — field bắt buộc | Không — query param |
| Emit event | Có — `reading_recorded` | Không |
| Retry | Có — qua EventQueue | Không — fail trả về client |
| WAL mode | Write connection | Read connection (song song) |

---

## 8. WAL và SQLite

### Tại sao WAL mode

```python
await conn.execute("PRAGMA journal_mode=WAL")
```

| Chế độ | Đọc-ghi song song | Crash recovery | Hiệu suất |
|--------|-------------------|----------------|-----------|
| DELETE (mặc định) | Không — write block read | Tốt | Thấp |
| WAL | Có — read không block write | Tốt | Cao |

**Lợi ích cho AgriMeshAI:**
- `sensor_poller` ghi liên tục không block `FleetTools` đọc history
- `retention.py` chạy dọn dữ liệu không làm treo query
- Jetson Nano với SD card — WAL giảm fsync, tăng tuổi thọ thẻ nhớ

### WAL checkpoint

```python
# Tự động checkpoint khi WAL file > 1000 pages
await conn.execute("PRAGMA wal_autocheckpoint=1000")
```

**Lưu ý:** WAL file (`*.wal`) tồn tại song song với DB chính. Nếu app crash, WAL chứa transaction chưa commit có thể recover.

---

## 9. Giới hạn

- **Chỉ SQLite** — không hỗ trợ PostgreSQL, MySQL, InfluxDB
- **Một write connection** — không có connection pool
- **Retention chưa có lịch tự động** — cần gọi thủ công hoặc qua daemon loop
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
store = ReadingStore("data/readings.db")

manager = DatabaseManager(queue, bus, "data/readings.db")
await manager.start()  # Subscribe "db_write"
```

### Ghi dữ liệu qua event

```python
# sensor_poller publish event
await queue.publish("db_write",
    device_id="sensor_01",
    sensor_id="temperature",
    value=32.5,
    unit="celsius",
    timestamp="2026-06-12T10:00:00"
)

# DatabaseManager tự động xử lý:
# 1. Validate fields
# 2. Store.record() → SQLite
# 3. Emit "reading_recorded"
```

### Đọc history (đi tắt)

```python
# FleetTools gọi trực tiếp — không qua DatabaseManager
history = await store.get_history(
    device_id="sensor_01",
    sensor_id="temperature",
    hours=24
)
# [{"value": 32.5, "timestamp": "2026-06-12T10:00:00"}, ...]
```

### Chạy retention

```python
from database_manager.retention import run_retention

# Chạy một lần (thường gọi từ daemon loop đêm khuya)
await run_retention("data/readings.db")
# Downsample > 30d → hourly, purge > 1y
```

### Xử lý lỗi store

```python
# Nếu SQLite bị lock, store.record() raise
# EventQueueManager tự động retry 3×
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
- Time-series downsample pattern — influxdata.com
