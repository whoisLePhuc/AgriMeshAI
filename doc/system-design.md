# THIẾT KẾ HỆ THỐNG
## AI Agent + MCP Server + LoRa Mesh — Nông Nghiệp Thông Minh
**Phiên bản:** 4.0 (SystemManager architecture) | **Ngày:** 12/06/2026 | **Quy mô:** POC / Vườn nhỏ — 1 người dùng, < 20 node

---

## Mục lục

1. [Tổng Quan Hệ Thống](#1-tổng-quan-hệ-thống)
2. [Phần Cứng](#2-phần-cứng)
3. [Software Stack](#3-software-stack)
4. [Luồng Dữ Liệu](#4-luồng-dữ-liệu)
5. [MCP Server — agrimesh](#5-mcp-server--agrimesh)
6. [AI Agent](#6-ai-agent)
7. [An Toàn và Bảo Mật](#7-an-toàn-và-bảo-mật)
8. [Phát Hiện Bất Thường — Rule Engine](#8-phát-hiện-bất-thường--rule-engine)
9. [Machine Learning](#9-machine-learning)
10. [Kết Nối Người Dùng](#10-kết-nối-người-dùng)
11. [Giao Thức LoRa Mesh](#11-giao-thức-lora-mesh)
12. [Kế Hoạch Triển Khai](#12-kế-hoạch-triển-khai)
13. [Tổng Hợp Gap Analysis](#13-tổng-hợp-gap-analysis)

---

## 1. Tổng Quan Hệ Thống

### 1.1. Mục đích

Hệ thống kết nối người nông dân với thiết bị cảm biến và điều khiển ngoài đồng ruộng thông qua ngôn ngữ tự nhiên. Người dùng chỉ cần nhắn tin hoặc nói chuyện với hệ thống; AI tự động đọc cảm biến, phân tích dữ liệu, và thực thi lệnh điều khiển.

### 1.2. Nguyên tắc thiết kế

- **LLM Server ≠ Edge Gateway:** LLM chạy trên server riêng (PC/cloud), edge gateway chỉ chạy agent nhẹ + MCP + recorder.
- **Dual-mode:** Online = LLM available → chat + tool calling. Offline = LLM unreachable → data collection 24/7.
- **Offline-first:** Thu thập dữ liệu, ghi SQLite, threshold alerts vẫn hoạt động khi mất kết nối LLM Server.
- **Edge-centric:** Mọi quyết định vận hành được đưa ra tại gateway, không phụ thuộc cloud.
- **MCP là lớp giao tiếp duy nhất** giữa AI và phần cứng.
- **Safety tách biệt khỏi AI:** Guard rail không dùng LLM để quyết định an toàn.
- **Human-in-the-loop:** Lệnh điều khiển actuator luôn cần xác nhận người dùng.

### 1.3. Kiến trúc hiện tại (06/2026)

> **Online mode:** LLM Server reachable → Agent chat + tool calling
> **Offline mode:** LLM Server unreachable → Edge gateway = data collector + recorder + threshold alerts

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           PC (RTX 3050 — 6GB VRAM)                          │
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                          Ollama                                      │   │
│  │                     Qwen2.5 7B (Q4_K_M)                              │   │
│  │                     Port 11434                                       │   │
│  │                     GPU: ~40 tok/s                                   │   │
│  └───────────────────────────────────┬──────────────────────────────────┘   │
│                                      │ Tailscale VPN                        │
│                                100.125.217.6                                │
└───────────────────────────────────────┼─────────────────────────────────────┘
                                        │
┌───────────────────────────────────────┼─────────────────────────────────────┐
│                           JETSON NANO (edge)                                │
│                             100.91.80.113                                   │
│  ┌────────────────────────────────────▼──────────────────────────────────┐  │
│  │                     SystemManager (orchestrator)                      │  │
│  │                                                                       │  │
│  │  ┌─────────────────────┐    ┌──────────────────────────────────────┐  │  │
│  │  │    AI AGENT         │    │  EventBus + EventQueueManager        │  │  │
│  │  │  (edge-agent)       │    │  (pub/sub nội bộ, DLQ, retry)        │  │  │
│  │  │ ┌─────────────────┐ │    └──────────────────┬───────────────────┘  │  │
│  │  │ │ Session (REPL)  │ │                       │                      │  │
│  │  │ │ OllamaProvider  │─┤─ (tool bridge) ───────┤                      │  │
│  │  │ │ (→ PC via HTTP) │ │                       │                      │  │
│  │  │ └─────────────────┘ │                       │                      │  │
│  │  └─────────────────────┘                       │                      │  │
│  │                                                ▼                      │  │
│  │  ┌─────────────────────────────────────────────────────────────────┐  │  │
│  │  │                    MCP SERVER (lowlevel.Server)                 │  │  │
│  │  │                                                                 │  │  │
│  │  │  ┌──────────────┐  ┌──────────────────┐  ┌──────────────────┐   │  │  │
│  │  │  │ Fleet Tools  │  │  DeviceManager   │  │  DatabaseManager │   │  │  │
│  │  │  │ (4 tools)    │  │  (discovery,     │  │  (write          │   │  │  │
│  │  │  │  read-only)  │  │   catalog,       │  │   coordinator)   │   │  │  │
│  │  │  └──────┬───────┘  │   routing, lock) │  └────────┬─────────┘   │  │  │
│  │  │         │          └────────┬─────────┘           │             │  │  │
│  │  │         │                   │                     │             │  │  │
│  │  │  ┌──────┴───────────────────┴─────────────────────▼──────────┐  │  │  │
│  │  │  │              ReadingStore (SQLite WAL)                    │  │  │  │
│  │  │  │  1 table: readings (device_id, sensor_id, value, unit, ts)│  │  │  │
│  │  │  └───────────────────────────────────────────────────────────┘  │  │  │
│  │  │                                                                 │  │  │
│  │  │  ┌───────────────────────────────────────────────────────────┐  │  │  │
│  │  │  │  Adapters: Mock | Serial (UART) | MQTT                    │  │  │  │
│  │  │  └───────────────────────────────────────────────────────────┘  │  │  │
│  │  └─────────────────────────────────────────────────────────────────┘  │  │
│  │                                                                       │  │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                 │  │
│  │  │  Rule Engine │  │  Notifier    │  │  Retention   │                 │  │
│  │  │  (8 rules)   │  │  (console,   │  │ (downsample  │                 │  │
│  │  │  threshold/  │  │  telegram,   │  │   + purge)   │                 │  │
│  │  │  rate/stuck) │  │  webhook)    │  └──────────────┘                 │  │
│  │  └──────────────┘  └──────────────┘                                   │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  RAM: 4GB shared (GPU + CPU)                                                │
│  GPU: 128-core Maxwell (Cuda 10.2)                                          │
│  OS: Ubuntu 22.04 (JetPack R32.7.6)                                         │
└─────────────────────────────────────────────────────────────────────────────┘
```

| Lớp | Thành phần | Vai trò | Chạy 24/7? |
|-----|-----------|---------|-----------|
| Giao tiếp | Web (port 8374), Telegram Bot, SMS | Nhận lệnh người dùng, trả kết quả | ✅ |
| Edge — LLM Agent | Qwen2.5 + edge-agent + MCP | Xử lý ngôn ngữ, suy luận, multi-step | ❌ On-demand |
| Edge — Daemon | MCP server + Rule Engine + Recorder | Thu thập data, threshold, retention | ✅ 24/7 |
| Edge — Modules | SystemManager, EventBus, Notifier, DatabaseManager | Orchestration, event-driven, alerts | ✅ 24/7 |
| Mesh Field | ESP32 Sensor / Relay / Actuator Node | Thu thập dữ liệu, thực thi lệnh | ✅ 24/7 |

---

## 2. Phần Cứng

### 2.1. Edge Gateway

| Thành phần | Lựa chọn khuyến nghị | Ghi chú |
|-----------|---------------------|---------|
| Board chính | Jetson Nano 4GB | GPU 128 nhân Maxwell cho ML inference |
| LoRa module | SX1262 HAT (UART/SPI) | Gắn trực tiếp lên GPIO |
| Storage | 32GB+ microSD / SSD | SSD qua USB 3.0 ưu tiên |
| Nguồn điện | 5V/4A adapter (barrel jack) | Jetson Nano không hỗ trợ UPS HAT — cần UPS ngoài |
| GPU | 128 nhân Maxwell @ 921MHz | CUDA acceleration cho ML inference (ONNX Runtime + TensorRT) |
| Kết nối | WiFi USB + 4G USB dongle (tùy chọn) | Jetson Nano không có WiFi onboard |

### 2.2. Sensor / Relay / Actuator Node

| Loại node | MCU | Module radio | Cảm biến / thiết bị ngoại vi | Nguồn |
|-----------|-----|-------------|------------------------------|-------|
| Sensor Node | ESP32-S3 | SX1262 | DHT22 (nhiệt/ẩm không khí), Capacitive soil sensor, BH1750 (ánh sáng) | Solar + LiPo 3.7V |
| Relay Node | ESP32-S3 | SX1262 | Không có sensor — chỉ forward gói tin mesh | Solar + LiPo 3.7V |
| Actuator Node | ESP32 | SX1262 | Relay module 5V (bơm, van solenoid) | Nguồn lưới 12V DC hoặc solar |

> ⚠️ **GAP G00 — Watchdog phần cứng:** Thiết kế hiện tại chưa đề cập external watchdog trên node. Cần thêm hardware watchdog tích hợp của ESP32 với timeout ≤ 60s để tự reset khi firmware treo.

---

## 3. Software Stack

### 3.1. Gateway software

| Thành phần | Công nghệ | Vai trò | RAM | Chạy ở đâu |
|-----------|-----------|---------|-----|-----------|
| **LLM Inference** | Ollama + Qwen2.5 7B Q4_K_M | Inference GPU (RTX 3050) | ~4.7 GB | **PC** (Tailscale) |
| **AI Agent** | edge-agent (vendored) | Chat + tool calling, provider abstraction | ~50 MB | Jetson |
| **MCP Server** | `mcp.server.lowlevel.Server` | Tool routing (fleet + device), stdio + HTTP | ~30 MB | Jetson |
| **SystemManager** | `system/manager.py` | Central orchestrator: lifecycle, DI, health check | ~10 MB | Jetson |
| **EventBus** | `event_bus/` | Pub/sub sync + async queue (DLQ, retry) | ~5 MB | Jetson |
| **Database** | SQLite 3 + WAL mode | 1 table: `readings` (time-series) | ~20 MB | Jetson |
| **Rule Engine** | `rule_engine/engine.py` | 8 rules: threshold, rate, stuck, missing data | ~5 MB | Jetson |
| **Notifier** | `notifier/` | Multi-channel: console, telegram, webhook, SMS | ~10 MB | Jetson |
| **Sensor Poller** | `sensor_poller/` | Background polling (per-device async tasks) | ~10 MB | Jetson |
| **Web UI** | Chưa triển khai | — | — | — |
| **OS** | Ubuntu 22.04 (Jetson) + Ubuntu 22.04 (PC) | — | — | — |
| **VPN** | Tailscale | Kết nối Jetson ↔ PC | ~50 MB | Cả 2 máy |

> **Tổng RAM khi đầy đủ:** ~280 MB (daemon + ML + DB + notification) + ~1.6 GB (LLM khi chạy). Jetson Nano 4GB còn ~2 GB trống. GPU 128-core Maxwell hỗ trợ CUDA cho ONNX Runtime (ML inference) và Ollama (LLM).

### 3.2. Node firmware (ESP32)

| Thành phần | Thư viện / framework | Ghi chú |
|-----------|---------------------|---------|
| RTOS | FreeRTOS (tích hợp trong ESP-IDF) | Task-based concurrency |
| LoRa mesh | LoRaMesher hoặc MeshCore | Distance-vector routing, TDMA, self-healing |
| Sensor drivers | ESP-IDF / Arduino libs | DHT22, BH1750, capacitive soil |
| Power management | ESP32 deep sleep + wake on timer/interrupt | Chu kỳ wake mỗi 5–15 phút |
| OTA update | ESP-IDF OTA partition + custom LoRa OTA protocol | Xem mục 7.3 |

> ⚠️ **GAP G11 — Deploy guide:** v1.0 chưa mô tả dependency management và môi trường deploy. Cần thêm `pyproject.toml` với version pin, systemd unit files cho mỗi service, và setup script tự động.

---

## 4. Luồng Dữ Liệu

### 4.1. Thu thập dữ liệu cảm biến (event-driven)

```
Sensor / Poller                  Gateway Modules                   AI Agent / User
    │                               │                                 │
    ├── tool call (serial/mqtt) ───►│                                 │
    │                               │                                 │
    │                        ┌──────▼────────┐                        │
    │                        │ Sensor Poller │                        │
    │                        │ (tự động)     │                        │
    │                        │ hoặc MCP tool │                        │
    │                        └──────┬────────┘                        │
    │                               │                                 │
    │                     publish "db_write"                          │
    │                               │                                 │
    │                        ┌──────▼──────────┐                      │
    │                        │ DatabaseManager │                      │
    │                        │ _handle_write() │                      │
    │                        └──────┬──────────┘                      │
    │                               │                                 │
    │               ┌───────────────┼──────────────┐                  │
    │               ▼               ▼              ▼                  │
    │        ┌──────────┐   ┌────────────┐   ┌─────────┐              │
    │        │ SQLite   │   │  EventBus  │   │ Log nếu │              │
    │        │ (WAL)    │   │"reading_   │   │ emit    │              │
    │        │ record() │   │ recorded"  │   │ fail    │              │
    │        └──────────┘   └──────┬─────┘   └─────────┘              │
    │                              │                                  │
    │                     ┌────────┴────────┐                         │
    │                     ▼                 ▼                         │
    │              ┌───────────┐     ┌──────────────┐                 │
    │              │Rule Engine│     │ Notifier     │                 │
    │              │(8 rules)  │     │ (nếu là      │                 │
    │              │           │     │  alert)      │                 │
    │              └───────────┘     └──────────────┘                 │
    │                              │                                  │
    │                              ▼                                  │
    │                        ┌────────────┐                           │
    │                        │  Telegram  │                           │
    │                        │  Console   │                           │
    │                        │  Webhook   │                           │
    │                        └────────────┘                           │
    │                              │                                  │
    │                              │◄── MCP: fleet.get_history ──── ──┤
    │                              ├─── query SQLite direct ─────────►│
    │                              │◄── MCP: call device tool ────────┤
    │◄── serial/mqtt ──────────────┤── tool result ──────────────────►│
```

### 4.2. Điều khiển actuator

```
User ──"Tưới khu A 20 phút"──► AI Agent
                                    │
                            ┌───────▼────────┐
                            │ Safety         │
                            │ Validator      │
                            │✓ duration ≤ max│
                            │✓ device OK     │
                            │✓ temp in range │
                            └───────┬────────┘
                                    │
                            "Xác nhận tưới khu A 20 phút?"
                                    │
                            User ── "Có"
                                    │
                            ┌───────▼────────┐
                            │ ActuatorLock   │  ← GAP G01
                            │ mutex acquire  │
                            └───────┬────────┘
                                    │
                            MCP: execute_actuator
                                    │
                            LoRa Bridge → SerialQueue  ← GAP G02
                                    │
                            [LoRa] ──► Actuator Node
                                    │       │
                                    │       ├─ kích relay
                                    │       └─ trả ACK
                                    │
                            SQLite: actuation_log
                                    │
                            AI Agent ──► "Đã bật bơm khu A."
```

> ⚠️ **GAP G01 — ActuatorLock:** Nếu 2 lệnh đến cùng lúc từ Telegram và Web, hành vi không xác định. Cần mutex per `actuator_id`, timeout tự giải phóng sau 5 phút.

> ⚠️ **GAP G02 — UART SerialQueue:** Khi poll nhiều node đồng thời, lệnh UART có thể xung đột. Cần FIFO queue, per-command timeout 3s, retry 3 lần.

### 4.3. Health check định kỳ

```
Gateway Daemon (mỗi 10 phút)
    │
    ├──[LoRa] ping ──► Node A ──► PONG trong 30s → status: ONLINE
    ├──[LoRa] ping ──► Node B ──► Không trả lời  → status: OFFLINE → alert
    └──[LoRa] ping ──► Node C ──► PONG trong 30s → status: ONLINE
```

- Node OFFLINE > 2 giờ → push notification cảnh báo pin hoặc hỏng phần cứng.
- AI Scheduled report (sáng/tối) tóm tắt trạng thái toàn bộ hệ thống.

### 4.4. ML Inference — Anomaly Detection (24/7)

```
Sensor stream ──[LoRa]──► Gateway Daemon
                                │
                                ▼
                        ┌──────────────────┐
                        │ SQLite           │
                        │ (lưu mọi reading)│
                        └────────┬─────────┘
                                 │
                          ┌──────┴──────────┐
                          │ Univariate ML   │  ← chạy 24/7, ~10 MB RAM
                          │ Moving Avg ±3σ  │
                          │ Rate of Change  │
                          │ Variance check  │
                          └──────┬──────────┘
                          │ score > ngưỡng?
                    ┌──── yes ────┐  no → bỏ qua
                    ▼
          ┌──────────────────┐
          │ Multivariate ML  │  ← Isolation Forest
          │ (nếu đủ data)    │     ~30 MB RAM
          └──────┬───────────┘
                 │ score > ngưỡng?
           ┌──── yes ────┐  no
           ▼               ▼
    ┌──────────────┐  ┌──────────────┐
    │ Ghi alert    │  │ Bỏ qua       │
    │ SQLite       │  └──────────────┘
    └──────┬───────┘
           │
    ┌──────┴───────┐
    │ Push         │
    │ Telegram     │
    └──────────────┘
```

### 4.5. ML Inference — Predictive + Weather (định kỳ)

```
AgriMeshAI Daemon (mỗi 15 phút)
    │
    ├── [Predictive] ──► LightGBM
    │   ├── Input: sensor history 7 ngày
    │   ├── Output: độ ẩm dự báo 24h
    │   └── Nếu predicted < threshold → gọi AI Agent đề xuất tưới
    │
    ├── [Battery Predict] ──► Linear Regression
    │   ├── Input: pin% 30 ngày
    │   ├── Output: ngày còn lại
    │   └── Nếu < 7 ngày → push "sắp hết pin"
    │
    └── [Weather LSTM] ──► LSTM-TCN (ONNX)
        ├── Input: 30 ngày gần nhất (từ SQLite)
        ├── Output: 48h tới (temp, humidity, rain_prob)
        └── Lưu vào SQLite bảng `weather_forecasts`

AI Agent gọi:
  MCP: get_weather_forecast_local()  → ưu tiên ML, fallback API
  MCP: predict_soil_moisture()       → LightGBM
  MCP: predict_battery_life()        → Linear Regression
```

---

## 5. MCP Server — agrimesh

### 5.1. Tổng quan

MCP Server là trung tâm routing tool giữa AI Agent và hệ thống (SQLite, hardware). Được xây dựng với **lowlevel.Server** (từ `mcp` Python SDK), chạy trên Jetson Nano.

- **Framework:** `mcp.server.lowlevel.Server`
- **Transport:** stdio (cho Agent) + Streamable HTTP (cho Web UI / MCP clients)
- **Entry point:** `python main.py agent` (stdio) / `python main.py daemon` (HTTP)
- **File:** `mcp_server/server.py` (319 dòng) + `mcp_server/fleet.py` (248 dòng)
- **Dependency injection:** Nhận `SystemManager` qua constructor — không tự khởi tạo module nào

### 5.2. Kiến trúc

```
SystemManager (injected via DI)
    │
    ├── device_manager: DeviceManager (discovery, catalog, routing, locks)
    ├── store: ReadingStore (SQLite)
    ├── fleet: FleetTools
    ├── rule_engine: RuleEngine
    ├── notifier: NotifierManager
    ├── event_bus / event_queue: EventBus + EventQueueManager
    └── database_manager: DatabaseManager (write coordinator)
    │
    ▼
AgriMeshAIServer (mcp_server/server.py)
    │
    ├── handle_list_tools()
    │   └── system.list_tools() → device_manager.tools + fleet.tools
    │
    ├── handle_call_tool(name, arguments)
    │   ├── fleet.* → system.call_tool() → FleetTools
    │   │   ├── list_devices       → DeviceManager
    │   │   ├── get_all_readings   → ReadingStore
    │   │   ├── get_history        → ReadingStore
    │   │   └── search_anomalies   → ReadingStore
    │   │
    │   └── còn lại → system.call_tool() → DeviceManager
    │       ├── {device}.{tool} → adapter.send(command)
    │       └── adapter.receive() → return value
    │
    ├── serve_stdio()    → agent mode
    └── serve_http()     → daemon mode (Streamable HTTP, port 8374, endpoint /mcp)
```

### 5.3. Database Schema

Tất cả dữ liệu được lưu trong `data/agrimesh.db` (SQLite WAL mode). Schema hiện tại chỉ có **1 bảng**:

#### readings — Dữ liệu cảm biến time-series

```sql
CREATE TABLE IF NOT EXISTS readings (
    timestamp   REAL    NOT NULL,  -- epoch seconds (float)
    device_id   TEXT    NOT NULL,
    sensor_id   TEXT    NOT NULL,
    value       REAL    NOT NULL,
    unit        TEXT    NOT NULL,
    downsampled INTEGER NOT NULL DEFAULT 0  -- 0=raw, 1=hourly avg
);

CREATE INDEX IF NOT EXISTS idx_readings_device_sensor_time
    ON readings (device_id, sensor_id, timestamp);

CREATE INDEX IF NOT EXISTS idx_readings_downsampled
    ON readings (downsampled, timestamp);
```

Không có bảng `alerts`, `devices`, `actuation_log` riêng — device registry đọc từ TOML profiles, alert qua EventBus, actuation chưa có log.

### 5.4. Retention Policy

Dữ liệu cũ được dọn qua `database_manager/retention.py`:

| Khoảng thời gian | Độ chi tiết | downsampled flag |
|------------------|-------------|------------------|
| 0-30 ngày | Raw | `0` |
| 30 ngày - 1 năm | Hourly average | `1` |
| > 1 năm | Xóa | — |

Chạy mỗi 6 giờ trong daemon loop qua `run_cleanup(store, full_res_days=30, keep_downsampled_days=365)`.

### 5.5. Device Discovery

Thiết bị được định nghĩa qua file TOML trong `device_manager/device_profiles/`:

```toml
[device]
name = "farm_sensor"
description = "Mock soil moisture & temperature sensor"

[connection]
protocol = "mock"         # mock | serial | mqtt

[connection.mock_responses]
READ = "25.3"
PING = "PONG"

[[tools]]
name = "get_moisture"
description = "Get current soil moisture"
command = "READ"
[tools.returns]
type = "float"
unit = "percent"

[recording]
enabled = true
poll_interval_ms = 5000
```

Discovery flow (qua `device_manager/`):
```
profiles/**/*.toml → profile_parser.py → DeviceModel (Pydantic)
                       → tool_builder.py → MCP Tool[] (namespaced: device.tool)
                       → discovery.py → DiscoveredDevice + Adapter
                       → catalog.py → DeviceCatalog (tools, routes, devices, locks)
```

### 5.6. DeviceManager (thay thế Aggregator cũ)

DeviceManager consolidate device lifecycle, catalog building, connection lifecycle, tool routing, và health checks:

```python
class DeviceManager:
    async def connect_all() -> dict[str, AdapterResult]
    async def disconnect_all() -> dict[str, AdapterResult]
    async def call_tool(namespaced_name, arguments) -> AdapterResult
    async def health_check_all() -> dict[str, AdapterResult]

    # Per-device asyncio.Lock để tránh interleaved send/receive
    # Tool namespace: {device_name}.{tool_name} (vd: "farm_sensor.get_temperature")
```

### 5.7. Adapters

| Adapter | Protocol | Khi nào dùng |
|---------|----------|-------------|
| **MockAdapter** | In-memory | Testing, development |
| **SerialAdapter** | UART (pyserial-asyncio) | ESP32, Arduino qua USB/UART |
| **MQTTAdapter** | MQTT (paho-mqtt) | WiFi devices (Pico W, ESP32) |

Base interface (tại `utils/adapters/base.py`):
```python
class BaseAdapter:
    async connect() -> AdapterResult
    async disconnect() -> AdapterResult
    async send(data) -> AdapterResult
    async receive(length?, timeout?) -> AdapterResult
    async health_check() -> AdapterResult
```

### 5.8. Entry point — main.py

| Command | Transport | Background tasks | Use case |
|---------|-----------|-----------------|----------|
| `python main.py agent` | In-process tool bridge (không MCP transport) | ❌ | AI Agent interactive REPL |
| `python main.py daemon` | Streamable HTTP :8374 | retention + missing_data | Production 24/7 |
| `python main.py status` | — | — | Kiểm tra hệ thống |

### 5.9. MCP Tools Đầy Đủ

#### Nhóm Fleet — Truy vấn dữ liệu tổng hợp

| MCP Tool | Parameters | Mô tả | Data Source |
|----------|-----------|-------|-------------|
| `fleet.list_devices` | None | Danh sách thiết bị + trạng thái | DeviceManager |
| `fleet.get_all_readings` | None | Dữ liệu cảm biến mới nhất (tất cả) | ReadingStore |
| `fleet.get_history` | `device_id, sensor_id, hours?, limit?` | Lịch sử dữ liệu | ReadingStore |
| `fleet.search_anomalies` | `threshold_sigma?, baseline_days?` | Phát hiện bất thường (statistical) | ReadingStore |

#### Nhóm Device — Điều khiển thiết bị

Các tool được sinh tự động từ TOML profiles, namespace theo `{device_name}.{tool_name}`:
- `farm_sensor.get_moisture` → command "READ" → float
- `farm_sensor.get_temperature` → command "READ" → float
- `mqtt_sensor.get_temperature` → command "READ_TEMP" → float
- `serial_sensor.get_humidity` → command "READ_HUMID" → float
- V.v. (tùy theo profile định nghĩa)

---

## 6. AI Agent

### 6.1. Tổng quan

AI Agent là lớp giao tiếp với người dùng, chạy trên Jetson Nano. Agent sử dụng **edge-agent framework** (vendored, Python thuần, không dependencies ngoài) để kết nối LLM (trên PC) và MCP tools (in-process).

- **Framework:** edge-agent (`agent/src/`, 14 files)
- **LLM Provider:** OllamaProvider qua Tailscale → PC (Qwen2.5 7B), dùng `urllib` stdlib (không cần openai-python)
- **Tool bridge:** In-process — tools được bridge từ SystemManager thành `Agent.Tool` objects, **không qua MCP transport**, không subprocess
- **Giao diện:** Interactive REPL (Session) trong `main.py`
- **Entry point:** `python main.py agent`
- **Agent types hỗ trợ:** agent, guardrail, router, evaluator, fallback

### 6.2. Luồng hoạt động

```
1. main.py: SystemManager.start() → tools list
2. _build_tool_bridge() → chuyển MCP Tool[] thành Agent.Tool[]
3. Agent(system_instructions, tools, provider)
4. Session.start() → REPL loop
5. User nhập query → Agent.run() → LLM (Ollama API)
6. LLM trả về tool_calls → _execute_tool() (gọi trực tiếp SystemManager, không qua MCP)
7. Kết quả gửi lại LLM → LLM trả lời tiếng Việt
```

### 6.3. Caching & Memory

edge-agent sử dụng message-based memory:
- Tất cả messages (system, user, assistant, tool_result) được lưu trong list
- Session giữ history qua các turn
- `max_turns = 10` giới hạn số lần tool call loop
- Không có persistent memory (sẽ lose sau khi thoát)

### 6.4. Tools

Agent có quyền truy cập N tools (số lượng tùy theo TOML profiles + fleet tools):

| Tool | Mô tả |
|------|-------|
| `fleet.list_devices` | Liệt kê thiết bị + trạng thái |
| `fleet.get_all_readings` | Dữ liệu cảm biến mới nhất từ SQLite |
| `fleet.get_history` | Lịch sử dữ liệu (device_id, sensor_id, hours, limit) |
| `fleet.search_anomalies` | Phát hiện bất thường ±σ |
| `{device}.{tool}` | Tool thiết bị (vd: `farm_sensor.get_temperature`) |

### 6.5. Offline Behavior

Khi LLM Server không reachable (Jetson mất kết nối Tailscale/Internet), edge gateway vẫn hoạt động:

| Thành phần | Online | Offline |
|-----------|--------|---------|
| AI Agent | ✅ Chat + tool calling | ❌ Báo lỗi kết nối |
| MCP Server (daemon) | ✅ Tool routing + HTTP | ✅ Vẫn chạy |
| Background recorder | ✅ Poll sensors | ✅ Poll sensors |
| SQLite ghi dữ liệu | ✅ | ✅ |
| Rule Engine | ✅ Threshold alerts | ✅ Threshold alerts |
| Notifier | ✅ Push notification | ✅ Push notification |

### 6.6. Lưu ý

- **Query bằng English** — Qwen2.5 tool calling ổn định hơn với English
- **Trả lời bằng tiếng Việt** — `instructions.txt` yêu cầu "Reply in Vietnamese ONLY"
- **Thoát:** `exit` hoặc `quit`
- **Temperature:** 0.01 (deterministic) — có thể cấu hình trong `config/models.yaml`
- **Tool bridge in-process:** tools gọi trực tiếp `SystemManager.call_tool()` — không có overhead MTP transport

---

## 7. An Toàn và Bảo Mật

### 7.1. Safety 3 lớp

| Lớp | Nơi thực thi | Cơ chế | Bypass? |
|-----|-------------|--------|---------|
| Lớp 1 — Hardware | Firmware ESP32 | Watchdog timer tự reset, max runtime relay hard-coded, nhiệt độ cut-off | Không thể |
| Lớp 2 — Logic | MCP Safety Validator (Python) | Kiểm tra params trước khi gửi LoRa, reject lệnh vượt giới hạn, ActuatorLock mutex | Không — luôn chạy |
| Lớp 3 — Semantic | AI Agent (LLM) | Hỏi lại nếu lệnh mơ hồ, xác nhận trước khi thực thi | Có — nhưng user phải confirm |

### 7.2. Safety rules mặc định

| Rule | Giá trị mặc định | Configurable? |
|------|-----------------|--------------|
| Max duration bơm / van | 30 phút | Có — trong device config |
| Max nhiệt độ môi trường để tưới | 45°C | Có |
| Min pin node để nhận lệnh actuator | 15% | Có |
| Cooldown giữa 2 lần tưới cùng zone | 30 phút | Có |
| Max concurrent actuators hoạt động | 3 | Có |
| Thời gian chờ ACK từ node | 30 giây | Không |
| Số lần retry khi không có ACK | 3 lần | Không |

### 7.3. OTA Firmware Update (Node ESP32)

Tính năng rủi ro cao — firmware bị brick giữa đồng ruộng không thể sửa từ xa. Cần quy trình chặt chẽ:

| Bước | Mô tả | Điều kiện bắt buộc |
|------|-------|-------------------|
| 1. Upload firmware | Gateway nhận file `.bin` từ user qua web UI | Kiểm tra SHA256 checksum |
| 2. Broadcast thông báo OTA | Gateway broadcast `OTA_AVAILABLE` packet | Chỉ broadcast khi không có actuator đang chạy |
| 3. Node xác nhận | Node kiểm tra pin > 50%, trả `OTA_READY` | Node từ chối nếu pin thấp |
| 4. Chuyển file | Gateway gửi firmware theo chunks 200 byte | Retry từng chunk nếu NACK |
| 5. Verify & apply | Node verify SHA256 toàn bộ file, ghi vào OTA partition | Rollback về firmware cũ nếu verify fail |
| 6. Reboot & report | Node reboot, join lại mesh, báo version mới | Gateway chờ 60s, ping xác nhận |

> ⚠️ **GAP G07:** OTA là GAP lớn trong v1.0 — được đề cập nhưng hoàn toàn chưa có thiết kế. Nên xếp vào Phase 4 (sau khi hệ thống cơ bản ổn định).

### 7.4. Bảo mật truyền thông

| Kênh | Biện pháp | Mức độ ưu tiên |
|------|-----------|---------------|
| LoRa Mesh — Actuator commands | **AES-256 bắt buộc** (flag Encrypted = 1) | 🔴 Cao — bắt buộc |
| LoRa Mesh — Sensor data | AES-256 tùy chọn (mặc định tắt để tiết kiệm năng lượng) | 🟡 Thấp |
| UART Gateway ↔ LoRa Module | Physical security (cùng thiết bị) | — |
| Web UI (port 8374) | Basic Auth + HTTPS (self-signed cert) | 🔴 Cao |
| Telegram Bot | Bot token bảo vệ, whitelist `chat_id` | 🔴 Cao |
| SSH Gateway | Key-based auth, disable password auth | 🔴 Cao |

> ⚠️ **GAP G06:** v1.0 mô tả AES-256 là "tùy chọn" cho cả sensor và actuator. Cần tách biệt: actuator commands **phải** mã hóa (rủi ro replay attack gây tưới sai), sensor data có thể tùy chọn.

---

## 8. Phát Hiện Bất Thường — Rule Engine

### 8.1. Kiến trúc (Event-driven)

```
Sensor Reading (từ recorder hoặc tool call)
    │
    ▼
EventQueueManager → DatabaseManager → SQLite
    │
    ▼
EventBus.emit("reading_recorded")
    │
    ▼
Rule Engine (8 rules, 5-min cooldown)
    │
    ▼
EventBus.emit("alert_triggered")
    │
    ▼
NotifierManager (console, telegram, webhook)
    │
    ▼
Push notification → người dùng
```

### 8.2. Danh sách rules đã triển khai

| Rule ID | Loại | Sensor | Điều kiện | Severity |
|---------|------|--------|-----------|----------|
| R01 | Threshold | temperature | `> 40°C` | CRITICAL |
| R02 | Threshold | temperature | `< 5°C` | CRITICAL |
| R03 | Threshold | moisture | `< 20%` (khô) | WARNING |
| R04 | Threshold | moisture | `> 80%` (ngập) | WARNING |
| R05 | Threshold | battery | `< 20%` | WARNING |
| R06 | Threshold | humidity | `> 90%` (nấm mốc) | WARNING |
| R07 | Threshold | humidity | `< 30%` (khô) | WARNING |
| R08 | Rate of change | temperature | `> 5°C/h` | WARNING |
| R09 | Missing data | — | No data > 1h | WARNING |

Rules được định nghĩa trong `config/rules.yaml`, có thể hot-reload. Alert deduplication qua cooldown 5 phút.

> **Lưu ý:** Rules R07-R09 trong doc cũ (baseline deviation, actuator overtime, correlation anomaly) chưa triển khai. R09 ở đây là missing data check (chạy timer-based trong daemon loop).

---

## 9. Machine Learning

> ⚠️ **Hiện trạng:** Các ML models dưới đây là thiết kế mục tiêu. Codebase hiện tại mới chỉ có statistical anomaly detection (baseline deviation ±σ qua SQLite) trong `ReadingStore.search_anomalies()`. LightGBM, LSTM-TCN, ONNX chưa được tích hợp.

> 📖 **Tài liệu chi tiết từng tác vụ ML được mô tả trong thư mục `docs/machine_learning/`:**
> - [`01-anomaly-detection.md`](machine_learning/01-anomaly-detection.md) — Univariate + Multivariate Anomaly Detection
> - [`02-predictive.md`](machine_learning/02-predictive.md) — Dự đoán sensor (độ ẩm, nhiệt, pin)
> - [`03-weather-forecasting.md`](machine_learning/03-weather-forecasting.md) — Dự báo thời tiết LSTM-TCN từ NASA POWER

Hệ thống có 3 tầng xử lý. ML nằm giữa Rule Engine (đơn giản, cứng nhắc) và LLM (chậm, nặng):

```
┌──────────────────────────────────────────────────────────────┐
│              BA TẦNG XỬ LÝ TRONG HỆ THỐNG                    │
│                                                              │
│  ┌─────────────────┬──────────────────┬──────────────────┐   │
│  │   RULE ENGINE   │       ML         │       LLM        │   │
│  │  (if/else)      │  (dự đoán, phát  │  (suy luận,      │   │
│  │                 │  hiện bất thường)│   ngôn ngữ)      │   │
│  ├─────────────────┼──────────────────┼──────────────────┤   │
│  │ Nhanh (<1ms)    │ Dự đoán được     │ Linh hoạt        │   │
│  │ Rẻ (0% CPU)     │ Nhẹ (<5% CPU)    │ Hiểu ngữ cảnh    │   │
│  │ Cứng nhắc       │ Cần data huấn    │Chậm (5-30s)      │   │
│  │ Không dự đoán   │   luyện          │ Nặng (>1GB)      │   │
│  └─────────────────┴──────────────────┴──────────────────┘   │
└──────────────────────────────────────────────────────────────┘
```

### 9.1. Nguyên tắc chọn tác vụ cho ML

| Nên dùng ML khi | Không nên dùng ML khi |
|-----------------|----------------------|
| Cần phát hiện bất thường tinh vi (deviation từ baseline) | Có thể dùng if/else đơn giản |
| Cần dự đoán tương lai (độ ẩm, nhiệt độ) | Cần safety/absolute (ngưỡng chết người) |
| Phân loại tín hiệu phức tạp | Cần giải thích bằng ngôn ngữ tự nhiên |
| Dữ liệu có tính chu kỳ, lặp lại | Dataset quá nhỏ (< 100 mẫu) |

### 9.2. Tác vụ ML 1: Phát hiện bất thường (Anomaly Detection)

**Vấn đề:** Rule engine dùng threshold cứng (`nhiệt > 40°C`) — bỏ sót bất thường trong ngưỡng (vd: nhiệt 37°C nhưng tăng 5°C/giờ).

**Giải pháp:** Học baseline tự động, phát hiện độ lệch.

```
Sensor stream 24/7
    │
    ▼
┌─────────────────────────────┐
│ Baseline Calculator (nhẹ)   │  ← Moving Average ± 3σ
│ RAM: ~5 MB, CPU: <1%        │     20 dòng Python
└──────────┬──────────────────┘
           │ deviation score
           ▼
┌─────────────────────────────┐
│ Anomaly Detector (trung bình)│  ← Isolation Forest / Autoencoder
│ RAM: ~50 MB, CPU: ~2%       │     Chạy mỗi 5 phút
└──────────┬──────────────────┘
           │ alert
           ▼
┌─────────────────────────────┐
│ Rule Engine + LLM           │  ← Nếu cần phân tích sâu
│ (threshold cứng cho safety) │     gọi AI Agent
└─────────────────────────────┘
```

**Các luật phát hiện mới nhờ ML:**

| ML Rule | Mô tả | Model | So với rule cũ |
|---------|-------|-------|----------------|
| `M01` | Độ lệch > 3σ so với baseline 7 ngày | Moving Average ± 3σ | Phát hiện sớm hơn threshold |
| `M02` | Giá trị không đổi > 12h (stuck sensor) | Variance threshold | Rule cũ: 6h → dương tính giả |
| `M03` | Rate of change bất thường | Linear Regression slope | Mới hoàn toàn |
| `M04` | Tương quan 2 sensor đảo chiều | Cross-correlation | Mới hoàn toàn |
| `M05` | Bất thường đa chiều (nhiệt + ẩm + áp) | Isolation Forest | Mới hoàn toàn |

**Hiệu quả kỳ vọng:**

| Chỉ số | Rule engine | + ML |
|--------|-------------|------|
| Precision (cảnh báo đúng) | ~70% | ~90% |
| Recall (phát hiện được) | ~60% | ~95% |
| Thời gian phát hiện sớm | — | +2-6 giờ sớm hơn |

### 9.3. Tác vụ ML 2: Dự đoán (Predictive)

**Các dự đoán có giá trị:**

| Dự đoán | Input | Output | Model | RAM |
|---------|-------|--------|-------|-----|
| Độ ẩm đất 6-24h tới | Độ ẩm 7 ngày, nhiệt độ, lượng mưa | % | LightGBM | ~50 MB |
| Nhiệt độ tank 2h tới | Nhiệt độ 24h, giờ trong ngày | °C | Linear Regression | ~5 MB |
| Khi nào cần tưới | Độ ẩm, tốc độ khô, dự báo mưa | giờ | Prophet | ~100 MB |
| Thời gian pin còn lại | Pin 30 ngày, số lần gửi | ngày | Simple Regression | ~5 MB |

**Ví dụ — Dự đoán độ ẩm → tưới chủ động:**

```
Rule engine:  Độ ẩm = 25% → tưới                     (phản ứng)

Với ML:       "6h nữa độ ẩm sẽ xuống 20% → tưới ngay" (chủ động)
              → Tưới lúc rẻ điện, trước khi cây bị stress
```

**Cách tích hợp:** ML model được expose như MCP tool:

```python
@mcp.tool
def predict_soil_moisture(node_id: str, hours_ahead: int = 24) -> dict:
    """
    Dự đoán độ ẩm đất X giờ tới.
    Input: lịch sử sensor 7 ngày + dự báo thời tiết
    Model: LightGBM (train sẵn, ~50 MB RAM)
    """
    features = extract_features(node_id)
    prediction = model.predict(features)
    return {"hours": hours_ahead, "predicted": prediction}
```

**AI Agent gọi như thế nào:**

```
User: "Có cần tưới hôm nay không?"

AI Agent:
  ├── MCP: predict_soil_moisture(node=soil_01, 24h)
  │   ├── ML: query SQLite 7 ngày
  │   ├── ML: fetch weather forecast (Open-Meteo)
  │   └── ML: predict → "18h tới sẽ xuống 22%"
  │
  ├── MCP: get_weather_forecast(lat, lon, 2 days)
  │
  ├── Safety: ngưỡng tưới là < 25% → 22% là dưới ngưỡng
  │
  └── "Độ ẩm hiện 35%. Dự báo 18h tới sẽ xuống 22%
       (dưới ngưỡng 25%). Khuyến nghị tưới nhẹ 10 phút
       ngay bây giờ — trước khi cây bị stress."
```

### 9.4. Tác vụ ML 3: Dự đoán thời tiết (Weather Forecasting)

Dùng LSTM-TCN (Long Short-Term Memory + Temporal Convolutional Network) hybrid để dự đoán thời tiết **cục bộ tại ruộng**, dựa vào dữ liệu NASA POWER — miễn phí, toàn cầu, từ 1981 đến nay.

#### Lợi ích

| Tiêu chí | Open-Meteo API (hiện tại) | LSTM-TCN local |
|----------|--------------------------|----------------|
| Cần Internet | ✅ Có | ❌ **Không** |
| Độ phân giải | ~11 km grid | ✅ **Chính xác tại ruộng** |
| Dự báo vi khí hậu | ❌ Không | ✅ **Có** |
| Chi phí | Miễn phí (giới hạn) | ✅ **0đ** |

#### Lấy dữ liệu NASA POWER

```python
import requests
import pandas as pd

# Ví dụ: toạ độ tại Đắk Lắk
LAT, LON = 12.5, 108.0
START = "2020-01-01"
END = "2025-06-01"

url = (
    f"https://power.larc.nasa.gov/api/temporal/daily/point?"
    f"parameters=T2M,RH2M,PRECTOTCORR,PS,ALLSKY_SFC_SW_DWN"
    f"&community=RE&longitude={LON}&latitude={LAT}"
    f"&start={START}&end={END}&format=JSON"
)

resp = requests.get(url)
data = resp.json()["properties"]["parameter"]

df = pd.DataFrame({
    "date": pd.to_datetime(list(data["T2M"].keys())),
    "temp": list(data["T2M"].values()),           # °C
    "humidity": list(data["RH2M"].values()),       # %
    "rain": list(data["PRECTOTCORR"].values()),    # mm/ngày
    "pressure": list(data["PS"].values()),         # kPa
    "solar": list(data["ALLSKY_SFC_SW_DWN"].values()),  # Wh/m²
})
```

**Các tham số có thể lấy từ NASA POWER:**

| Tham số | Ý nghĩa | Đơn vị |
|---------|---------|--------|
| `T2M` | Nhiệt độ 2m | °C |
| `RH2M` | Độ ẩm | % |
| `PRECTOTCORR` | Lượng mưa | mm/ngày |
| `PS` | Áp suất bề mặt | kPa |
| `ALLSKY_SFC_SW_DWN` | Bức xạ mặt trời | Wh/m² |
| `WS2M` | Tốc độ gió | m/s |
| `T2M_MAX`/`T2M_MIN` | Nhiệt độ max/min | °C |

#### Mô hình LSTM-TCN

```python
import torch
import torch.nn as nn

class LSTMTCN(nn.Module):
    """Hybrid LSTM + TCN cho dự báo thời tiết.
    
    Input:  30 ngày (temp, humidity, rain, pressure, solar)
    Output: 24h tới (temp, humidity, rain_prob)
    """
    def __init__(self, input_dim=5, hidden=64, num_layers=2):
        super().__init__()
        self.lstm = nn.LSTM(input_dim, hidden, num_layers, batch_first=True)
        self.conv = nn.Conv1d(hidden, hidden, kernel_size=3, padding=1)
        self.fc = nn.Linear(hidden, 3)

    def forward(self, x):
        x, _ = self.lstm(x)
        x = x[:, -1, :]
        x = self.conv(x.unsqueeze(-1)).squeeze(-1)
        return self.fc(x)
```

**Thông số model:**
- Kích thước: ~5-10 MB (ONNX quantized)
- RAM inference: ~50-80 MB
- CPU: ~5% mỗi 15 phút (Jetson Nano)
- Training: trên laptop với 5-10 năm NASA POWER

#### Pipeline training và deploy

```
NASA POWER (5-10 năm)
  ├── Tại toạ độ ruộng (12.5°N, 108.0°E)
  ├── Daily: temp, humidity, rain, pressure, solar
  └── Input: 30 ngày → Output: 24h tới

                        ▼
              ┌────────────────────┐
              │ Train LSTM-TCN     │
              │ trên laptop        │
              │ (PyTorch)          │
              └─────────┬──────────┘
                        │ export ONNX + quantize
                        ▼
              ┌────────────────────┐
              │ Deploy xuống       │
              │ gateway            │
              │ ONNX Runtime       │
              └─────────┬──────────┘
                        │
          ┌─────────────┴─────────────┐
          │                           │
          ▼                           ▼
  ┌─────────────────┐    ┌──────────────────────┐
  │ Mới deploy      │    │ Có sensor data > 3   │
  │ Chỉ dùng NASA   │    │ tháng                │
  │ Dự báo ±3°C     │    │ Fine-tune với data   │
  │                 │    │ thực tế tại ruộng    │
  │ get_weather_    │    │ Dự báo ±1°C          │
  │ forecast_local()│    │                      │
  └─────────────────┘    └──────────────────────┘
```

Khi có sensor data thực tế, model sẽ được fine-tune để học microclimate riêng của ruộng — dưới tán cây, gần ao, chân đồi...

#### Các nguồn dữ liệu cho Việt Nam

| Nguồn | Phạm vi | Độ phân giải | Phí | Phù hợp |
|-------|---------|-------------|-----|---------|
| **NASA POWER** | Toàn cầu | 0.5° (~50km) | Miễn phí | ✅ Tốt nhất cho edge |
| Open-Meteo Historical | Toàn cầu | ~11km | Miễn phí | ✅ Chi tiết hơn |
| ERA5 (Copernicus) | Toàn cầu | 0.25° (~27km) | Miễn phí (đăng ký) | ⚠️ Nặng, cần xử lý |
| Đài KTTV VN | Việt Nam | Theo trạm | Miễn phí (khó lấy) | ❌ Không có API |

#### So sánh model cho weather forecasting trên edge

| Model | RAM | Độ chính xác | Inference | Phù hợp |
|-------|-----|-------------|-----------|---------|
| **LSTM-TCN** | ~80 MB | ✅✅ Cao nhất | ~100ms | ⚠️ Cần Jetson Nano (có GPU) |
| Prophet | ~100 MB | ✅ Tốt | ~500ms | ⚠️ Nặng |
| LightGBM | ~30 MB | ⚠️ Trung bình | ~10ms | ✅ Rất nhẹ |
| Simple Moving Avg | ~1 MB | ❌ Thấp | <1ms | ✅ Nhẹ nhất |

Khuyến nghị: **LSTM-TCN** nếu có Jetson Nano (tận dụng GPU CUDA) và cần độ chính xác cao. **LightGBM** nếu cần nhẹ hơn, chạy CPU.

#### Tích hợp vào MCP

```python
@mcp.tool
def get_weather_forecast_local(hours: int = 48) -> dict:
    """
    Dự đoán thời tiết tại ruộng dựa trên LSTM-TCN.
    Fallback: Open-Meteo API khi có Internet.
    """
    if model_available:
        features = build_features_from_sqlite(days=30)
        pred = lstm_tcn.predict(features)
        return {
            "temp": pred[0], "humidity": pred[1],
            "rain_prob": pred[2], "source": "local_ml"
        }
    else:
        return get_weather_forecast_from_api(...)
```

AI Agent gọi tool này thay vì Open-Meteo API, ưu tiên dự báo local.

### 9.6. Kiến trúc tổng thể tích hợp ML

```
┌─────────────────────────────────────────────────────────────┐
│              EDGE GATEWAY (Jetson Nano 4GB)                 │
│                                                             │
│  ┌────────────────┐  ┌──────────────┐  ┌──────────────────┐ │
│  │ ML Inference   │  │  LLM Agent   │  │  Rule Engine     │ │
│  │ Server         │  │  (qwen2.5)   │  │  (threshold)     │ │
│  │                │  │              │  │                  │ │
│  │ + Baseline     │  │ + Phân tích  │  │ + Cảnh báo tức   │ │
│  │ + Anomaly      │  │ + Multi-step │  │   thì            │ │
│  │ + Dự đoán      │  │ + Trả lời    │  │ + Safety cutoff  │ │
│  │ + Weather      │  │   user       │  │ + Kill switch    │ │
│  │                │  │              │  │                  │ │
│  │ RAM: ~150 MB   │  │ RAM: ~1.5 GB │  │ RAM: ~5 MB       │ │
│  │ CPU: ~10%      │  │ CPU: ~30%    │  │ CPU: <1%         │ │
│  └────────┬───────┘  └──────┬───────┘  └────────┬─────────┘ │
│           │                 │                   │           │
│           └──────┬──────────┴───────────────────┘           │
│                  │                                          │
│             ┌────┴────┐                                     │
│             │  MCP    │                                     │
│             │ Gateway │                                     │
│             └─────────┘                                     │
└─────────────────────────────────────────────────────────────┘
```

ML và Rule Engine chạy 24/7. LLM chỉ chạy khi cần.

### 9.7. Lộ trình tích hợp ML

| Giai đoạn | Tác vụ | Model | RAM | Phức tạp | Thời gian |
|-----------|--------|-------|-----|---------|-----------|
| **P1** | Anomaly detection cơ bản | Moving Average ± 3σ | ~5 MB | Thấp | 1 ngày |
| **P2** | Baseline tự động + stuck sensor | Var threshold + slope | ~10 MB | Thấp | 2 ngày |
| **P3** | Dự đoán độ ẩm đất | LightGBM (train sẵn) | ~50 MB | Trung bình | 5 ngày |
| **P4** | Anomaly đa chiều | Isolation Forest | ~30 MB | Trung bình | 3 ngày |
| **P5** | Dự đoán thời tiết local | LSTM-TCN (từ NASA POWER) | ~80 MB | Trung bình | 7 ngày |

### 9.8. Yêu cầu phần mềm

```bash
# ML core
pip install scikit-learn numpy pandas

# Nếu cần dự đoán nâng cao
pip install lightgbm prophet
```

---

## 10. Kết Nối Người Dùng

### 10.1. Notifier Module (đã triển khai)

Alert được gửi qua `notifier/` module, subscribe `alert_triggered` event từ EventBus:

| Channel | File | Giao thức | Trạng thái |
|---------|------|-----------|-----------|
| **Console** | `notifier/console.py` | stderr với severity coloring | ✅ Luôn bật |
| **Telegram** | `notifier/telegram.py` | HTTP Bot API (httpx) | ✅ Config qua env var |
| **Webhook** | `notifier/webhook.py` | HTTP POST JSON | ✅ URL + headers tùy chỉnh |
| **SMS** | `notifier/sms.py` | AT commands qua serial (SIM800/SIM7600) | ✅ Cần pyserial-asyncio |

Cấu hình trong `config/notifiers.yaml`:

```yaml
notifiers:
  console:
    enabled: true
  telegram:
    enabled: false
    bot_token: "${TELEGRAM_BOT_TOKEN}"
    chat_id: "${TELEGRAM_CHAT_ID}"
  webhook:
    enabled: false
    url: "https://hooks.example.com/agrimesh"
  sms:
    enabled: false
    port: "/dev/ttyUSB2"
    baud_rate: 115200
    to: "+84901234567"
```

### 10.2. Web UI

Chưa triển khai. MCP server có sẵn HTTP endpoint (`/mcp` port 8374, Streamable HTTP) để Web UI tương lai kết nối.

### 10.3. Conversation Design — Intent mapping

| Intent | Ví dụ câu | MCP tools gọi | Cần xác nhận? |
|--------|-----------|--------------|--------------|
| Đọc cảm biến | "Độ ẩm khu A?" | `fleet.get_all_readings` / `{device}.{tool}` | Không |
| Xem lịch sử | "Tuần này mưa nhiều không?" | `fleet.get_history` | Không |
| Hỏi bất thường | "Có gì lạ không?" | `fleet.search_anomalies` + `get_history` | Không |
| Danh sách thiết bị | "Có node nào đang online?" | `fleet.list_devices` | Không |
| Điều khiển | "Tưới khu A 20 phút" | tool device → actuator | **Có ✅** |

### 10.4. Conversation State Management

```
chat_id: "telegram_123456"
├── pending_confirmation:
│     action: execute_actuator
│     params: { node_id: 3, duration: 20min }
│     expires_at: now + 5min
└── context_history: [last 10 messages]
```

> ⚠️ **GAP G05:** v1.0 chưa thiết kế conversation state. Nếu AI hỏi xác nhận và người dùng trả lời "Có" sau 30 giây, AI cần nhớ context. Cần thêm `ConversationSession` với timeout 5 phút lưu `pending_confirmations` per `chat_id`.

---

## 11. Giao Thức LoRa Mesh

> ⚠️ **Hiện trạng:** Các protocol và packet format dưới đây là thiết kế mục tiêu cho ESP32 firmware. Codebase hiện tại chưa có firmware ESP32 — chỉ có gateway adapter (SerialAdapter) để giao tiếp với LoRa module qua UART.

### 11.1. Cấu trúc gói tin mesh

```
┌──────┬──────┬──────┬───────┬─────┬─────┬───────────┬────────┐
│ Dest │  Src │  Seq │ Flags │ TTL │ Cmd │  Payload  │ CRC16  │
│  2 B │  2 B │  2 B │   1 B │ 1 B │ 1 B │  0–240 B  │   2 B  │
└──────┴──────┴──────┴───────┴─────┴─────┴───────────┴────────┘
```

**Flags:**
- `Bit 0` — ACK required
- `Bit 1` — Encrypted (AES-256)
- `Bit 2` — Broadcast
- `Bit 3–7` — Reserved

**Dest = 0xFFFF** → Broadcast toàn mạng.

### 11.2. UART Binary Protocol (Gateway ↔ LoRa Module)

```
┌────────────┬──────────┬─────────┬──────────────┬───────────┐
│ Start byte │  Length  │ Command │   Payload    │ Checksum  │
│    0x7E    │   2 B    │   1 B   │   0–250 B    │    1 B    │
│  (fixed)   │ (LE u16) │         │              │ (XOR all) │
└────────────┴──────────┴─────────┴──────────────┴───────────┘
```

| Mã | Lệnh | Hướng | Mô tả |
|----|------|-------|-------|
| `0x01` | `SEND_TO` | Gateway → Module | Gửi tới node cụ thể |
| `0x02` | `BROADCAST` | Gateway → Module | Broadcast toàn mạng |
| `0x03` | `REQUEST_DATA` | Gateway → Module | Yêu cầu đọc sensor |
| `0x04` | `PING` | Gateway → Module | Ping một node |
| `0x05` | `TOPOLOGY` | Gateway → Module | Yêu cầu cấu trúc mạng |
| `0x06` | `NODE_STATUS` | Gateway → Module | Yêu cầu trạng thái node |
| `0x10` | `DATA_RESPONSE` | Module → Gateway | Dữ liệu sensor trả về |
| `0x11` | `PONG` | Module → Gateway | Phản hồi ping |
| `0x12` | `ACK` | Module → Gateway | Xác nhận lệnh thành công |
| `0x13` | `NACK` | Module → Gateway | Lệnh thất bại (kèm error code) |
| `0x20` | `OTA_CHUNK` | Gateway → Module | Chunk firmware OTA |
| `0xFF` | `ERROR` | Module → Gateway | Lỗi hệ thống |

### 11.3. Payload formats

**Sensor Data** (Node → Gateway):
```
┌───────┬───────────┬─────────┬───────────┐
│ Count │ Sensor ID │  Value  │ Timestamp │
│  1 B  │    1 B    │ 4 B f32 │  4 B u32  │
└───────┴───────────┴─────────┴───────────┘
```

**Actuator Command** (Gateway → Node):
```
┌──────┬─────┬──────────┬──────────┐
│ Addr │ Cmd │  Params  │ Duration │
│  2 B │ 1 B │ 0–32 B   │   2 B    │
└──────┴─────┴──────────┴──────────┘
```

**Status Report** (Node → Gateway):
```
┌─────────┬──────┬────────┬────────┬───────┐
│ Battery │ RSSI │ Uptime │ Errors │  Temp │
│  1 B %  │ 1 B  │  2 B h │  1 B   │ 2 B   │
└─────────┴──────┴────────┴────────┴───────┘
```

### 11.4. Mesh Routing

- **Giao thức:** Distance-vector routing (LoRaMesher)
- **TDMA:** Node được cấp slot thời gian để gửi, tránh collision
- **Auto-join:** Node mới tự động gia nhập mạng
- **Self-healing:** Khi node chết, route tự tìm đường khác
- **Phạm vi:** 2–3 km với multi-hop (tùy địa hình)

---

## 12. Kế Hoạch Triển Khai

### 12.1. Yêu cầu

| Yêu cầu | PC | Jetson Nano |
|---------|-----|-------------|
| **OS** | Ubuntu 22.04 | JetPack R32.7.6 (Ubuntu 22.04) |
| **GPU** | RTX 3050 (6GB VRAM) | Maxwell 128-core (CUDA 10.2) |
| **RAM** | 16GB | 4GB |
| **Storage** | — | 22GB free |
| **Python** | 3.10+ | 3.10 |
| **Ollama** | ✅ Cần | ❌ Không cần |
| **Tailscale** | ✅ Cần | ✅ Cần |

### 12.2. Modules đã hoàn thành

| Module | Công nghệ | Trạng thái | Ghi chú |
|--------|-----------|-----------|---------|
| SystemManager | `system/manager.py` | ✅ | Central orchestrator + DI + health check |
| EventBus | `event_bus/` | ✅ | Pub/sub sync + async queue + DLQ + retry |
| DeviceManager | `device_manager/` | ✅ | Discovery, catalog, routing, per-device lock |
| MCP Server | `mcp_server/` | ✅ | stdio + Streamable HTTP, fleet tools |
| AI Agent | `agent/src/` | ✅ | edge-agent, OllamaProvider, Session REPL |
| ReadingStore | `database_manager/` | ✅ | SQLite WAL, time-series, batch write, anomaly search |
| DatabaseManager | `database_manager/` | ✅ | Write coordinator, db_write subscriber |
| Retention | `database_manager/` | ✅ | Downsample + purge, 6h cycle |
| Sensor Poller | `sensor_poller/` | ✅ | Per-device async polling, jitter |
| Rule Engine | `rule_engine/` | ✅ | 8 rules (threshold, rate, stuck), 5-min cooldown |
| Notifier | `notifier/` | ✅ | Console, Telegram, Webhook, SMS |
| Adapters | `utils/adapters/` | ✅ | Mock, Serial, MQTT |
| TOML Profiles | `device_manager/device_profiles/` | ✅ | Templates + examples |
| Tailscale Split | — | ✅ | LLM trên PC, Agent trên Jetson |

### 12.3. Chưa triển khai

| Module | Mô tả | Ưu tiên |
|--------|-------|---------|
| Web UI | Dashboard HTML/JS kết nối MCP HTTP | 🟢 Thấp |
| ML Models | LightGBM, LSTM-TCN, Isolation Forest | 🟢 Thấp |
| ESP32 firmware | LoRa mesh node firmware | 🟡 Trung bình |
| OTA firmware | Cập nhật ESP32 qua LoRa | 🟢 Thấp |
| Scheduler | Lịch tưới tự động | 🟡 Trung bình |
| Safety Layer | Logic validator semantic AI check | 🟡 Trung bình |

---

## 13. Tổng Hợp Gap Analysis

### Resolved Gaps

| # | Gap | Giải pháp | Trạng thái |
|---|-----|----------|-----------|
| G01 | ActuatorLock | `asyncio.Lock` per device trong DeviceManager | ✅ Đã giải quyết |
| G02 | UART SerialQueue | SerialAdapter + per-device lock | ✅ Đã giải quyết |
| G03 | Alert acknowledgment | Alert qua EventBus, notifier đa kênh | ✅ Đã giải quyết |
| G04 | Background recorder | `sensor_poller/` + `database_manager/` event-driven | ✅ Đã giải quyết |
| G05 | ConversationSession | edge-agent Session giữ message history | ✅ Đã giải quyết |
| G10 | Rule engine | 8 rules (R01-R08 + R09 missing data) | ✅ Đã giải quyết |
| G11 | Deploy guide | `doc/system-design.md` + module docs | ✅ Đã giải quyết |

### Còn lại

| # | Gap | Mức độ | Ghi chú |
|---|-----|--------|---------|
| G06 | AES-256 | 🟠 Trung bình | Protocol thiết kế có flag Encrypted, chưa code |
| G07 | OTA firmware | 🟠 Trung bình | Chưa có firmware ESP32 |
| G08 | Web UI | 🟢 Thấp | MCP HTTP endpoint sẵn sàng, cần frontend |
| G09 | Scheduler | 🟡 Trung bình | Lịch tưới tự động chưa triển khai |
| G12-G18 | ML models | 🟢 Thấp | LightGBM, LSTM-TCN, ONNX chưa tích hợp |
| — | ESP32 firmware | 🟡 Trung bình | Chưa có firmware code trong repo |
| — | Tests | 🟡 Trung bình | Chưa có test suite chính thức |

---

*Tài liệu này được cập nhật theo từng phase triển khai.*