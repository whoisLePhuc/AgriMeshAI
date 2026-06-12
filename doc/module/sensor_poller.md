# Sensor Poller — Thiết kế module

> **Module:** `sensor_poller/`
> **Phiên bản:** 2.0
> **Ngày:** 12/06/2026

---

## 1. Tổng quan

### Mục đích

`sensor_poller` là vòng lặp polling cảm biến chạy nền. Module này đọc giá trị cảm biến theo khoảng thời gian cấu hình trong TOML profile, sau đó publish sự kiện `db_write` đến `EventQueueManager`.

- Không ghi trực tiếp vào SQLite — chỉ publish event
- Mỗi thiết bị chạy task riêng, không block lẫn nhau
- Jitter ngẫu nhiên (0 đến full interval) tránh burst khi khởi động
- Lọc chỉ công cụ numeric có command, bỏ qua công cụ có tham số bắt buộc
- Per-device interval đọc từ TOML profile (`[recording] poll_interval_ms`)

Module hoạt động như một producer: đọc dữ liệu từ phần cứng qua `DeviceManager`, đẩy vào queue để `DatabaseManager` xử lý ghi sau.

### Vị trí trong hệ thống

```
TOML profile ([recording] enabled, poll_interval_ms)
    │
    ▼
sensor_poller — run_recorder()
    │
    ├── _recordable_routes() → lọc công cụ numeric + có command
    │
    └── Tạo 1 task asyncio per device
            │
            ▼
            _poll_device() — vòng lặp với jitter
                │
                ├── Đọc cảm biến qua DeviceManager.call_tool()
                │
                └── Publish "db_write" → EventQueueManager
                        │
                        ▼
                        DatabaseManager — _handle_write()
                            │
                            ▼
                            SQLite (WAL mode)
```

### Ràng buộc thiết kế

- Không ghi DB trực tiếp — chỉ publish event, decouple khỏi SQLite
- Mỗi device một task — slow device không block fleet
- Jitter ngẫu nhiên (0-interval) khi start — tránh tất cả device đồng loạt request
- Chỉ công cụ numeric có command — bỏ qua công cụ handler-only hoặc có tham số bắt buộc
- Interval theo từng device — config trong TOML, không hardcode
- Graceful shutdown — drain queue trước khi hủy task

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
Config (TOML profile — [recording] section)
    │
    ▼
run_recorder(device_manager, event_queue, stop_event)
    │
    ▼
_recordable_routes(device_manager) → dict[str, list[ToolRoute]]
    │
    ├── Lọc tool có returns.type numeric (float, number, int, integer)
    ├── Lọc tool có command (không phải handler-only)
    ├── Bỏ tool có required params
    ├── Bỏ device có recording.enabled = false
    └── Group theo device name
    │
    ▼
Tạo N task asyncio (1 per device)
    │
    ▼
_poll_device(device_name, routes, device_manager, event_queue, interval_s, stop_event)
    │
    ├── Jitter: await asyncio.sleep(random.uniform(0, interval_s))
    │
    ├── while not stop_event.is_set()
    │   │
    │   ├── Gọi từng tool route
    │   │   ├── device_manager.call_tool(namespaced_name, {})
    │   │   ├── Parse kết quả → float
    │   │   └── Publish "db_write" (device_id, sensor_id, value, unit)
    │   │
    │   └── await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
    │       ├── stop_event → break
    │       └── timeout → poll lại
    │
    └── Khi stop_event.set() → asyncio.gather() hoàn thành
```

### Khác biệt với version 1.0

- `run_recorder` nhận `DeviceManager` (không phải `SystemManager`)
- Interval đọc từ TOML profile mỗi device, không phải tham số function
- Jitter 0-interval (không phải 0-10%)
- Event `db_write` không gửi timestamp (để `DatabaseManager` tự gán)

---

## 3. Các thành phần

### 3.1 poller.py — run_recorder

```python
async def run_recorder(
    device_manager: DeviceManager,
    event_queue: EventQueueManager,
    stop_event: asyncio.Event,
) -> None
```

**Công dụng:** Điểm vào duy nhất. Tạo task nền cho mỗi thiết bị, mỗi task chạy vòng lặp polling độc lập.

**Tham số:**

| Tham số | Kiểu | Ý nghĩa |
|---------|------|---------|
| `device_manager` | `DeviceManager` | Để gọi tool đọc cảm biến và lấy routes |
| `event_queue` | `EventQueueManager` | Publish `db_write` |
| `stop_event` | `asyncio.Event` | Dừng tất cả task |

**Lưu ý:** `run_recorder` hiện tại **không** được gọi từ `mcp_server.run_daemon_loops()`. Module này có thể chạy độc lập nếu cần background recording. Interval được lấy từ `device.model.recording.poll_interval_ms` trong TOML profile.

### 3.2 _poll_device

```python
async def _poll_device(
    device_name: str,
    routes: list[ToolRoute],
    device_manager: DeviceManager,
    event_queue: EventQueueManager,
    interval_s: float,
    stop_event: asyncio.Event,
) -> None
```

**Công dụng:** Vòng lặp polling cho một thiết bị cụ thể. Chạy độc lập với các thiết bị khác.

**Luồng hoạt động:**

```python
# Jitter ngẫu nhiên 0-interval khi bắt đầu (tránh burst)
await asyncio.sleep(random.uniform(0, interval_s))

