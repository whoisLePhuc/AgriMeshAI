# Sensor Poller — Thiết kế module

> **Module:** `sensor_poller/`
> **Phiên bản:** 1.0
> **Ngày:** 12/06/2026

---

## 1. Tổng quan

### Mục đích

`sensor_poller` là vòng lặp polling cảm biến chạy nền. Module này đọc giá trị cảm biến theo khoảng thời gian cấu hình, sau đó publish sự kiện `db_write` đến `EventQueueManager`.

- Không ghi trực tiếp vào SQLite — chỉ publish event
- Mỗi thiết bị chạy task riêng, không block lẫn nhau
- Jitter ngẫu nhiên tránh storm request
- Lọc chỉ công cụ số, bỏ qua công cụ có tham số

Module hoạt động như một producer: đọc dữ liệu từ phần cứng, đẩy vào queue để `database_manager` xử lý ghi sau.

### Vị trí trong hệ thống

```
TOML profile ([recording] enabled, poll_interval_ms)
    │
    ▼
sensor_poller — run_recorder()
    │
    ├── _recordable_routes() → lọc công cụ số, bỏ tham số
    │
    └── Tạo 1 task asyncio per device
            │
            ▼
            _poll_device() — vòng lặp với jitter
                │
                ├── Đọc cảm biến qua DeviceManager
                │
                └── Publish "db_write" → EventQueueManager
                        │
                        ▼
                        database_manager — _handle_write()
                            │
                            ▼
                            SQLite (WAL mode)
```

### Ràng buộc thiết kế

- Không ghi DB trực tiếp — chỉ publish event, decouple khỏi SQLite
- Mỗi device một task — slow device không block fleet
- Jitter ngẫu nhiên — tránh tất cả device đồng loạt request
- Chỉ công cụ số — bỏ qua công cụ có tham số (ví dụ: `control_actuator`)
- Graceful shutdown — drain queue trước khi hủy task
- Config qua TOML — không hardcode interval

---

## 2. Kiến trúc

### Các thành phần

```
sensor_poller/
├── __init__.py      Export: run_recorder
└── poller.py        Vòng lặp polling và publish event
```

### Luồng dữ liệu chi tiết

```
Config (TOML profile)
    │
    ▼
run_recorder(system_manager, event_queue, stop_event)
    │
    ▼
_recordable_routes(system_manager)
    │
    ├── Lọc tool có outputSchema numeric
    ├── Bỏ tool có required arguments
    └── Trả list[(device_id, sensor_id, tool_name)]
    │
    ▼
Tạo N task asyncio (1 per device)
    │
    ▼
_poll_device(device_id, routes, interval_ms, stop_event)
    │
    ├── while not stop_event.is_set()
    │   │
    │   ├── Jitter: sleep(random.uniform(0, interval_ms * 0.1))
    │   │
    │   ├── Gọi từng tool trong routes
    │   │   ├── read_sensor → value, unit
    │   │   └── ...
    │   │
    │   ├── Publish "db_write" event
    │   │   device_id, sensor_id, value, unit, timestamp
    │   │
    │   └── Sleep(interval_ms - jitter)
    │
    └── Khi stop_event.set() → thoát vòng lặp
```

---

## 3. Các thành phần

### 3.1 poller.py — run_recorder

```python
async def run_recorder(
    system_manager: SystemManager,
    event_queue: EventQueueManager,
    stop_event: asyncio.Event,
    poll_interval_ms: int = 5000
)
```

**Công dụng:** Điểm vào duy nhất. Tạo task nền cho mỗi thiết bị, mỗi task chạy vòng lặp polling độc lập.

**Sử dụng bởi:** `mcp_server.run_daemon_loops()` khởi chạy khi chế độ daemon.

**Tham số:**

| Tham số | Mặc định | Ý nghĩa |
|---------|----------|---------|
| `system_manager` | bắt buộc | Để gọi tool đọc cảm biến |
| `event_queue` | bắt buộc | EventQueueManager để publish `db_write` |
| `stop_event` | bắt buộc | asyncio.Event dừng tất cả task |
| `poll_interval_ms` | 5000 | Khoảng thời gian giữa 2 lần đọc (ms) |

### 3.2 _poll_device

