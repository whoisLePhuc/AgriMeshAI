# MCP Server — Thiết kế module

> **Module:** `mcp_server/`
> **Phiên bản:** 2.0
> **Ngày:** 12/06/2026

---

## 1. Tổng quan

### Mục đích

`mcp_server` là module giao thức MCP (Model Context Protocol). Module này chỉ làm nhiệm vụ dịch giao thức, không chứa logic nghiệp vụ.

- Nhận yêu cầu từ AI agent qua MCP (stdio hoặc HTTP)
- Định tuyến đến đúng công cụ (fleet hoặc device)
- Trả kết quả với JSON Schema xác thực
- Chạy ở 2 chế độ: agent (stdio) và daemon (HTTP + nền)

Module nhận `SystemManager` qua dependency injection. Không tự khởi tạo store hay manager nội bộ.

### Hai chế độ hoạt động

#### Chế độ agent (stdio)

```
AI agent (Ollama + edge-agent)
    │
    ▼ stdin/stdout (MCP protocol)
    │
mcp_server — serve_stdio()
    │
    ▼ handle_call_tool()
    │
SystemManager → FleetTools / DeviceManager
```

- Giao tiếp trực tiếp với AI agent qua stdio
- Mỗi tool call là một request/response đồng bộ
- **Dùng cho:** Tương tác người dùng, truy vấn thủ công

#### Chế độ daemon (HTTP + retention + missing-data)

```
HTTP client (Web UI, curl, MCP client)
    │
    ▼ GET/POST/DELETE /mcp (Streamable HTTP)
    │
mcp_server — serve_http()
    │
    ▼ handle_call_tool()
    │
SystemManager → FleetTools / DeviceManager
```

- Cung cấp endpoint HTTP theo chuẩn MCP Streamable HTTP
- Chạy `run_daemon_loops()` gồm retention, missing_data, HTTP server
- **Dùng cho:** Vận hành 24/7, dọn dẹp lịch sử

> **Lưu ý:** Daemon mode hiện tại **không** chạy sensor_poller (background recorder). Sensor polling được thiết kế để chạy độc lập nếu cần.

#### Tại sao cần cả hai?

| Tình huống | Nếu chỉ có stdio | Nếu chỉ có HTTP |
|------------|------------------|-----------------|
| AI agent hỏi "nhiệt độ hiện tại" | Ổn — phản hồi ngay | Không tự nhiên — cần polling |
| Web UI gọi tool từ xa | Không thể — stdio local only | Ổn — HTTP endpoint |
| Dọn dữ liệu cũ (retention) | Không thể — cần chạy nền | Ổn — daemon loop |
| Kiểm tra thiết bị mất tín hiệu | Không thể — cần chạy nền | Ổn — daemon loop |

**Kết luận:** stdio cho tương tác AI agent. HTTP cho vận hành độc lập. Hai chế độ bổ sung cho nhau, không thay thế.

### Vị trí trong hệ thống

```
AI agent (edge-agent + Ollama)
    │
    ▼ MCP stdio
    │
mcp_server — serve_stdio()
    │
    ├── handle_list_tools() → SystemManager → device tools + fleet tools
    │
    └── handle_call_tool() → routing
            │
            ├── fleet.* → SystemManager → FleetTools (fleet.py)
            │   ├── list_devices
            │   ├── get_all_readings
            │   ├── get_history
            │   └── search_anomalies
            │
            └── còn lại → SystemManager → DeviceManager
                ├── read_sensor
                ├── get_moisture
                └── ...
```

### Ràng buộc thiết kế

- Zero domain logic — chỉ dịch giao thức, không xử lý nghiệp vụ
- Dependency injection — nhận `SystemManager` từ caller, không tự khởi tạo
- Timeout 30 giây — mỗi tool call bị giới hạn qua `asyncio.wait_for`
- outputSchema — tất cả công cụ fleet có outputSchema JSON
- Graceful shutdown — SIGINT/SIGTERM dừng sạch qua `stop_event` + `http_server.should_exit`
- Structured error — lỗi trả về kèm thông báo lỗi

---

## 2. Kiến trúc

### Các thành phần

```
mcp_server/
├── __init__.py      Export: AgriMeshAIServer
├── server.py        AgriMeshAIServer — MCP protocol handler
└── fleet.py         FleetTools — 4 công cụ tổng hợp
```

