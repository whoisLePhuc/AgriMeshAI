# MCP Server — Thiết kế module

> **Module:** `mcp_server/`
> **Phiên bản:** 1.0
> **Ngày:** 12/06/2026

---

## 1. Tổng quan

### Mục đích

`mcp_server` là adapter giao thức MCP (Model Context Protocol). Module này chỉ làm nhiệm vụ dịch giao thức, không chứa logic nghiệp vụ.

- Nhận yêu cầu từ AI agent qua MCP
- Định tuyến đến đúng công cụ (fleet hoặc device)
- Trả kết quả với JSON Schema xác thực
- Chạy ở 2 chế độ: agent (stdio) và daemon (HTTP + nền)

Module nhận `SystemManager` qua dependency injection. Không tự khởi tạo store hay manager nội bộ.

### Hai chế độ hoạt động

Module có 2 chế độ, mỗi chế độ phục vụ một mục đích khác nhau:

#### Chế độ agent (stdio)

```
AI agent (Ollama + LangChain)
    │
    ▼ stdin/stdout (MCP protocol)
    │
mcp_server — serve_stdio()
    │
    ▼ handle_call_tool()
    │
FleetTools / DeviceManager
```

- Giao tiếp trực tiếp với AI agent qua stdio
- Mỗi tool call là một request/response đồng bộ
- **Dùng cho:** Tương tác người dùng, truy vấn thủ công
- **Không dùng cho:** Ghi dữ liệu nền liên tục

#### Chế độ daemon (HTTP + nền)

```
HTTP client (Web UI, Telegram, script)
    │
    ▼ HTTP POST /call_tool
    │
mcp_server — serve_http()
    │
    ▼ handle_call_tool()
    │
FleetTools / DeviceManager
```

- Cung cấp endpoint HTTP độc lập
- Chạy `run_daemon_loops()` gồm recorder, retention, missing_data, HTTP server
- **Dùng cho:** Vận hành 24/7, ghi dữ liệu tự động, dọn dẹp lịch sử
- **Không dùng cho:** Tương tác trực tiếp với AI agent

#### Tại sao cần cả hai?

| Tình huống | Nếu chỉ có stdio | Nếu chỉ có HTTP |
|------------|------------------|-----------------|
| AI agent hỏi "nhiệt độ hiện tại" | Ổn — phản hồi ngay | Không tự nhiên — cần polling |
| Ghi dữ liệu cảm biến mỗi 5 phút | Không thể — stdio cần agent | Ổn — daemon tự chạy |
| Web UI gọi tool từ xa | Không thể — stdio local only | Ổn — HTTP endpoint |
| Dọn dữ liệu cũ (retention) | Không thể — cần chạy nền | Ổn — daemon loop |

**Kết luận:** stdio cho tương tác AI agent. HTTP cho vận hành độc lập. Hai chế độ bổ sung cho nhau, không thay thế.

### Vị trí trong hệ thống

```
AI agent (LangChain + Ollama)
    │
    ▼ MCP stdio
    │
mcp_server — serve_stdio()
    │
    ├── handle_list_tools() → liệt kê tất cả công cụ
    │
    └── handle_call_tool() → định tuyến
            │
            ├── fleet.* → FleetTools (fleet.py)
            │   ├── list_devices
            │   ├── get_all_readings
            │   ├── get_history
            │   └── search_anomalies
            │
            └── còn lại → DeviceManager (system_manager)
                ├── read_sensor
                ├── control_actuator
                └── ...
```

### Ràng buộc thiết kế

- Zero domain logic — chỉ dịch giao thức, không xử lý nghiệp vụ
- Dependency injection — nhận SystemManager từ caller, không tự khởi tạo
- Timeout 30 giây — mỗi tool call bị giới hạn qua `asyncio.wait_for`
- outputSchema — tất cả công cụ fleet trả về JSON Schema xác thực
- Graceful shutdown — SIGINT/SIGTERM dừng sạch qua `stop_event`
- Structured error — lỗi trả về kèm danh sách công cụ khả dụng