while not stop_event.is_set():
    for route in routes:
        try:
            namespaced = f"{device_name}.{route.tool_name}"
            result = await device_manager.call_tool(namespaced, {})

            value = float(result.data)
            unit = route.returns.unit or ""

            await event_queue.publish("db_write",
                device_id=device_name,
                sensor_id=route.tool_name,
                value=value,
                unit=unit,
            )
        except Exception:
            logger.warning(...)  # Log và tiếp tục — không dừng vòng lặp

    # Chờ interval, thoát sớm nếu stop
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
        break
    except asyncio.TimeoutError:
        pass  # interval elapsed, poll lại
```

### 3.3 _recordable_routes

```python
def _recordable_routes(device_manager: DeviceManager) -> dict[str, list[ToolRoute]]
    # Returns: {device_name: [ToolRoute, ...]}
```

**Công dụng:** Lọc danh sách routes từ `DeviceManager` để chỉ giữ lại công cụ có thể poll tự động.

**Quy tắc lọc:**

| Tiêu chí | Hành vi |
|----------|---------|
| `recording.enabled = false` | Bỏ qua toàn bộ device |
| `route.command is None` | Bỏ qua (handler-only, không có command text) |
| `returns.type` là numeric | Giữ lại — có thể ghi vào DB |
| `returns.type` không phải numeric | Bỏ qua — không ghi được |
| Có required params | Bỏ qua — không thể gọi với `{}` |

**Ví dụ kết quả:**

```python
{
    "farm_sensor": [
        ToolRoute(device=..., tool_name="get_moisture", command="READ", returns=ToolReturns(type="float", unit="percent")),
        ToolRoute(device=..., tool_name="get_temperature", command="READ", returns=ToolReturns(type="float", unit="celsius")),
    ],
    "mqtt_sensor": [
        ToolRoute(device=..., tool_name="get_temperature", command="READ_TEMP", returns=ToolReturns(type="float", unit="celsius")),
        ToolRoute(device=..., tool_name="get_humidity", command="READ_HUMID", returns=ToolReturns(type="float", unit="percent")),
    ],
}
```

---

## 4. Cơ chế polling

### Một task per device

```
Device A (farm_sensor — 2 sensors)
    └── Task A — _poll_device("farm_sensor", [get_moisture, get_temperature], interval=5.0s)
            ├── Jitter 0-5.0s
            ├── Đọc moisture → float
            ├── Đọc temperature → float
            └── Sleep 5s (hoặc stop)

Device B (mqtt_sensor — 2 sensors)
    └── Task B — _poll_device("mqtt_sensor", [get_temperature, get_humidity], interval=5.0s)
            ├── Jitter 0-5.0s
            ├── Đọc temperature → float
            ├── Đọc humidity → float
            └── Sleep 5s (hoặc stop)
```

**Lợi ích:**
- Device A chậm (serial timeout) không block Device B
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

Không phải công cụ nào cũng trả về giá trị đo lường có thể poll tự động:

- Công cụ trả về string (config, status) — không ghi vào time-series DB
- Công cụ cần tham số (control_actuator) — không thể gọi tự động
- Công cụ handler-only (không có command) — không thể gửi qua adapter

### Logic lọc thực tế

```python
_NUMERIC_TYPES = {"float", "number", "int", "integer"}

def _recordable_routes(device_manager):
    by_device = {}
    for namespaced_name in device_manager.route_names:
        route = device_manager.get_route(namespaced_name)
        if route is None:
            continue
        if not route.device.model.recording.enabled:
            continue
        if route.command is None:          # handler-only
            continue
        if not route.returns or route.returns.type not in _NUMERIC_TYPES:
            continue
        by_device.setdefault(route.device.name, []).append(route)
    return by_device