Chỉ có **3 file**. Không có `scanner.py`, không có `adapters/` hay `gateway/` subdirectory.

### Luồng dữ liệu chi tiết

```
Client (AI agent stdin / HTTP client)
    │
    ▼
serve_stdio() hoặc serve_http()
    │
    ▼
handle_list_tools()
    │
    └── system.list_tools() → device_manager.tools + fleet.tools
    │
    ▼
handle_call_tool(name, arguments)
    │
    ├── name.startswith("fleet.") → system.call_tool(name, args)
    │   ├── FleetTools.call(name, args)
    │   │   ├── list_devices → device_manager
    │   │   ├── get_all_readings → ReadingStore
    │   │   ├── get_history → ReadingStore
    │   │   └── search_anomalies → ReadingStore
    │   └── Trả về dict kết quả
    │
    └── còn lại → system.call_tool(name, args)
        ├── DeviceManager.call_tool(name, args)
        └── Adapter (serial/mock/mqtt) → hardware
    │
    ▼
asyncio.wait_for(tool_call, timeout=30.0)
    │
    ├── thành công → CallToolResult với TextContent
    └── timeout → isError = True + message
```

---

## 3. Các thành phần

### 3.1 server.py — AgriMeshAIServer

```python
class AgriMeshAIServer:
    def __init__(self, system: SystemManager)

    # Tool handlers
    def handle_list_tools() -> list[Tool]
    async def handle_call_tool(name: str, arguments: dict | None) -> CallToolResult

    # Transport — stdio
    async def serve_stdio()
    async def run_stdio()               # start system → serve stdio → stop system

    # Transport — HTTP
    async def serve_http(host="127.0.0.1", port=8374)

    # Daemon mode
    async def run_daemon_loops(host="127.0.0.1", port=8374)
    async def run_daemon(host="127.0.0.1", port=8374)  # start → daemon loops → stop
```

**Công dụng:** Điểm vào duy nhất cho giao thức MCP. Tất cả request từ AI agent hoặc HTTP đều đi qua `handle_call_tool()`.

**Sử dụng bởi:** `main.py` khởi tạo và chọn chế độ agent hoặc daemon.

### 3.2 fleet.py — FleetTools

```python
class FleetTools:
    def __init__(self, device_manager: DeviceManager, store: ReadingStore)
        # device_manager dùng cho list_devices
        # store dùng cho get_all_readings, get_history, search_anomalies

    @property
    def tools(self) -> list[Tool]  # 4 MCP Tool definitions với outputSchema

    async def call(self, tool_name: str, arguments: dict) -> dict
        # Dispatch to handler, returns JSON-serializable dict

    # Internal handlers (all receive arguments: dict)
    async def _list_devices(self, arguments) -> dict
    async def _get_all_readings(self, arguments) -> dict
    async def _get_history(self, arguments) -> dict
    async def _search_anomalies(self, arguments) -> dict
```

**Công dụng:** Cung cấp 4 công cụ cấp fleet để AI agent truy vấn dữ liệu tổng hợp. Tất cả đều có `outputSchema` JSON.

**Sử dụng bởi:** `SystemManager.call_tool()` định tuyến `fleet.*` prefix đến `FleetTools.call()`.

**4 công cụ và outputSchema thực tế:**

| Công cụ | outputSchema (rút gọn) |
|---------|----------------------|
| `fleet.list_devices` | `{devices: [{name, description, protocol, connected, healthy, error, tools}], count}` |
| `fleet.get_all_readings` | `{readings: [{timestamp, device_id, sensor_id, value, unit}], count}` |
| `fleet.get_history` | `{device_id, sensor_id, readings: [{...}], count, hours_requested}` |
| `fleet.search_anomalies` | `{anomalies: [{device_id, sensor_id, current_value, mean, stddev, sigma_distance, unit}], count, threshold_sigma, baseline_days}` |

---

## 4. Chế độ hoạt động

### Agent mode — serve_stdio()

```python
# main.py — python main.py agent
server = AgriMeshAIServer(system_manager)
await server.run_stdio()
# → system.start() → serve_stdio() → system.stop()
```