---

## 2. Kiến trúc

### Các thành phần

```
mcp_server/
├── __init__.py      Export: AgriMeshAIServer
├── server.py        AgriMeshAIServer — MCP protocol handler
├── fleet.py         FleetTools — 4 công cụ tổng hợp
└── scanner.py       Device discovery (tùy chọn)
```

### Luồng dữ liệu chi tiết

```
Client (AI agent / HTTP / Telegram)
    │
    ▼
serve_stdio() hoặc serve_http()
    │
    ▼
handle_list_tools()
    │
    ├── fleet.py → FleetTools.list_tools() → 4 tools với outputSchema
    │
    └── system_manager → device_tools() → N tools từ DeviceManager
    │
    ▼
handle_call_tool(name, arguments)
    │
    ├── name.startswith("fleet.") → FleetTools.call(name, args)
    │   ├── list_devices → ReadingStore
    │   ├── get_all_readings → ReadingStore
    │   ├── get_history → ReadingStore
    │   └── search_anomalies → ReadingStore
    │
    └── còn lại → system_manager.call_tool(name, args)
        ├── read_sensor → DeviceManager → ESP32
        ├── control_actuator → DeviceManager → ESP32
        └── ...
    │
    ▼
asyncio.wait_for(tool_call, timeout=30.0)
    │
    ├── thành công → JSON result với outputSchema
    └── timeout → structured error + available tools list
```

---

## 3. Các thành phần

### 3.1 server.py — AgriMeshAIServer

```python
class AgriMeshAIServer:
    def __init__(self, system_manager: SystemManager)

    # MCP lifecycle
    async def handle_list_tools() -> list[Tool]
    async def handle_call_tool(name: str, arguments: dict) -> list[TextContent]

    # Transport
    async def serve_stdio()
    async def serve_http(host: str, port: int)

    # Daemon mode
    async def run_daemon_loops()
    async def run_daemon()
```

**Công dụng:** Điểm vào duy nhất cho giao thức MCP. Tất cả request từ AI agent hoặc HTTP đều đi qua `handle_call_tool()`.

**Sử dụng bởi:** `main.py` khởi tạo và chọn chế độ agent hoặc daemon.

### 3.2 fleet.py — FleetTools

```python
class FleetTools:
    def __init__(self, reading_store: ReadingStore)

    # 4 công cụ tổng hợp
    async def list_devices() -> list[dict]
    async def get_all_readings() -> list[dict]
    async def get_history(device_id: str, hours: int) -> list[dict]
    async def search_anomalies(
        device_id: str | None,
        sensor_id: str | None,
        threshold: float
    ) -> list[dict]
```

**Công dụng:** Cung cấp 4 công cụ cấp fleet để AI agent truy vấn dữ liệu tổng hợp. Tất cả đều có `outputSchema` JSON Schema.

**Sử dụng bởi:** `server.py` định tuyến `fleet.*` prefix đến đây.

**outputSchema cho 4 công cụ:**

| Công cụ | outputSchema | Mô tả |
|---------|--------------|-------|
| `list_devices` | `{"type": "array", "items": {"type": "object", "properties": {"device_id": {"type": "string"}, "status": {"type": "string"}}}}` | Danh sách thiết bị và trạng thái |
| `get_all_readings` | `{"type": "array", "items": {"type": "object", "properties": {"device_id": {"type": "string"}, "sensor_id": {"type": "string"}, "value": {"type": "number"}, "unit": {"type": "string"}, "timestamp": {"type": "string"}}}}` | Tất cả giá trị cảm biến mới nhất |
| `get_history` | `{"type": "array", "items": {"type": "object", "properties": {"value": {"type": "number"}, "timestamp": {"type": "string"}}}}` | Lịch sử giá trị theo giờ |
| `search_anomalies` | `{"type": "array", "items": {"type": "object", "properties": {"device_id": {"type": "string"}, "sensor_id": {"type": "string"}, "value": {"type": "number"}, "expected": {"type": "number"}, "deviation": {"type": "number"}, "timestamp": {"type": "string"}}}}` | Các điểm bất thường vượt ngưỡng |