```python
async def _poll_device(
    device_id: str,
    routes: list[tuple[str, str, str]],  # (sensor_id, tool_name, unit)
    interval_ms: int,
    stop_event: asyncio.Event,
    event_queue: EventQueueManager
)
```

**Công dụng:** Vòng lặp polling cho một thiết bị cụ thể. Chạy độc lập với các thiết bị khác.

**Luồng hoạt động:**

```python
while not stop_event.is_set():
    # Jitter ngẫu nhiên 0-10% interval
    jitter = random.uniform(0, interval_ms * 0.001 * 0.1)
    await asyncio.sleep(jitter)

    for sensor_id, tool_name, unit in routes:
        try:
            value = await system_manager.call_tool(tool_name, {})
            await event_queue.publish("db_write",
                device_id=device_id,
                sensor_id=sensor_id,
                value=value,
                unit=unit,
                timestamp=datetime.now().isoformat()
            )
        except Exception:
            # Log và tiếp tục — không dừng vòng lặp
            pass

    # Sleep phần còn lại của interval
    await asyncio.sleep(interval_ms * 0.001 - jitter)
```

### 3.3 _recordable_routes

```python
def _recordable_routes(system_manager: SystemManager) -> list[tuple[str, str, str, str]]:
    # Trả về: [(device_id, sensor_id, tool_name, unit), ...]
```

**Công dụng:** Lọc danh sách công cụ để chỉ giữ lại công cụ có thể ghi nhận.

**Quy tắc lọc:**

| Tiêu chí | Hành vi |
|----------|---------|
| outputSchema là numeric | Giữ lại — có thể ghi vào DB |
| outputSchema không phải numeric | Bỏ qua — không ghi được |
| Có required arguments | Bỏ qua — không thể gọi không tham số |
| Không có arguments | Giữ lại — gọi trực tiếp |

**Ví dụ:**

```
read_sensor (outputSchema: number, no args)     → GIỮ
get_battery (outputSchema: number, no args)     → GIỮ
control_actuator (has args: {duration: int})    → BỎ
read_config (outputSchema: string)              → BỎ
```

---

## 4. Cơ chế polling

### Một task per device

```
Device A (sensor_01, sensor_02)
    └── Task A — _poll_device("device_A", [...], 5000, ...)
            ├── Đọc sensor_01
            ├── Đọc sensor_02
            └── Sleep 5s

Device B (sensor_03)
    └── Task B — _poll_device("device_B", [...], 5000, ...)
            ├── Đọc sensor_03
            └── Sleep 5s
```

**Lợi ích:**
- Device A chậm không block Device B
- Mỗi task có jitter riêng, không đồng bộ hóa
- Một device offline chỉ làm task đó fail, không ảnh hưởng fleet

### Publish event thay vì ghi trực tiếp

```python
# KHÔNG làm thế này:
await sqlite.execute("INSERT ...")  # ❌ Block nếu SQLite bận

# Mà làm thế này:
await event_queue.publish("db_write", ...)  # ✅ Non-blocking, decoupled
```

**Lý do decouple:**
- SQLite write có thể chậm (WAL checkpoint, disk I/O)
- Polling không nên bị block bởi DB
- EventQueueManager có retry, DLQ — robust hơn ghi trực tiếp

---

## 5. Lọc công cụ ghi nhận

### Tại sao cần lọc

Không phải công cụ nào cũng trả về giá trị đo lường. Một số công cụ:
- Trả về string (config, status) — không ghi vào time-series DB
- Cần tham số (control_actuator) — không thể gọi tự động
- Trả về object phức tạp — không có schema rõ ràng

### Logic lọc

```python
def _recordable_routes(system_manager):
    routes = []
    for device in system_manager.devices:
        for tool in device.tools:
            schema = tool.outputSchema
            # Chỉ nhận numeric
            if schema.get("type") not in ("number", "integer"):
                continue
            # Bỏ qua nếu có required arguments
            if tool.inputSchema.get("required"):
                continue
            routes.append((device.id, tool.sensor_id, tool.name, tool.unit))
    return routes
```

**Kết quả:** Danh sách công cụ "an toàn" để gọi không tham số và ghi kết quả số.

---

## 6. Jitter và đồng bộ

### Jitter ngẫu nhiên

```python
jitter = random.uniform(0, interval_ms * 0.001 * 0.1)  # 0-10% interval
await asyncio.sleep(jitter)
```