- Sử dụng MCP stdio transport — đọc JSON-RPC từ stdin, ghi ra stdout
- Một MCP client (AI agent) kết nối trực tiếp
- Kết thúc khi stdin đóng hoặc nhận SIGINT

### Daemon mode — run_daemon_loops()

```python
# main.py — python main.py daemon
server = AgriMeshAIServer(system_manager)
await server.run_daemon()
# → system.start() → run_daemon_loops() → system.stop()
```

`run_daemon_loops()` khởi chạy 3 task nền song song:

```
run_daemon_loops(host, port)
    │
    ├── _run_retention_loop()     ← database_manager.retention — dọn dữ liệu cũ (mỗi 6h)
    │
    ├── _missing_data_loop()      ← rule_engine.check_missing() — kiểm tra thiết bị mất tín hiệu (mỗi 5 phút)
    │
    └── http_server.serve()       ← serve_http() — endpoint MCP Streamable HTTP
```

- 3 task chạy độc lập qua `asyncio.gather()`
- Tất cả dùng chung `stop_event` để dừng đồng bộ
- SIGINT/SIGTERM gọi `_request_shutdown()`: set stop_event + báo uvicorn thoát

---

## 5. Routing công cụ và xử lý lỗi

### Định tuyến tool call

```python
async def handle_call_tool(self, name: str, arguments: dict | None):
    args = arguments or {}
    if name.startswith("fleet."):
        # fleet.list_devices → SystemManager → FleetTools
        result = await asyncio.wait_for(
            self._system.call_tool(name, args), timeout=30.0
        )
        # result.success → CallToolResult với structuredContent
        # else → CallToolResult với isError=True
    else:
        # read_sensor → SystemManager → DeviceManager
        adapter_result = await asyncio.wait_for(
            self._system.call_tool(name, args), timeout=30.0
        )
        # adapter_result.success → {"data": adapter_result.data}
```

**Quy tắc:**
- Prefix `fleet.` → `SystemManager` → `FleetTools`
- Không có prefix hoặc prefix khác → `SystemManager` → `DeviceManager`

### Xử lý lỗi có cấu trúc

```python
try:
    result = await asyncio.wait_for(
        self._system.call_tool(name, args), timeout=_TOOL_TIMEOUT
    )
except asyncio.TimeoutError:
    return CallToolResult(
        content=[TextContent(type="text", text=f"timed out after {_TOOL_TIMEOUT}s")],
        isError=True,
    )
except Exception as e:
    return CallToolResult(
        content=[TextContent(type="text", text=str(e))],
        isError=True,
    )
```

Mỗi tool call có timeout 30 giây riêng, không ảnh hưởng lẫn nhau.

---

## 6. Graceful shutdown

### Cơ chế dừng sạch (daemon mode)

```
SIGINT / SIGTERM
    │
    ▼
_request_shutdown()
    │
    ├── stop_event.set()
    │   ├── retention_loop() → kết thúc vòng lặp hiện tại
    │   └── missing_data_loop() → kết thúc vòng lặp hiện tại
    │
    ├── http_server.should_exit = True
    │   └── uvicorn thoát sau request hiện tại
    │
    └── asyncio.gather() hoàn thành → system.stop()
```

```python
def _request_shutdown():
    stop_event.set()
    http_server.should_exit = True

for sig in (signal.SIGINT, signal.SIGTERM):
    loop.add_signal_handler(sig, _request_shutdown)

try:
    await asyncio.gather(*tasks)
finally:
    stop_event.set()
    self._daemon_active = False
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.remove_signal_handler(sig)
    await system.stop()
```

**Đảm bảo:**
- Không mất request đang xử lý
- Retention và missing-data kết thúc cycle hiện tại
- SQLite connection đóng sạch qua `system.stop()`

---

## 7. Timeout và bảo vệ

### Timeout mỗi tool call

```python
_TOOL_TIMEOUT = 30.0  # seconds
```

| Tình huống | Hành vi |
|------------|---------|
| Tool hoàn thành trong 30s | Trả kết quả bình thường |
| Tool chậm hơn 30s | `TimeoutError` → `isError=True` |
| Tool crash | `Exception` → `isError=True` |
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

### Ví dụ outputSchema thực tế