```

**Kết quả:** `dict[device_name, list[ToolRoute]]` — chỉ chứa công cụ "an toàn" để gọi không tham số và ghi kết quả số.

---

## 6. Jitter và đồng bộ

### Jitter ngẫu nhiên

```python
# Jitter 0 đến full interval — chỉ áp dụng lúc start
await asyncio.sleep(random.uniform(0, interval_s))
```

**Tác dụng:**
- Tránh tất cả thiết bị đồng loạt request khi `run_recorder` khởi động
- Giảm burst load lên DeviceManager và ESP32
- Chỉ jitter một lần lúc start, các vòng lặp sau đồng bộ theo interval

### Ví dụ jitter

| Interval | Jitter range | Thực tế |
|----------|--------------|---------|
| 5000 ms | 0-5000 ms | Device A start ngay, Device B start sau 3.2s, Device C start sau 1.1s |
| 30000 ms | 0-30000 ms | Các task phân tán đều trong 30 giây |

---

## 7. Graceful shutdown

### Cơ chế dừng sạch

```
stop_event.set()
    │
    ▼
Từng _poll_device() kiểm tra ở đầu vòng lặp mới
    │
    ├── Đang trong sleep → timeout → check stop → thoát
    ├── Đang đọc cảm biến → đọc xong → check stop → thoát
    └── Đang publish event → publish xong → check stop → thoát
    │
    ▼
run_recorder() đợi tất cả task trong asyncio.gather()
    │
    ▼
finally block: cancel task còn sống, đợi hoàn thành
```

```python
try:
    await asyncio.gather(*tasks)
except BaseException:
    stop_event.set()
    raise
finally:
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
```

**Đảm bảo:**
- Không mất event đang publish
- Task được cancel sạch, không zombie
- `BaseException` catch bao gồm cả `CancelledError`

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
| `enabled` | `true` | Bật/tắt recorder nền cho device này |
| `poll_interval_ms` | `5000` | Khoảng thời gian giữa 2 lần poll (ms) |

Interval có thể khác nhau giữa các device:

```toml
# Device A — đọc nhanh
[recording]
poll_interval_ms = 5000

# Device B — tiết kiệm pin
[recording]
poll_interval_ms = 60000
```

**Khuyến nghị:**
- Cảm biến nhiệt độ/độ ẩm: 30-60 giây
- Cảm biến độ ẩm đất: 5-15 phút
- Cảm biến pin: 1 giờ

---

## 9. Giới hạn

- **Không có backpressure** — nếu EventQueueManager đầy, event bị drop (`QueueFull`)
- **Không lưu trữ local** — nếu DB offline, event mất (không có buffer local)
- **Chỉ hỗ trợ numeric** — không ghi string, boolean, object
- **Không có adaptive interval** — không tự điều chỉnh khi phát hiện thay đổi nhanh
- **Không batch publish** — mỗi sensor là một event riêng, không gộp
- **Không tích hợp trong daemon** — `run_recorder` không được gọi bởi `mcp_server.run_daemon_loops()`, cần chạy thủ công nếu cần
- **Không kiểm tra backpressure** — publish event mà không kiểm tra queue còn chỗ hay không

---

## 10. Ví dụ

### Khởi chạy recorder

```python
from sensor_poller import run_recorder
from event_bus import EventQueueManager
from device_manager.manager import DeviceManager
import asyncio

queue = EventQueueManager()
await queue.start()

stop_event = asyncio.Event()

# Chạy nền — interval đọc từ TOML profile mỗi device
recorder_task = asyncio.create_task(
    run_recorder(
        device_manager=device_manager,
        event_queue=queue,
        stop_event=stop_event,
    )
)
```

### Dừng recorder

```python
# Signal shutdown
stop_event.set()

# Các task tự động thoát, run_recorder cleanup
await recorder_task
```

### Config TOML

```toml
# device_profiles/templates/serial_sensor.toml
[device]
name = "serial_sensor"

[recording]
enabled = true
poll_interval_ms = 10000  # 10 giây
```

### Xem route đã lọc

```python
routes = _recordable_routes(device_manager)
for device_name, tool_routes in routes.items():
    for r in tool_routes:
        namespaced = f"{device_name}.{r.tool_name}"
        returns = f"{r.returns.type} ({r.returns.unit})" if r.returns else "?"
        print(f"{namespaced} → {returns}")
# farm_sensor.get_moisture → float (percent)
# farm_sensor.get_temperature → float (celsius)
# mqtt_sensor.get_temperature → float (celsius)
# mqtt_sensor.get_humidity → float (percent)
```

---

## Tham khảo

- asyncio.Task — docs.python.org
- asyncio.Event — docs.python.org
- random.uniform — docs.python.org
- TOML specification — toml.io
- DeviceManager — doc/module/device_manager.md
- DatabaseManager — doc/module/database_manager.md