**Tác dụng:**
- Tránh tất cả thiết bị đồng loạt request cùng lúc
- Giảm burst load lên DeviceManager và ESP32
- Phân tán traffic theo thời gian

### Ví dụ jitter

| Interval | Jitter range | Thực tế |
|----------|--------------|---------|
| 5000 ms | 0-500 ms | Device A sleep 5.0s, Device B sleep 5.3s, Device C sleep 5.1s |
| 30000 ms | 0-3000 ms | Các task phân tán trong 3 giây |

**Lưu ý:** Jitter chỉ áp dụng trước mỗi lần đọc. Sleep chính vẫn đúng interval.

---

## 7. Graceful shutdown

### Cơ chế dừng sạch

```
SIGINT / SIGTERM → stop_event.set()
    │
    ▼
Tất cả _poll_device() kiểm tra stop_event
    │
    ├── Vòng lặp hiện tại hoàn thành
    │   ├── Đọc xong cảm biến đang dở
    │   ├── Publish event cuối cùng
    │   └── Thoát while loop
    │
    └── run_recorder() đợi tất cả task
            │
            ▼
            asyncio.gather(*tasks, return_exceptions=True)
```

```python
async def run_recorder(..., stop_event):
    tasks = []
    for device_id, routes in devices:
        task = asyncio.create_task(
            _poll_device(device_id, routes, interval_ms, stop_event, queue)
        )
        tasks.append(task)

    # Đợi stop_event, sau đó đợi task hoàn thành vòng lặp hiện tại
    await stop_event.wait()

    # Cancel và đợi cleanup
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
```

**Đảm bảo:**
- Không mất event đang publish
- Queue được drain trước khi thoát
- Không có zombie task chạy ngầm

---

## 8. Cấu hình

### TOML profile

```toml
[recording]
enabled = true
poll_interval_ms = 5000
```

| Tham số | Mặc định | Ý nghĩa |
|---------|----------|---------|
| `enabled` | true | Bật/tắt recorder nền |
| `poll_interval_ms` | 5000 | Khoảng thời gian đọc cảm biến (ms) |

### Thay đổi interval

```toml
# Đọc mỗi 30 giây — tiết kiệm pin, ít dữ liệu
poll_interval_ms = 30000

# Đọc mỗi 1 giây — nhiều dữ liệu, tốn pin
poll_interval_ms = 1000
```

**Khuyến nghị:**
- Cảm biến nhiệt độ/độ ẩm: 30-60 giây
- Cảm biến độ ẩm đất: 5-15 phút
- Cảm biến pin: 1 giờ

---

## 9. Giới hạn

- **Không có backpressure** — nếu EventQueueManager đầy, event bị drop
- **Không lưu trữ local** — nếu DB offline, event mất (không có buffer local)
- **Jitter cố định 10%** — không configurable qua TOML
- **Chỉ hỗ trợ numeric** — không ghi string, boolean, object
- **Không có adaptive interval** — không tự điều chỉnh khi phát hiện thay đổi nhanh
- **Một interval cho tất cả device** — không có per-device interval
- **Không batch publish** — mỗi sensor là một event riêng, không gộp

---

## 10. Ví dụ

### Khởi chạy recorder

```python
from sensor_poller import run_recorder
from event_bus import EventQueueManager
import asyncio

queue = EventQueueManager()
await queue.start()

stop_event = asyncio.Event()

# Chạy nền
await run_recorder(
    system_manager=system_manager,
    event_queue=queue,
    stop_event=stop_event,
    poll_interval_ms=5000
)
```

### Dừng recorder

```python
# Signal shutdown
stop_event.set()

# run_recorder() sẽ tự động drain queue và thoát
```

### Config TOML

```toml
# config/profile.toml
[recording]
enabled = true
poll_interval_ms = 10000  # 10 giây
```

### Xem route đã lọc

```python
routes = _recordable_routes(system_manager)
for device_id, sensor_id, tool_name, unit in routes:
    print(f"{device_id}/{sensor_id}: {tool_name} ({unit})")
# Output:
# sensor_01/temperature: read_sensor (celsius)
# sensor_01/humidity: read_sensor (percent)
# sensor_02/battery: get_battery (percent)
```

---

## Tham khảo

- asyncio.Task — docs.python.org
- asyncio.Event — docs.python.org
- random.uniform — docs.python.org
- TOML specification — toml.io