### 3.3 scanner.py

```python
# Device discovery — quét và đăng ký thiết bị mới
async def scan_devices() -> list[dict]
```

**Công dụng:** Phát hiện thiết bị mới trong mạng. Hiện tại là stub, có thể mở rộng thành BLE scan hoặc LoRa discovery.

---

## 4. Chế độ hoạt động

### Agent mode — serve_stdio()

```python
# main.py — khởi tạo agent
server = AgriMeshAIServer(system_manager)
await server.serve_stdio()
```

- Đọc JSON-RPC từ stdin, ghi kết quả ra stdout
- Mỗi dòng là một MCP message
- Kết thúc khi stdin đóng hoặc nhận SIGINT

### Daemon mode — run_daemon_loops()

```python
# main.py — khởi tạo daemon
server = AgriMeshAIServer(system_manager)
await server.run_daemon_loops()
```

`run_daemon_loops()` khởi chạy 4 task nền song song:

```
run_daemon_loops()
    │
    ├── recorder_loop()      ← sensor_poller — đọc cảm biến định kỳ
    │
    ├── retention_loop()     ← recorder.retention — dọn dữ liệu cũ
    │
    ├── missing_data_loop()  ← kiểm tra thiết bị mất tín hiệu
    │
    └── http_server()        ← serve_http() — endpoint HTTP
```

- 4 task chạy độc lập, một task fail không kéo theo task khác
- Tất cả dùng chung `stop_event` để dừng đồng bộ

---

## 5. Routing công cụ và xử lý lỗi

### Định tuyến tool call

```python
async def handle_call_tool(self, name: str, arguments: dict):
    if name.startswith("fleet."):
        # fleet.list_devices → FleetTools.list_devices()
        result = await self.fleet_tools.call(name, arguments)
    else:
        # read_sensor → DeviceManager.read_sensor()
        result = await self.system_manager.call_tool(name, arguments)
    return [TextContent(type="text", text=json.dumps(result))]
```

**Quy tắc:**
- Prefix `fleet.` → `FleetTools`
- Không có prefix hoặc prefix khác → `DeviceManager` qua `SystemManager`

### Xử lý lỗi có cấu trúc

```python
try:
    result = await asyncio.wait_for(
        self._execute_tool(name, arguments),
        timeout=30.0
    )
except asyncio.TimeoutError:
    return self._error_response(
        f"Tool '{name}' timed out after 30s",
        available_tools=self._list_tool_names()
    )
except Exception as e:
    return self._error_response(
        f"Tool '{name}' failed: {e}",
        available_tools=self._list_tool_names()
    )
```

**Error response format:**

```json
{
    "error": "Tool 'fleet.get_history' timed out after 30s",
    "available_tools": [
        "fleet.list_devices",
        "fleet.get_all_readings",
        "fleet.get_history",
        "fleet.search_anomalies",
        "read_sensor",
        "control_actuator"
    ]
}
```

**Lý do kèm danh sách công cụ:** AI agent có thể tự động chọn công cụ khác khi một công cụ fail.

---

## 6. Graceful shutdown

### Cơ chế dừng sạch

```
SIGINT / SIGTERM
    │
    ▼
stop_event.set()
    │
    ├── serve_stdio() → thoát vòng lặp đọc stdin
    │
    ├── serve_http() → đóng server socket
    │
    ├── recorder_loop() → hủy task, drain queue
    │
    ├── retention_loop() → hủy task
    │
    └── missing_data_loop() → hủy task
```

```python
import signal

stop_event = asyncio.Event()

for sig in (signal.SIGINT, signal.SIGTERM):
    loop.add_signal_handler(sig, stop_event.set)

# Tất cả loop kiểm tra stop_event.is_set() mỗi vòng
while not stop_event.is_set():
    await asyncio.sleep(1)
```

**Đảm bảo:**
- Không mất request đang xử lý
- Queue được drain trước khi thoát
- SQLite connection đóng sạch

