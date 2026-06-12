# Agent & MCP Server — Thiết Kế Chi Tiết

> **Phiên bản:** 1.0 | **Ngày:** 08/06/2026 | **Branch:** `feature/mcp-server`

---

## Mục lục

1. [Tổng Quan Kiến Trúc](#1-tổng-quan-kiến-trúc)
2. [AI Agent](#2-ai-agent)
3. [MCP Server](#3-mcp-server)
4. [Recorder (SQLite)](#4-recorder-sqlite)
5. [Device Discovery & Adapters](#5-device-discovery--adapters)
6. [Aggregator](#6-aggregator)
7. [Background Recorder](#7-background-recorder)
8. [CLI & Transport](#8-cli--transport)
9. [Luồng Dữ Liệu Chi Tiết](#9-luồng-dữ-liệu-chi-tiết)
10. [Hướng Dẫn Phát Triển](#10-hướng-dẫn-phát-triển)

---

## 1. Tổng Quan Kiến Trúc

### 1.1. Mô Hình Tổng Thể

Hệ thống được thiết kế theo mô hình **LLM Server + Edge Gateway**:

- **LLM Server** — Máy chủ tập trung chạy LLM (PC, server, hoặc cloud). Có thể phục vụ nhiều edge gateways.
- **Edge Gateway** — Thiết bị tại hiện trường (Jetson Nano), chạy agent + MCP server + recorder.

```
                          ┌ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐
                          │         LLM Server                 │
                          │  (PC RTX 3050 / Cloud VM)          │
                          │  Ollama + Qwen2.5 7B               │
                          │  Port 11434                        │
                          └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘
                                    ▲              ▲
                                    │ Tailscale    │ Tailscale
                                    │              │
                          ┌─────────┴──────────────┴─────────┐
                          │        Edge Gateway               │
                          │  (Jetson Nano 4GB)                │
                          │                                   │
                          │  ┌─────────────────────────┐      │
                          │  │  AI Agent (edge-agent)   │      │
                          │  │  ├── Online: chat + tool │      │
                          │  │  └── Offline: fallback   │      │
                          │  │                          │      │
                          │  │  MCP Server (agrimesh)   │      │
                          │  │  Recorder (SQLite)       │      │
                          │  │  Hardware I/O (LoRa...)  │      │
                          │  └─────────────────────────┘      │
                          └──────────────────────────────────┘
```

### 1.2. Dual-Mode Operation

| Mode | Điều kiện | Agent | MCP Server | Recorder |
|------|-----------|-------|-------------|----------|
| **Online** | LLM Server reachable | ✅ Chat + tool calling | ✅ Tool routing | ✅ Ghi dữ liệu 24/7 |
| **Offline** | LLM Server unreachable | ❌ Thông báo "offline" | ✅ Vẫn routing | ✅ Ghi dữ liệu 24/7 |

Trong **offline mode**, edge gateway vẫn hoạt động đầy đủ như một **gateway thu thập và xử lý dữ liệu**:
- Poll sensors qua LoRa/Serial/MQTT
- Ghi dữ liệu vào SQLite
- Kiểm tra threshold rules (khi có Rule Engine)
- Lưu trữ dữ liệu chờ đồng bộ khi online

### 1.3. Sơ Đồ Kết Nối Hiện Tại

```
┌─────────────────────────────────────────────────────────────────────┐
│                         PC (RTX 3050)                               │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                     Ollama                                   │   │
│  │              Qwen2.5 7B (Q4_K_M)                            │   │
│  │              Port 11434                                      │   │
│  └────────────────────────┬─────────────────────────────────────┘   │
│                           │ Tailscale VPN                           │
│                   100.125.217.6                                     │
└───────────────────────────┼─────────────────────────────────────────┘
                            │
┌───────────────────────────┼─────────────────────────────────────────┐
│                    Jetson Nano (edge)                                │
│                           │                                          │
│  ┌────────────────────────▼──────────────────────────────────────┐  │
│  │                    AI Agent (agent/main.py)                    │  │
│  │  ┌─────────────────────────────────────────────────────────┐  │  │
│  │  │  edge-agent framework (vendored, zero-dep)              │  │  │
│  │  │  ├── OllamaProvider ──HTTP──► PC (Tailscale)            │  │  │
│  │  │  ├── MCPServer ──subprocess──► agrimesh start           │  │  │
│  │  │  └── Session ── interactive REPL                        │  │  │
│  │  └─────────────────────────────────────────────────────────┘  │  │
│  └────────────────────────┬──────────────────────────────────────┘  │
│                           │ stdio (MCP JSON-RPC 2.0)                │
│  ┌────────────────────────▼──────────────────────────────────────┐  │
│  │                    MCP Server (agrimesh)                       │  │
│  │                                                                │  │
│  │  ┌──────────────┐   ┌──────────────┐   ┌──────────────────┐   │  │
│  │  │ Fleet Tools  │   │ call_device  │   │   Aggregator     │   │  │
│  │  │ (4 tools)    │   │ (generic)    │   │ routing + lock   │   │  │
│  │  └──────┬───────┘   └──────┬───────┘   └────────┬─────────┘   │  │
│  │         │                  │                     │              │  │
│  │  ┌──────▼──────────────────▼─────────────────────▼──────────┐  │  │
│  │  │                    Recorder (SQLite)                      │  │  │
│  │  │  readings | alerts | devices | actuation_log             │  │  │
│  │  └──────────────────────────────────────────────────────────┘  │  │
│  │                                                                │  │
│  │  ┌──────────────────────────────────────────────────────────┐  │  │
│  │  │  Discovery + Adapters                                    │  │  │
│  │  │  TOML profiles → Mock | Serial | MQTT                    │  │  │
│  │  └──────────────────────────────────────────────────────────┘  │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                      │
│  IP: 100.91.80.113 (Tailscale)                                       │
│  RAM: 4GB (2GB free sau khi chạy agent)                              │
└──────────────────────────────────────────────────────────────────────┘
```

### 1.2. Luồng Dữ Liệu Cơ Bản

```
User query (English)
    │
    ▼
AI Agent (edge-agent)
    │
    ├──► LLM (Qwen2.5 7B via Tailscale)
    │       │
    │       └── decides to call tool or reply
    │
    ├──► [Tool call] ──MCP JSON-RPC──► MCP Server
    │       │
    │       ├──► Fleet tool → Recorder → SQLite
    │       └──► Device tool → Aggregator → Adapter → Hardware
    │
    └──► [Text reply] → In ra terminal
```

### 1.4. Nguyên Tắc Thiết Kế

- **Offline-first:** Agent + MCP Server + Recorder chạy local trên Jetson
- **LLM tách rời:** LLM chạy trên PC, kết nối qua Tailscale (có thể thay bằng cloud API)
- **MCP là giao tiếp duy nhất:** Agent gọi hardware tools qua MCP protocol
- **Tool calling = English:** Qwen2.5 chỉ gọi tool ổn định với English prompt
- **Reply = Vietnamese:** Agent luôn trả lời bằng tiếng Việt

---

## 2. AI Agent

### 2.1. File Cấu Trúc

```
agent/
├── main.py                  # Entry point, REPL loop
├── instructions.txt         # System prompt (English)
├── __init__.py
└── edge_agent/              # Vendored framework (zero-dep)
    ├── __init__.py
    ├── agent.py             # Agent class
    ├── mcp.py               # MCPServer (MQTT stdio client)
    ├── session.py           # Interactive REPL
    ├── providers/
    │   ├── ollama.py        # OllamaProvider
    │   └── ...
    └── tool.py, types.py, template.py, ...
```

### 2.2. Entry Point (`agent/main.py`)

```python
# Flow:
1. Đọc config/models.yaml → model name + api_url
2. Tạo MCPServer subprocess: [python, -m, mcp_server, start]
3. Tạo Agent với OllamaProvider + instructions
4. Session.start() → REPL loop

# Key code:
mcp_server = MCPServer("agrimesh-mcp",
    command=[sys.executable, "-m", "mcp_server", "start"])

with mcp_server:
    agent = Agent(
        provider=OllamaProvider(model=model_name, base_url=base_url, temperature=0.01),
        instructions=instructions,
        mcp_servers=[mcp_server],
    )
    Session(agent=agent).start()
```

### 2.3. Session Loop (`session.py`)

```
while True:
    user_input = input("You: ")
    if exit: break

    self._messages.append(user_msg)
    response = provider.chat(self._messages, tools_list)

    if response.tool_calls:
        print(f"  🔧 {name}({args})")          # ← tool indicator
        for tc in response.tool_calls:
            result = _execute_tool(tc)          # ← gọi MCP server
            self._messages.append(result_msg)
        # loop back → LLM gọi tiếp hoặc trả lời

    if not response.tool_calls:
        print(f"Agent: {answer}")
```

### 2.4. OllamaProvider (`providers/ollama.py`)

- **API:** OpenAI-compatible `/v1/chat/completions`
- **Method:** `urllib` (stdlib, zero-dep)
- **Temperature:** `0.01` (thấp → deterministic tool calling)
- **Tool format:** OpenAI function-calling format
- **Custom patch:** thêm `temperature` parameter (original không có)

### 2.5. Instructions (`instructions.txt`)

```text
- English prompt → model gọi tool ổn định
- Liệt kê 5 functions: fleet.list_devices, fleet.get_all_readings,
  fleet.get_history, fleet.get_alerts, call_device(device, tool)
- Rule: Reply in Vietnamese ONLY
- Few-shot examples để model hiểu format
```

### 2.6. Tool Names

| Tên trong instructions | Tên MCP thật | Ghi chú |
|------------------------|-------------|---------|
| `fleet.list_devices` | `fleet.list_devices` | Dot notation |
| `fleet.get_all_readings` | `fleet.get_all_readings` | |
| `fleet.get_history(node_id, sensor_id, hours)` | `fleet.get_history` | Parameters in schema |
| `fleet.get_alerts(hours, severity)` | `fleet.get_alerts` | |
| `call_device(device, tool)` | `call_device` | Generic, không cần dot |

---

## 3. MCP Server

### 3.1. File Cấu Trúc

```
mcp_server/
├── __init__.py              # sys.path fix for local modules
├── __main__.py              # python -m mcp_server
├── server.py                # FastMCP server (core)
├── cli.py                   # agrimesh CLI
├── aggregator.py            # Device routing + locking
├── discovery.py             # TOML → adapter instantiation
├── background_recorder.py   # Background polling 24/7
├── pyproject.toml           # CLI entry: agrimesh
├── setup.py                 # Editable install
├── tools/
│   ├── __init__.py
│   └── fleet.py             # Fleet tool handlers
├── adapters/
│   ├── base.py              # BaseAdapter interface
│   ├── mock.py              # MockAdapter (testing)
│   ├── serial.py            # SerialAdapter (UART)
│   └── mqtt.py              # MQTTAdapter (paho)
├── devices/
│   ├── __init__.py
│   └── model.py             # Pydantic models
├── profiles/
│   ├── __init__.py
│   ├── parser.py            # TOML → DeviceModel
│   └── generator.py         # DeviceModel → MCP Tool
└── tests/
    ├── fixtures/*.toml
    └── test_*.py
```

### 3.2. FastMCP Server (`server.py`)

Sử dụng `FastMCP` với **lifespan** context:

```python
@asynccontextmanager
async def lifespan(server: FastMCP):
    # Init
    store = ReadingsStore(...)
    recorder = Recorder(store)
    await recorder.start()
    discovered = discover_devices(PROFILES_DIR)
    aggregator = Aggregator()
    aggregator.register_all(discovered)
    try:
        yield {"recorder": recorder, "aggregator": aggregator}
    finally:
        await recorder.stop()

server = FastMCP("agrimesh-mcp", lifespan=lifespan)
```

**Lợi ích của FastMCP:**
- Auto-generate JSON Schema từ function signature
- Không cần `list_tools`/`call_tool` handlers thủ công
- Tích hợp sẵn lifespan cho lifecycle

### 3.3. Các MCP Tools

#### Fleet Tools

| Tool | Parameters | Mô tả | Data Source |
|------|-----------|-------|-------------|
| `fleet.list_devices` | None | Danh sách thiết bị | SQLite `devices` table |
| `fleet.get_all_readings` | None | Dữ liệu cảm biến mới nhất | SQLite `readings` table |
| `fleet.get_history` | `node_id`, `sensor_id`, `hours` | Lịch sử dữ liệu | SQLite `readings` table |
| `fleet.get_alerts` | `hours`, `severity?` | Cảnh báo gần đây | SQLite `alerts` table |

Mỗi fleet tool gọi `handle_fleet_tool()` trong `tools/fleet.py` → `recorder.store.query()`.

#### Device Tool

| Tool | Parameters | Mô tả |
|------|-----------|-------|
| `call_device` | `device`, `tool` | Gọi tool trên thiết bị cụ thể |

`call_device` → `aggregator.call_tool(f"{device}.{tool}")` → adapter → kết quả.

Nếu kết quả là số → auto-record vào SQLite readings.

### 3.4. MCP Prompts

```python
@server.prompt()
async def device_query_guide() -> str: ...

@server.prompt()
async def telemetry_guide() -> str: ...
```

Prompts giúp LLM hiểu cách dùng tools (được gửi khi session bắt đầu).

---

## 4. Recorder (SQLite)

### 4.1. File Cấu Trúc

```
recorder/
├── __init__.py              # Export Recorder, ReadingsStore
├── store.py                 # SQLite storage layer
└── recorder.py              # High-level pipeline wrapper
```

### 4.2. Database Schema

#### `readings` — Time-series sensor data

```sql
CREATE TABLE readings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id     INTEGER NOT NULL,
    sensor_id   TEXT    NOT NULL,
    value       REAL    NOT NULL,
    unit        TEXT    NOT NULL,
    timestamp   INTEGER NOT NULL,
    quality     INTEGER DEFAULT 100
);
```

#### `alerts` — Cảnh báo

```sql
CREATE TABLE alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id     INTEGER NOT NULL,
    sensor_id   TEXT,
    rule_id     TEXT    NOT NULL,
    value       REAL,
    severity    TEXT    NOT NULL,
    message     TEXT    NOT NULL,
    timestamp   INTEGER NOT NULL,
    ack_at      INTEGER,
    ack_by      TEXT
);
```

#### `devices` — Device registry

```sql
CREATE TABLE devices (
    node_id      INTEGER PRIMARY KEY,
    type         TEXT    NOT NULL,
    name         TEXT    NOT NULL,
    location     TEXT,
    sensors      TEXT,
    config       TEXT,
    status       TEXT DEFAULT 'unknown',
    last_seen    INTEGER,
    battery_pct  INTEGER
);
```

#### `actuation_log` — Audit trail

```sql
CREATE TABLE actuation_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id      INTEGER NOT NULL,
    actuator_id  TEXT    NOT NULL,
    command      TEXT    NOT NULL,
    params       TEXT,
    duration_sec INTEGER,
    triggered_by TEXT    NOT NULL,
    confirmed_by TEXT,
    status       TEXT    NOT NULL,
    timestamp    INTEGER NOT NULL
);
```

### 4.3. API

**ReadingsStore** (`store.py`):
- `insert_reading()`, `get_readings()`, `get_latest_reading()`, `get_all_latest_readings()`
- `insert_alert()`, `get_alerts()`, `acknowledge_alert()`
- `register_device()`, `update_device_status()`, `list_devices()`, `get_device()`
- `log_actuation()`, `get_actuation_log()`
- `run_retention()` — Xoá dữ liệu cũ hơn N ngày

**Recorder** (`recorder.py`):
- `record_reading()`, `record_alert()`, `register_device()`, `record_actuation()`
- `update_device_health()`, `run_retention()`

### 4.4. Node ID Generation

Sử dụng `hashlib.md5(device_name.encode()).hexdigest()[:8]` → deterministic, ổn định giữa các lần restart.

---

## 5. Device Discovery & Adapters

### 5.1. TOML Profiles

```toml
# devices/farm_sensor.toml
[device]
name = "farm_sensor"
description = "Mock soil moisture & temperature sensor"

[connection]
protocol = "mock"           # mock | serial | mqtt
port = "/dev/ttyUSB0"       # serial
baud_rate = 115200          # serial
broker = "localhost"        # mqtt
mqtt_port = 1883            # mqtt

[[tools]]
name = "get_moisture"
description = "Get current soil moisture"
command = "READ"

[[tools]]
name = "get_temperature"
description = "Get current air temperature"
command = "READ"

[recording]
enabled = true
poll_interval_ms = 300000
```

### 5.2. Discovery Flow

```
devices/*.toml
    │
    ▼
parser.py: parse_profile() → DeviceModel (Pydantic)
    │
    ▼
generator.py: generate_tools() → MCP Tool[]
    │
    ▼
discovery.py: discover_devices()
    ├── parse_profiles_dir()
    ├── lookup adapter class from registry
    └── return DiscoveredDevice[]
```

### 5.3. Adapter Registry

| Protocol | Adapter Class | File |
|----------|--------------|------|
| `mock` | `MockAdapter` | `adapters/mock.py` |
| `serial` | `SerialAdapter` | `adapters/serial.py` |
| `mqtt` | `MQTTAdapter` | `adapters/mqtt.py` |

Base interface (`adapters/base.py`):
```python
class BaseAdapter:
    async connect() -> AdapterResult
    async disconnect() -> AdapterResult
    async send(data) -> AdapterResult
    async receive(length?, timeout?) -> AdapterResult
    async health_check() -> AdapterResult
```

---

## 6. Aggregator

### 6.1. Vai Trò

Aggregator là lớp trung gian giữa MCP Server và các device adapters. Nó giải quyết:

1. **Per-device locking** — `asyncio.Lock` mỗi device, tránh concurrent conflict
2. **Tool routing** — Parse `"device_name.tool_name"` → tìm đúng device + tool
3. **Health check** — Kiểm tra adapter còn sống trước khi gửi lệnh

### 6.2. Code Mẫu

```python
class Aggregator:
    devices: dict[str, DiscoveredDevice]
    _locks: dict[str, asyncio.Lock]

    def register_all(discovered):
        for d in discovered:
            self.devices[d.model.name] = d
            self._locks[d.model.name] = Lock()

    async def call_tool(name, args) -> AdapterResult:
        device_name, tool_name = name.split(".")
        device = self.devices[device_name]
        async with self._locks[device_name]:
            # health check
            await device.adapter.health_check()
            # send command
            return await device.adapter.send(tool_def.command)
```

---

## 7. Background Recorder

### 7.1. Vai Trò

Poll devices định kỳ và ghi dữ liệu vào SQLite. Chạy trong `agrimesh daemon`.

```
BackgroundRecorder.start()
    │
    ├── farm_sensor: poll every 300s
    │   ├── get_temperature → recorder.record_reading()
    │   └── get_humidity → recorder.record_reading()
    │
    ├── serial_sensor: poll every 300s (nếu có HW)
    │
    └── mqtt_sensor: poll every 300s (nếu có broker)
```

### 7.2. Device Registration

Khi daemon start, tự động register devices từ TOML profiles vào SQLite:
```python
await bg_recorder.register_devices()
# → recorder.register_device(name, type, sensors, config)
# → devices table có dữ liệu
```

---

## 8. CLI & Transport

### 8.1. Commands

| Lệnh | Transport | Background Polling | Dùng khi |
|------|-----------|-------------------|----------|
| `agrimesh start` | stdio | ❌ | Agent / Claude Desktop |
| `agrimesh daemon` | HTTP (SSE) | ✅ | Production 24/7 |
| `agrimesh status` | — | — | Kiểm tra hệ thống |

### 8.2. FastMCP Transport

FastMCP có built-in transport selection:

```python
# stdio mode
server.run(transport="stdio")

# HTTP SSE mode
server.run(transport="sse", host="0.0.0.0", port=8374)
```

### 8.3. Entry Points

```bash
# Direct Python
python -m mcp_server start

# Via installed CLI
agrimesh start

# Via agent (subprocess)
python agent/main.py  # tự động start MCP server
```

---

## 9. Luồng Dữ Liệu Chi Tiết

### 9.1. User Query "List all devices"

```
User: "List all devices"
    │
    ▼
Session.start()
    ├── self._messages += [user_msg]
    ├── provider.chat(messages, tools_list)
    │       │
    │       ▼
    │   Ollama API: POST /v1/chat/completions
    │   Body: {messages, tools, temperature: 0.01}
    │       │
    │       ▼
    │   Response: {tool_calls: [{name: "fleet.list_devices", args: {}}]}
    │
    ├── print(f"  🔧 fleet.list_devices({})")
    ├── agent._execute_tool(tc)
    │       │
    │       ▼
    │   MCPServer._call_tool("fleet.list_devices", {})
    │       │
    │       ▼ JSON-RPC 2.0
    │   MCP Server (subprocess)
    │       │
    │       ├── FastMCP routes to fleet_list_devices()
    │       ├── recorder.store.list_devices() → SQLite
    │       └── return JSON result
    │
    ├── self._messages += [tool_result]
    ├── provider.chat(messages, tools_list)  # second call
    │       │
    │       ▼
    │   Response: {content: "Danh sách thiết bị: ..."}
    │
    └── print(f"Agent: {answer}")
```

### 9.2. Tool Call Details

#### Fleet Tool (`fleet.list_devices`)

```
LLM decides → tool_call: fleet.list_devices({})
    │
    ▼
MCP Server → handle_fleet_tool("fleet.list_devices", {}, recorder)
    │
    ▼
recorder.store.list_devices()
    │
    ▼ SQL: SELECT * FROM devices ORDER BY node_id
    │
    ▼ Return JSON
    {
      "devices": [
        {"node_id": 208, "name": "farm_sensor", "type": "mock", ...},
        {"node_id": 4741, "name": "mock_sensor", ...}
      ]
    }
```

#### Device Tool (`call_device`)

```
LLM decides → tool_call: call_device({device: "farm_sensor", tool: "get_temperature"})
    │
    ▼
MCP Server → aggregator.call_tool("farm_sensor.get_temperature")
    │
    ├── health_check() → OK
    ├── device.adapter.send("READ") → MockAdapter → "25.3"
    │
    ├── Auto-record: recorder.record_reading(node_id, "get_temperature", 25.3, "")
    │
    └── Return "25.3"
```

### 9.3. Background Polling

```
BackgroundRecorder.start()
    │
    for each device with recording.enabled:
        │
        ▼
        _poll_loop(name, device, interval)
            │
            while True:
                │
                for each tool in device.tools:
                    │
                    ▼
                    aggregator.call_tool(f"{name}.{tool.name}")
                        │
                        adapter.send(tool_def.command)
                        │
                        if result is numeric:
                            recorder.record_reading(...)
                │
                await asyncio.sleep(interval)
```

---

## 10. Hướng Dẫn Phát Triển

### 10.1. Cấu Trúc Thư Mục

```
AgriMeshAI/
├── agent/               # AI Agent
│   ├── main.py          # Entry point
│   ├── instructions.txt # System prompt
│   └── edge_agent/      # Vendored framework
├── mcp_server/          # MCP Server
├── recorder/            # SQLite storage
├── config/
│   └── models.yaml      # LLM config (model, api_url)
├── devices/             # Device profiles (TOML)
├── data/                # SQLite database (generated)
├── scripts/
│   ├── setup.sh         # Python + venv + pip
│   └── start.sh         # Kiểm tra Ollama → run agent
└── requirements.txt     # Python dependencies
```

### 10.2. Cách Thêm Một Tool Mới

**Fleet Tool:**
1. Thêm handler trong `tools/fleet.py` (thêm `if name == "fleet.new_tool"`)
2. Thêm `@server.tool()` trong `server.py`
3. Cập nhật `instructions.txt`

**Device Tool:**
1. Thêm `[[tools]]` trong TOML profile
2. parser.py + generator.py tự động xử lý

### 10.3. Cách Thêm Adapter Mới

1. Tạo file `adapters/new_protocol.py` với class kế thừa `BaseAdapter`
2. Đăng ký trong `discovery.py` (`_ADAPTER_REGISTRY`)
3. Thêm connection params trong `devices/model.py` (nếu cần)

### 10.4. Dependencies

```
requirements.txt (centralized, không có file con):
├── openai>=1.0           # Agent (OpenAI client)
├── httpx>=0.27           # HTTP client
├── pyyaml>=6.0           # Config parser
├── aiosqlite>=0.20       # Recorder (async SQLite)
├── mcp>=1.0              # MCP SDK (FastMCP)
├── click>=8.0            # CLI
├── starlette>=0.37       # HTTP server
├── uvicorn>=0.27         # ASGI server
├── paho-mqtt>=2.0        # MQTT adapter
├── pyserial-asyncio>=0.6 # Serial adapter
└── tomli>=2.0            # TOML parser (Python <3.11)
```

### 10.5. Testing

**Recorder:**
```python
# test_recorder.py
store = ReadingsStore(":memory:")
recorder = Recorder(store)
await recorder.start()
await recorder.record_reading(1, "temperature", 32.5, "°C")
readings = await store.get_readings(1, "temperature")
assert readings[0]["value"] == 32.5
```

**MCP Server:**
```bash
# Test tools trực tiếp
agrimesh status

# Test qua edge-agent MCPServer
python3 -c "
from edge_agent.mcp import MCPServer
mcp = MCPServer('test', command=['python', '-m', 'mcp_server', 'start'])
with mcp:
    print(mcp.tools[0].fn())  # call fleet.list_devices
"
```

### 10.6. Các Hạn Chế Đã Biết

| Hạn chế | Nguyên nhân | Workaround |
|---------|-------------|------------|
| Qwen2.5 1.5B không gọi tool với tiếng Việt | Model nhỏ, training data | Dùng English query |
| Qwen2.5 7B thỉnh thoảng trả lời Trung/Nhật | Multilingual training | Instructions nhấn mạnh "Vietnamese ONLY" |
| Jetson Nano không dùng được GPU CUDA | CUDA 10.2 + gcc-11 không tương thích | Dùng llama.cpp pre-built b5050 |
| smolagents loop vô hạn với Qwen2.5 | JSON output format incompatible | Dùng edge-agent |
| FastMCP tool name dots bị strip bởi smolagens | smolagens rename tools | Không dùng smolagens |

### 10.7. Phát Triển Tương Lai

| Tính năng | Mức độ | Gợi ý |
|-----------|--------|--------|
| Rule Engine (R01-R08) | ⬜ | Kiểm tra readings theo threshold → ghi alert |
| Notifier (Telegram) | ⬜ | Push notification khi có alert |
| Web UI | ⬜ | Dashboard + chat interface |
| OTA firmware | ⬜ | Cập nhật ESP32 qua LoRa |
| LoRa Bridge hardware | ⬜ | Kết nối SerialAdapter với module SX1262 |
| Background Recorder (daemon) | ⏸️ | Tạm dừng do FastMCP migration |