```python
# fleet.py — fleet.list_devices
Tool(
    name="fleet.list_devices",
    description="List all connected devices with their health status...",
    inputSchema={"type": "object", "properties": {}},
    outputSchema={
        "type": "object",
        "properties": {
            "devices": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "protocol": {"type": "string"},
                        "connected": {"type": "boolean"},
                        "healthy": {"type": "boolean"},
                        "error": {"type": "string"},
                        "tools": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            "count": {"type": "integer"},
        },
    },
)
```

**Lưu ý:** outputSchema là metadata. Server không validate kết quả thực tế — đó là trách nhiệm của FleetTools và ReadingStore.

---

## 9. Giới hạn

- **Chỉ hỗ trợ MCP JSON-RPC** — không hỗ trợ transport khác
- **stdio blocking** — agent mode không thể xử lý nhiều request song song
- **HTTP không có auth** — cần thêm API key hoặc JWT nếu expose ra ngoài
- **Không có rate limiting** — HTTP endpoint có thể bị flood
- **FleetTools chỉ đọc** — 4 công cụ đều là read-only, không ghi dữ liệu
- **Không có caching** — mỗi tool call đều query SQLite trực tiếp
- **Daemon không chạy sensor_poller** — background recording cần chạy riêng nếu cần

---

## 10. Ví dụ

### Khởi tạo server

```python
from mcp_server import AgriMeshAIServer
from system import Config, SystemManager

config = Config()
system = SystemManager(config)
server = AgriMeshAIServer(system)
```

### Chế độ agent (stdio)

```python
# python main.py agent
await server.run_stdio()
# → system.start() → serve_stdio() → system.stop()
```

### Chế độ daemon (HTTP)

```python
# python main.py daemon
await server.run_daemon()
# → system.start() → run_daemon_loops() → system.stop()
```

### Gọi tool qua HTTP

```bash
# MCP Streamable HTTP — endpoint /mcp
curl -X POST http://localhost:8374/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
      "name": "fleet.get_history",
      "arguments": {
        "device_id": "sensor_01",
        "sensor_id": "temperature",
        "hours": 24
      }
    }
  }'
```

### Danh sách tools

```python
tools = server.handle_list_tools()
for t in tools:
    print(f"{t.name}: {t.description[:50]}...")
# fleet.list_devices: List all connected devices...
# fleet.get_all_readings: Get the most recent stored reading...
# fleet.get_history: Get time-series history for a specific sensor...
# fleet.search_anomalies: Find sensors whose latest reading deviates...
# farm_sensor.get_moisture: Get current soil moisture...
# farm_sensor.get_temperature: Get current air temperature...
```

---

## 6. Enrichment Pipeline (v2.1+)

**Module:** `ml_detector/enrichment.py`

### Mục đích

Khi `MLDetector` phát hiện bất thường, `EnrichmentPipeline` tự động bổ sung
ngữ cảnh lịch sử 24h và gọi LLM (Qwen2.5 qua Ollama) để giải thích bằng
tiếng Việt.

### Luồng xử lý

```
alert_triggered (EventBus)
    │
    ▼
EnrichmentPipeline.enqueue()  ─── asyncio.Queue (max 1000)
    │
    ▼
_process_queue() — background task
    │
    ├── _get_history(device_id, sensor_id, hours=24)
    │       └── ReadingStore.get_history_for_enrichment()
    │
    ├── [Luôn] Gắn historical_context vào alert
    │
    └── [Cố gắng] _call_llm(alert)
            └── POST /v1/chat/completions → Ollama (qwen2.5:7b)
                    ├── Thành công → alert có llm_explanation
                    └── Thất bại → retry 3 lần (30s, 2min, 5min)
```

### Offline mode

- LLM server không reachable → alert được lưu **không** có explanation
- Retry queue tự động xử lý khi LLM online trở lại
- Alert **không bao giờ** bị chờ enrichment

### Cấu hình

```python
from ml_detector.enrichment import EnrichmentPipeline

pipeline = EnrichmentPipeline(
    store=store,
    llm_api_url="http://100.125.217.6:11434/v1",  # Ollama endpoint
)
pipeline.start()
```

---

## Tham khảo

- Model Context Protocol — modelcontextprotocol.io
- MCP Streamable HTTP — spec.modelcontextprotocol.io
- asyncio.wait_for — docs.python.org
- Starlette HTTP server — starlette.io
- uvicorn — uvicorn.org