---

## 7. Timeout và bảo vệ

### Timeout mỗi tool call

```python
result = await asyncio.wait_for(
    self.fleet_tools.get_history(device_id="s1", hours=24),
    timeout=30.0
)
```

| Tình huống | Hành vi |
|------------|---------|
| Tool hoàn thành trong 30s | Trả kết quả bình thường |
| Tool chậm hơn 30s | `TimeoutError` → structured error với available tools |
| Tool crash | `Exception` → structured error với traceback (dev mode) |
| Nhiều tool call đồng thời | Mỗi call có timeout riêng, không ảnh hưởng lẫn nhau |

**Tại sao 30 giây?**
- Đủ lâu cho truy vấn SQLite phức tạp (history 30 ngày)
- Đủ ngắn để AI agent không bị treo vô hạn
- Cân bằng giữa reliability và responsiveness

---

## 8. outputSchema

### Tại sao cần outputSchema

AI agent (LLM) cần biết cấu trúc dữ liệu trả về để:
- Hiển thị kết quả đúng định dạng
- Trích xuất field cụ thể cho bước reasoning tiếp theo
- Validate trước khi dùng làm input cho tool khác

### Ví dụ outputSchema

```python
# fleet.py — get_all_readings
Tool(
    name="fleet.get_all_readings",
    description="Lấy tất cả giá trị cảm biến mới nhất",
    inputSchema={"type": "object", "properties": {}},
    outputSchema={
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string"},
                "sensor_id": {"type": "string"},
                "value": {"type": "number"},
                "unit": {"type": "string"},
                "timestamp": {"type": "string", "format": "date-time"}
            },
            "required": ["device_id", "sensor_id", "value", "timestamp"]
        }
    }
)
```

**Lưu ý:** outputSchema là metadata. Server không validate kết quả thực tế — đó là trách nhiệm của FleetTools và ReadingStore.

---

## 9. Giới hạn

- **Chỉ hỗ trợ JSON-RPC 2.0** — không hỗ trợ binary MCP
- **stdio blocking** — agent mode không thể xử lý nhiều request song song
- **HTTP không có auth** — cần thêm API key hoặc JWT nếu expose ra ngoài
- **scanner.py chưa hoàn thiện** — chỉ là stub, chưa có discovery thực sự
- **Không có rate limiting** — HTTP endpoint có thể bị flood
- **FleetTools chỉ đọc** — 4 công cụ đều là read-only, không ghi dữ liệu
- **Không có caching** — mỗi tool call đều query SQLite trực tiếp

---

## 10. Ví dụ

### Khởi tạo server

```python
from mcp_server import AgriMeshAIServer
from device_manager import SystemManager

system_manager = SystemManager()
await system_manager.initialize()

server = AgriMeshAIServer(system_manager)
```

### Chế độ agent (stdio)

```python
# main.py — chạy với AI agent
await server.serve_stdio()
# Đọc từ stdin, ghi ra stdout
# Kết thúc khi stdin đóng
```

### Chế độ daemon (HTTP)

```python
# main.py — chạy 24/7
await server.run_daemon_loops()
# Khởi chạy 4 task nền + HTTP server
```

### Gọi tool qua HTTP

```bash
curl -X POST http://localhost:8374/call_tool \
  -H "Content-Type: application/json" \
  -d '{
    "name": "fleet.get_history",
    "arguments": {
      "device_id": "sensor_01",
      "hours": 24
    }
  }'
```

### Xử lý lỗi timeout

```python
try:
    result = await server.handle_call_tool(
        "fleet.search_anomalies",
        {"threshold": 3.0}
    )
except asyncio.TimeoutError:
    # Trả về AI agent với danh sách công cụ khác
    print("Query quá chậm, thử fleet.get_all_readings thay thế")
```

---

## Tham khảo

- Model Context Protocol — modelcontextprotocol.io
- JSON-RPC 2.0 Specification — jsonrpc.org
- asyncio.wait_for — docs.python.org
- Starlette HTTP server — starlette.io
