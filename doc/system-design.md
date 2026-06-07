# THIẾT KẾ HỆ THỐNG
## AI Agent + ML + MCP Gateway + LoRa Mesh — Nông Nghiệp Thông Minh
**Phiên bản:** 2.1 (cập nhật hardware Jetson Nano) | **Ngày:** 03/06/2026 | **Quy mô:** POC / Vườn nhỏ — 1 người dùng, < 20 node

---

## Mục lục

1. [Tổng Quan Hệ Thống](#1-tổng-quan-hệ-thống)
2. [Phần Cứng](#2-phần-cứng)
3. [Software Stack](#3-software-stack)
4. [Luồng Dữ Liệu](#4-luồng-dữ-liệu)
5. [MCP Gateway — Jeltz](#5-mcp-gateway--jeltz)
6. [Database Schema](#6-database-schema-sqlite)
7. [An Toàn và Bảo Mật](#7-an-toàn-và-bảo-mật)
8. [Phát Hiện Bất Thường — Rule Engine](#8-phát-hiện-bất-thường--rule-engine)
9. [Machine Learning](#9-machine-learning)
10. [Kết Nối Người Dùng](#10-kết-nối-người-dùng)
11. [Giao Thức LoRa Mesh](#11-giao-thức-lora-mesh)
12. [Kế Hoạch Triển Khai](#12-kế-hoạch-triển-khai-poc)
13. [Tổng Hợp Gap Analysis](#13-tổng-hợp-gap-analysis)

---

## 1. Tổng Quan Hệ Thống

### 1.1. Mục đích

Hệ thống kết nối người nông dân với thiết bị cảm biến và điều khiển ngoài đồng ruộng thông qua ngôn ngữ tự nhiên. Người dùng chỉ cần nhắn tin hoặc nói chuyện với hệ thống; AI tự động đọc cảm biến, phân tích dữ liệu, và thực thi lệnh điều khiển.

### 1.2. Nguyên tắc thiết kế

- **Offline-first:** Toàn bộ chức năng hoạt động khi không có Internet.
- **Edge-centric:** Mọi quyết định được đưa ra tại gateway, không phụ thuộc cloud.
- **MCP là lớp giao tiếp duy nhất** giữa AI và phần cứng.
- **Safety tách biệt khỏi AI:** Guard rail không dùng LLM để quyết định an toàn.
- **AI on-demand:** LLM chỉ khởi động khi cần, không chạy nền liên tục.
- **Human-in-the-loop:** Lệnh điều khiển actuator luôn cần xác nhận người dùng.

### 1.3. Kiến trúc 3 lớp

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              NGƯỜI DÙNG                                     │
│  ┌───────┐  ┌───────┐  ┌──────────┐  ┌──────────┐  ┌───────┐  ┌─────────┐   │
│  │  Web  │  │  App  │  │ Telegram │  │ WA / Zalo│  │  SMS  │  │   BLE   │   │
│  └───────┘  └───────┘  └──────────┘  └──────────┘  └───────┘  └─────────┘   │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │ Chat / Voice / SMS
                                   ▼
┌────────────────────────────────────────────────────────────────────────────┐
│              EDGE GATEWAY (Jetson Nano 4GB — GPU 128-core Maxwell)         │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                        AI AGENT (on-demand)                          │  │
│  │                                                                      │  │
│  │  ┌─────────────────┐   ┌──────────────────┐   ┌──────────────────┐   │  │
│  │  │  User Interface │   │  Ollama          │   │  LangChain       │   │  │
│  │  │  (Chat / Voice) │◄──┤  Qwen2.5 1.5B    │◄──┤  + MCP Client    │   │  │
│  │  │                 │──►│  RAM ~1.5GB      │──►│  Tool calling    │──┐│  │
│  │  └─────────────────┘   └──────────────────┘   └──────────────────┘  ││  │
│  │                                                                     ││  │
│  │                                              ┌───────────────────┐  ││  │
│  │                                              │  Safety Validator │◄─┘│  │
│  │                                              │  Guard rails      │   │  │
│  │                                              │  + human-in-loop  │   │  │
│  │                                              └──────────┬────────┘   │  │
│  │                                                         │            │  │
│  │  ┌──────────────────────────────────────────────────────▼─────────┐  │  │
│  │  │                     MCP GATEWAY (Jeltz)                        │  │  │
│  │  │  • Tool definitions & routing                                  │  │  │
│  │  │  • Adapter: Serial / MQTT / Mock                               │  │  │
│  │  │  • MCP → LoRa Bridge (UART 115200)                             │  │  │
│  │  └──────────────────────────┬─────────────────────────────────────┘  │  │
│  └─────────────────────────────┼────────────────────────────────────────┘  │
│                                │                                           │
│  ┌─────────────────────────────▼─────────────────────────────────────────┐ │
│  │                   JELTZ DAEMON (24/7 — ~50MB RAM)                     │ │
│  │                                                                       │ │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌────────────────────────┐ │ │
│  │  │  LoRa Bridge    │─►│  Background     │─►│  Rule Engine           │ │ │
│  │  │  UART / SPI     │  │  Recorder       │  │  R01–R06: Threshold    │ │ │
│  │  │  Read sensor    │  │  (SQLite)       │  │  R04: Stuck sensor     │ │ │
│  │  │  Send actuator  │  │                 │  │  R05: Missing data     │ │ │
│  │  └─────────────────┘  └─────────────────┘  │  R07: Battery low      │ │ │
│  │                                            │  R08: Actuator timeout │ │ │
│  │                                            └───────────┬────────────┘ │ │
│  │                                                        │              │ │
│  │  ┌──────────────────────────────────────────────────────▼───────────┐ │ │
│  │  │                   ML INFERENCE ENGINE (24/7)                     │ │ │
│  │  │                                                                  │ │ │
│  │  │  ┌──────────────────────────┐  ┌──────────────────────────────┐  │ │ │
│  │  │  │  UNIVARIATE AD (realtime)│  │  PREDICTIVE (mỗi 5–15 phút)  │  │ │ │
│  │  │  │  • Moving Avg ±3σ        │  │  • Soil moisture (LightGBM)  │  │ │ │
│  │  │  │  • Rate of Change        │  │  • Battery life (Linear Reg) │  │ │ │
│  │  │  │  • Stuck sensor          │  │  • Temp predict              │  │ │ │
│  │  │  │  • Seasonal deviation    │  │                              │  │ │ │
│  │  │  │  RAM ~10MB               │  └──────────────────────────────┘  │ │ │
│  │  │  └──────────────┬───────────┘                                    │ │ │
│  │  │                 │            ┌────────────────────────────────┐  │ │ │
│  │  │  ┌──────────────▼──────────┐ │  WEATHER LSTM (mỗi 15 phút)    │  │ │ │
│  │  │  │  MULTIVARIATE AD        │ │  • Dự báo 48h                  │  │ │ │
│  │  │  │  (khi có đủ data)       │ │  • ONNX ~8MB                   │  │ │ │
│  │  │  │  • Isolation Forest     │ │  • NASA POWER data             │  │ │ │
│  │  │  │  • Cross-correlation    │ │  RAM ~80MB                     │  │ │ │
│  │  │  │  RAM ~30MB              │ └────────────────────────────────┘  │ │ │
│  │  │  └─────────────────────────┘                                     │ │ │
│  │  └──────────────────────────────────────────────────────────────────┘ │ │
│  │                                                                       │ │
│  │  ┌────────────────────────────────────────────────────────────────┐   │ │
│  │  │                  NOTIFIER (Telegram / SMS)                     │   │ │
│  │  │  • Rule Engine trigger  → push alert ngay                      │   │ │
│  │  │  • ML anomaly > ngưỡng  → push + gọi AI Agent                  │   │ │
│  │  │  • Scheduled report (sáng / tối)                               │   │ │
│  │  └────────────────────────────────────────────────────────────────┘   │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│                                                                            │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                     SQLite DATABASE (~20MB)                          │  │
│  │  ┌─────────────┐ ┌──────────┐ ┌─────────────────┐ ┌───────────────┐  │  │
│  │  │ sensor_data │ │ alerts   │ │ device_registry │ │ weather_      │  │  │
│  │  │ (time-series│ │ (ML out) │ │                 │ │ forecasts     │  │  │
│  │  └─────────────┘ └──────────┘ └─────────────────┘ └───────────────┘  │  │
│  │  ┌─────────────┐ ┌──────────┐ ┌─────────────────┐                    │  │
│  │  │ actuation_  │ │ ml_      │ │model_metrics    │                    │  │
│  │  │ log         │ │ models   │ │                 │                    │  │
│  │  └─────────────┘ └──────────┘ └─────────────────┘                    │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────┬─────────────────────────────────────────┘
                                   │ SPI / UART 115200
                                   │ (Jeltz gửi lệnh qua UART đến module radio)
                                   ▼
                            ┌──────────────────┐
                            │   Module LoRa    │
                            │   SX1262 HAT     │
                            │  (radio trans-   │
                            │   ceiver)        │
                            └────────┬─────────┘
                                     │ LoRa 433/868/915 MHz
                                     │
┌─────────────────────────────────────────────────────────────────────────────┐
│                     LỚP MESH — ĐỒNG RUỘNG                                   │
│                     ESP32-S3 + SX1262 — Mesh tự phục hồi                    │
│            ┌───────────────┼────────────────┐                               │
│            ▼               ▼                ▼                               │
│  ┌─────────────────┐ ┌─────────────┐ ┌──────────────────┐                   │
│  │  Sensor Node    │ │ Relay Node  │ │  Actuator Node   │                   │
│  │  ESP32-S3       │ │ ESP32-S3    │ │  ESP32 + Relay   │                   │
│  │  + SX1262       │ │ + SX1262    │ │  + SX1262        │                   │
│  │─────────────────│ │─────────────│ │──────────────────│                   │
│  │  DHT22          │ │ Chuyển tiếp │ │  Máy bơm / Van   │                   │
│  │  Soil sensor    │ │ tín hiệu    │ │  Relay module    │                   │
│  │  BH1750         │ │─────────────│ │──────────────────│                   │
│  │─────────────────│ │ Năng lượng  │ │  Nguồn lưới      │                   │
│  │  Solar + LiPo   │ │ mặt trời    │ │  / Solar         │                   │
│  └─────────────────┘ └─────────────┘ └──────────────────┘                   │
└─────────────────────────────────────────────────────────────────────────────┘
```

| Lớp | Thành phần | Vai trò | Chạy 24/7? |
|-----|-----------|---------|-----------|
| Giao tiếp | Web, Telegram Bot, BLE, SMS | Nhận lệnh người dùng, trả kết quả | ✅ |
| Edge — LLM Agent | Qwen2.5 + LangChain + Safety + MCP | Xử lý ngôn ngữ, suy luận, multi-step | ❌ On-demand |
| Edge — ML Inference | Univariate AD + Multivariate AD + Weather LSTM | Phát hiện bất thường, dự đoán | ✅ 24/7 |
| Edge — Daemon | Recorder + Rule Engine + ML Triggers | Thu thập data, threshold, gọi ML | ✅ 24/7 |
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

| Thành phần | Công nghệ | Vai trò | RAM | Chạy |
|-----------|-----------|---------|-----|------|
| LLM runtime | Ollama (Qwen2.5 1.5B/3B) | Inference local — on-demand | ~1.5 GB | On-demand |
| AI Agent framework | LangChain + MCP Client (Python) | Tool calling, conversation management | ~100 MB | On-demand |
| MCP Gateway | Jeltz (custom) | Tool definitions, device routing, LoRa bridge | ~50 MB | ✅ 24/7 |
| Daemon / Rule Engine | Python process (systemd service) | 24/7 data collection, threshold alerting | ~50 MB | ✅ 24/7 |
| **ML — Univariate AD** | **Moving Avg ±3σ + Rate of Change + Variance** | **Deviation, stuck sensor, seasonal** | **~10 MB** | **✅ 24/7** |
| **ML — Multivariate AD** | **Isolation Forest + Cross-correlation** | **Tổ hợp sensor bất thường (M01-M04)** | **~30 MB** | **✅ 24/7** |
| **ML — Predictive** | **LightGBM / Linear Regression** | **Dự đoán độ ẩm, nhiệt, pin** | **~50 MB** | **✅ Mỗi 5-15 phút** |
| **ML — Weather LSTM** | **LSTM-TCN ONNX (từ NASA POWER)** | **Dự báo thời tiết local** | **~80 MB** | **✅ Mỗi 15 phút** |
| Database | SQLite 3 + WAL mode | Time-series sensor data, alert log, device registry | ~20 MB | ✅ 24/7 |
| Notification | Telegram Bot API / SMTP | Push alerts khi có bất thường | ~10 MB | ✅ 24/7 |
| Web UI | Lightweight HTTP server (port 8374) | Chat interface + REST API | ~30 MB | ✅ 24/7 |
| OS | Ubuntu 22.04 LTS (aarch64) | NVIDIA JetPack SDK, systemd | ~2 GB | ✅ 24/7 |

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

### 4.1. Thu thập dữ liệu cảm biến

```
Sensor Node                   Gateway Daemon                  AI Agent / User
    │                               │                               │
    ├──[LoRa]── data packet ───────►│                               │
    │                               ├─ parse + store SQLite         │
    │                               ├─ Rule Engine check            │
    │                               │   ├─ OK → tiếp tục            │
    │                               │   └─ Vi phạm rule             │
    │                               │       ├─ Ghi alerts table     │
    │                               │       ├─ Push notification    │
    │                               │       └─ Wake AI (nếu cần)    │
    │                               │                               │
    │                               │◄── MCP: get_sensor_history ───┤
    │                               ├─── query SQLite ─────────────►│
    │                               │◄── MCP: read_sensor ──────────┤
    │◄──[LoRa]── request ───────────┤                               │
    ├──[LoRa]── response ──────────►│                               │
    │                               ├─── tool result ──────────────►│
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
Jeltz Daemon (mỗi 15 phút)
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

## 5. MCP Gateway — Jeltz

### 5.1. Danh sách MCP Tools đầy đủ

#### Nhóm Read — Đọc dữ liệu

| MCP Tool | Tham số | Mô tả | Trạng thái |
|----------|---------|-------|-----------|
| `read_sensor` | `node_id, sensor_id` | Đọc 1 cảm biến realtime | ✅ Đã thiết kế |
| `read_multiple_sensors` | `node_ids[], sensor_ids[]` | Đọc nhiều cảm biến song song | ✅ Đã thiết kế |
| `get_sensor_history` | `node_id, sensor_id, hours, aggregation` | Lịch sử từ SQLite | ✅ Đã thiết kế |
| `get_all_readings` | — | Đọc tất cả cảm biến hiện tại | ✅ Đã thiết kế |
| `get_soil_conditions` | `lat, lon, depth` | Tình trạng đất tổng hợp | ✅ Đã thiết kế |
| `get_node_status` | `node_id` | Pin, RSSI, uptime, lỗi firmware | ⚠️ Cần bổ sung |
| `get_system_summary` | — | Tóm tắt toàn bộ hệ thống cho AI report | ⚠️ Cần bổ sung |

#### Nhóm Control — Điều khiển

| MCP Tool | Tham số | Mô tả | Trạng thái |
|----------|---------|-------|-----------|
| `execute_actuator` | `node_id, actuator_id, command, params` | Gửi lệnh điều khiển (có safety check) | ✅ Đã thiết kế |
| `emergency_stop_all` | `reason` | Dừng khẩn cấp toàn bộ — broadcast | ✅ Đã thiết kế |
| `set_threshold` | `node_id, sensor_id, min, max` | Đặt ngưỡng cảnh báo | ✅ Đã thiết kế |
| `schedule_actuator` | `node_id, actuator_id, cron_expr, duration` | Hẹn giờ bật/tắt định kỳ | ⚠️ Cần bổ sung |
| `cancel_schedule` | `schedule_id` | Hủy lịch đã đặt | ⚠️ Cần bổ sung |
| `list_schedules` | `node_id?` | Xem danh sách lịch tưới hiện tại | ⚠️ Cần bổ sung |

#### Nhóm Network — Quản lý mạng

| MCP Tool | Tham số | Mô tả | Trạng thái |
|----------|---------|-------|-----------|
| `get_device_topology` | — | Bản đồ mạng mesh (nodes, links, stats) | ✅ Đã thiết kế |
| `ping_node` | `node_id` | Kiểm tra node còn sống, đo RTT + RSSI | ✅ Đã thiết kế |
| `list_devices` | — | Danh sách thiết bị từ registry | ✅ Đã thiết kế |
| `register_device` | `node_id, type, sensors[], location` | Đăng ký node mới vào registry | ⚠️ Cần bổ sung |
| `decommission_device` | `node_id` | Xóa node khỏi hệ thống | ⚠️ Cần bổ sung |

#### Nhóm Analysis — Phân tích AI

| MCP Tool | Tham số | Mô tả | Trạng thái |
|----------|---------|-------|-----------|
| `check_irrigation_plan` | `fields[], hours` | Phân tích nhu cầu tưới | ✅ Đã thiết kế |
| `search_anomalies` | `hours, device_id?` | Phát hiện bất thường từ alert log | ✅ Đã thiết kế |
| `get_weather_forecast` | `lat, lon, days` | Dự báo thời tiết (Internet, offline fallback) | ✅ Đã thiết kế |
| `get_weather_forecast_local` | `hours` | Dự báo LSTM-TCN local (NASA POWER) | ✅ Thêm mới |
| `predict_soil_moisture` | `node_id, hours_ahead` | Dự đoán độ ẩm đất (LightGBM) | ✅ Thêm mới |
| `predict_battery_life` | `node_id` | Dự đoán pin còn lại (Linear Regression) | ✅ Thêm mới |
| `generate_daily_report` | `date?` | Báo cáo ngày: tưới, cảm biến, alert | ⚠️ Cần bổ sung |
| `compare_trend` | `node_id, sensor_id, days` | So sánh xu hướng tuần này vs tuần trước | ⚠️ Cần bổ sung |

### 5.2. LoRa Bridge Adapter

LoRa Bridge là MCP adapter triển khai interface `BaseAdapter`, giao tiếp với LoRa module qua UART 115200 baud theo binary protocol (xem mục 10.2).

> ⚠️ **GAP G02 — SerialQueue:** Cần thêm FIFO queue cho UART, per-command timeout 3s, retry 3 lần trước khi trả lỗi cho MCP layer.

---

## 6. Database Schema (SQLite)

Tất cả dữ liệu lưu trong một file duy nhất tại `/var/jeltz/data.db`. **WAL mode bật mặc định** để tránh lock contention giữa daemon (writer) và AI agent (reader).

### 6.1. Bảng `readings` — Dữ liệu cảm biến

```sql
CREATE TABLE readings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id     INTEGER NOT NULL,
    sensor_id   TEXT    NOT NULL,
    value       REAL    NOT NULL,
    unit        TEXT    NOT NULL,
    timestamp   INTEGER NOT NULL,  -- Unix timestamp (giây)
    quality     INTEGER DEFAULT 100  -- Chất lượng tín hiệu 0–100 (từ RSSI)
);

CREATE INDEX idx_readings_lookup
    ON readings (node_id, sensor_id, timestamp DESC);
```

- **Retention policy:** 30 ngày full resolution; downsampled 1h/lần giữ 1 năm.
- Cleanup job chạy khi gateway start và mỗi đêm lúc 2h.

### 6.2. Bảng `alerts` — Cảnh báo

```sql
CREATE TABLE alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id     INTEGER NOT NULL,
    sensor_id   TEXT,               -- NULL nếu là alert hệ thống (node offline)
    rule_id     TEXT    NOT NULL,   -- Tên rule vi phạm (R01, R02, ...)
    value       REAL,
    severity    TEXT    NOT NULL,   -- INFO / WARNING / CRITICAL
    message     TEXT    NOT NULL,
    timestamp   INTEGER NOT NULL,
    ack_at      INTEGER,            -- NULL nếu chưa xác nhận
    ack_by      TEXT                -- 'user' / 'system' / user_id
);
```

> ⚠️ **GAP G03:** v1.0 thiếu `ack_at` / `ack_by`. Không có acknowledgment, cùng một alert bị push notification nhiều lần và AI sẽ báo lại lỗi cũ đã xử lý.

### 6.3. Bảng `devices` — Device Registry

```sql
CREATE TABLE devices (
    node_id      INTEGER PRIMARY KEY,
    type         TEXT    NOT NULL,  -- sensor / relay / actuator
    name         TEXT    NOT NULL,  -- Tên thân thiện (ví dụ: pump_zone_A)
    location     TEXT,              -- Mô tả vị trí
    sensors      TEXT,              -- JSON array tên sensor hỗ trợ
    config       TEXT,              -- JSON config (thresholds, intervals)
    firmware_ver TEXT,
    status       TEXT DEFAULT 'unknown',  -- online / offline / warning / unknown
    last_seen    INTEGER,           -- Unix timestamp lần cuối nhận gói tin
    battery_pct  INTEGER            -- % pin; NULL nếu dùng lưới điện
);
```

### 6.4. Bảng `schedules` — Lịch tưới tự động

```sql
CREATE TABLE schedules (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id      INTEGER NOT NULL,
    actuator_id  TEXT    NOT NULL,
    cron_expr    TEXT    NOT NULL,  -- Ví dụ: "0 6 * * *" = 6h sáng mỗi ngày
    duration_sec INTEGER NOT NULL,
    enabled      INTEGER DEFAULT 1,  -- 1 = bật, 0 = tắt
    created_by   TEXT,               -- 'user' / 'ai'
    last_run     INTEGER             -- Unix timestamp lần chạy gần nhất
);
```

> ⚠️ **GAP G04:** Đây là GAP lớn trong v1.0 — chức năng hẹn giờ được đề cập nhưng không có schema lưu trữ hay logic xử lý. Cần thêm `SchedulerService` trong daemon, chạy check mỗi phút, thực thi lệnh đến hạn qua `execute_actuator` với safety check đầy đủ.

### 6.5. Bảng `actuation_log` — Lịch sử điều khiển

```sql
CREATE TABLE actuation_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id      INTEGER NOT NULL,
    actuator_id  TEXT    NOT NULL,
    command      TEXT    NOT NULL,  -- ON / OFF / SET_VALUE
    params       TEXT,              -- JSON params bổ sung
    duration_sec INTEGER,
    triggered_by TEXT    NOT NULL,  -- user / schedule / ai_agent / rule_engine
    confirmed_by TEXT,              -- User ID xác nhận; NULL nếu auto
    status       TEXT    NOT NULL,  -- success / failed / timeout
    timestamp    INTEGER NOT NULL
);
```

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

### 8.1. Kiến trúc 3 tầng

```
┌──────────────────────────────────────────────────────────────┐
│                    24/7 (luôn chạy)                          │
│  ┌──────────────────┐  ┌──────────────────┐  ┌────────────┐  │
│  │ LoRa Receiver    │  │ SQLite Recorder  │  │Rule Engine │  │
│  │ (đọc gói tin)    │  │ (ghi dữ liệu)    │  │(so ngưỡng) │  │
│  └──────────────────┘  └──────────────────┘  └────────────┘  │
│  RAM: ~50MB, CPU: ~5%                                        │
├──────────────────────────────────────────────────────────────┤
│               THEO YÊU CẦU (on-demand)                       │
│  ┌──────────────────────┐  ┌──────────────────────────────┐  │
│  │ Push Notification    │  │ AI Agent (LLM)               │  │
│  │ + Scheduled report   │  │ RAM: ~1.5GB, CPU: 30–50%     │  │
│  └──────────────────────┘  │ Chạy 5–30s rồi tắt           │  │
│                            └──────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

### 8.2. Danh sách rules đầy đủ

| Rule ID | Loại | Điều kiện | Hành động | Trạng thái |
|---------|------|-----------|-----------|-----------|
| R01 | Threshold đơn | `value > max` hoặc `value < min` | Alert CRITICAL + push notification | ✅ |
| R02 | Threshold kép | `value > warning_level` trong T phút | Alert WARNING + gọi AI | ✅ |
| R03 | Rate of change | `\|Δvalue/Δt\| > threshold` | Alert WARNING + gọi AI | ✅ |
| R04 | Stuck sensor | Giá trị không đổi > 6h | Alert WARNING: cảm biến hỏng? | ✅ |
| R05 | Missing data | Không có reading > threshold_time | Alert CRITICAL: node mất kết nối | ✅ |
| R06 | Baseline deviation | Lệch > X% so với trung bình 7 ngày | Alert INFO + gọi AI | ✅ |
| R07 | Battery low | `battery_pct < 20%` | Alert WARNING: sắp hết pin | ⚠️ Thêm mới |
| R08 | Actuator overtime | Actuator vẫn ON sau `duration + 5 phút` | Emergency stop + Alert CRITICAL | ⚠️ Thêm mới |
| R09 | Correlation anomaly | 2 sensor liên quan tăng/giảm cùng lúc | Gọi AI phân tích root cause | ⚠️ Thêm mới |

> ⚠️ **GAP G10:** Rules R07, R08, R09 chưa có trong v1.0. R08 đặc biệt quan trọng — actuator chạy quá giờ mà không tự tắt là rủi ro thực tế.

---

## 9. Machine Learning

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
              │ Deploy xuống        │
              │ gateway (Jetson Nano)    │
              │ ONNX Runtime       │
              └─────────┬──────────┘
                        │
          ┌─────────────┴─────────────┐
          │                           │
          ▼                           ▼
  ┌─────────────────┐    ┌──────────────────────┐
  │ Mới deploy      │    │ Có sensor data > 3   │
  │ Chỉ dùng NASA   │    │ tháng                 │
  │ Dự báo ±3°C     │    │ Fine-tune với data    │
  │                 │    │ thực tế tại ruộng      │
  │ get_weather_     │    │ Dự báo ±1°C           │
  │ forecast_local()│    │                       │
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
│             │ (Jeltz) │                                     │
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

### 9.1. Các kênh giao tiếp

| Kênh | Phạm vi | Cần Internet? | Multi-user? | Push notification? | Khi nào dùng |
|------|---------|--------------|-------------|-------------------|-------------|
| WiFi AP (gateway tạo hotspot) | ~50m | Không | Không | Không | POC, thử nghiệm tại chỗ |
| Local WiFi (router nhà/trại) | ~100m | Không | Có | Không | Gia đình, trại nhỏ có WiFi |
| **4G USB + Telegram Bot** | **Bất kỳ** | **Có** | **Có** | **Có ✅** | **Triển khai thực tế — khuyến nghị** |
| Bluetooth BLE + App | ~10m | Không | Không | Không | Kiểm tra nhanh tại gateway |
| SMS (SIM800) | Sóng GSM | Không | Không | Có (giới hạn) | Vùng sâu không có 4G |

M��i kênh đều đi qua cùng một MCP Server — không có logic riêng biệt cho từng kênh.

### 9.2. Conversation design — Intent mapping

| Intent | Ví dụ câu | MCP tools gọi | Cần xác nhận? |
|--------|-----------|--------------|--------------|
| Đọc cảm biến | "Độ ẩm khu A?" | `read_sensor` / `get_all_readings` | Không |
| Xem lịch sử | "Tuần này mưa nhiều không?" | `get_sensor_history` | Không |
| Hỏi thời tiết | "Có cần tưới hôm nay không?" | `get_weather_forecast` + `get_sensor_history` | Không |
| Điều khiển ngay | "Tưới khu A 20 phút" | safety_check → `execute_actuator` | **Có ✅** |
| Đặt lịch | "Mỗi sáng 6h tưới khu B 15 phút" | `schedule_actuator` | **Có ✅** |
| Xem lịch | "Lịch tưới hiện tại?" | `list_schedules` | Không |
| Hỏi bất thường | "Có gì lạ không?" | `search_anomalies` + `get_sensor_history` | Không |
| Báo cáo | "Hôm nay tưới bao nhiêu?" | `generate_daily_report` | Không |
| Kiểm tra node | "Node khu C còn sống không?" | `ping_node` + `get_node_status` | Không |
| Dừng khẩn cấp | "Dừng tất cả!" | `emergency_stop_all` | **Có ✅** |

### 9.3. Conversation State Management

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

## 10. Giao Thức LoRa Mesh

### 10.1. Cấu trúc gói tin mesh

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

### 10.2. UART Binary Protocol (Gateway ↔ LoRa Module)

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

### 10.3. Payload formats

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

### 10.4. Mesh Routing

- **Giao thức:** Distance-vector routing (LoRaMesher)
- **TDMA:** Node được cấp slot thời gian để gửi, tránh collision
- **Auto-join:** Node mới tự động gia nhập mạng
- **Self-healing:** Khi node chết, route tự tìm đường khác
- **Phạm vi:** 2–3 km với multi-hop (tùy địa hình)

---

## 11. Kế Hoạch Triển Khai (POC)

### 11.1. Yêu cầu môi trường

| Yêu cầu | Giá trị | Ghi chú |
|---------|---------|---------|
| OS | Ubuntu 22.04 (JetPack 6.0+) | NVIDIA SDK hỗ trợ CUDA, TensorRT |
| Python | 3.11+ | |
| Ollama | Latest stable | Tải model Qwen2.5 1.5B (~1GB) |
| SQLite | 3.40+ | Tích hợp sẵn trong Python stdlib |
| Disk space | ≥ 8GB free | LLM model + data retention |
| RAM | ≥ 4GB | 2GB cho LLM khi chạy + 50MB daemon |

### 11.2. Thứ tự phát triển

| Phase | Thành phần | Mô tả | Blockers |
|-------|-----------|-------|---------|
| **Phase 1** | Daemon + Rule Engine | Thu thập dữ liệu 24/7, alert cơ bản | Cần firmware ESP32 |
| **Phase 1** | SQLite schema đầy đủ | Tất cả 5 bảng trong mục 6 | — |
| **Phase 1** | UART Bridge + SerialQueue | Giao tiếp gateway ↔ LoRa module | Cần phần cứng SX1262 |
| **Phase 2** | MCP Tools nhóm Read | `read_sensor`, `get_sensor_history`, ... | Phase 1 hoàn thành |
| **Phase 2** | AI Agent cơ bản | Chat, đọc cảm biến, phân tích | Ollama + LangChain |
| **Phase 2** | ConversationSession | Context management cho confirmation | AI Agent |
| **Phase 2** | ML — Univariate AD | Moving Avg ±3σ, stuck sensor, rate of change | Có data > 1 tuần |
| **Phase 3** | MCP Tools nhóm Control | `execute_actuator` + safety validator + ActuatorLock | Phase 2 xong |
| **Phase 3** | SchedulerService | Lịch tưới tự động | MCP Control |
| **Phase 3** | Telegram Bot | Remote access + push notification | 4G dongle |
| **Phase 3** | ML — Predictive (LightGBM) | Dự đoán độ ẩm, nhiệt, pin | Có data > 30 ngày |
| **Phase 3** | ML — Multivariate AD | Isolation Forest, cross-correlation | Có data > 2 tháng |
| **Phase 4** | ML — Weather LSTM | LSTM-TCN từ NASA POWER, ONNX deploy | Có data sensor > 30 ngày |
| **Phase 4** | OTA firmware | Cập nhật firmware qua LoRa | Phase 1–3 ổn định |

---

## 12. Tổng Hợp Gap Analysis

Tất cả gaps phát hiện từ v1.0, phân loại theo mức độ ưu tiên:

| # | Gap | Mức độ | Giải pháp |
|---|-----|--------|----------|
| G01 | Thiếu ActuatorLock — conflict khi 2 lệnh đến cùng lúc | 🔴 Cao | Mutex per `actuator_id` trong MCP layer, timeout tự giải phóng 5 phút |
| G02 | UART không có queue — lệnh xung đột khi poll nhiều node | 🔴 Cao | `SerialQueue` FIFO + per-command timeout 3s + retry 3 lần |
| G03 | SQLite thiếu `alerts.ack_at` — alert bị push notification nhiều lần | 🔴 Cao | Thêm cột `ack_at`, `ack_by` vào bảng `alerts` |
| G04 | Thiếu bảng `schedules` và `SchedulerService` | 🔴 Cao | Schema mục 6.4 + service chạy check mỗi phút trong daemon |
| G05 | Thiếu `ConversationSession` — AI mất context khi chờ xác nhận | 🟠 Trung bình | Session store per `chat_id`, timeout 5 phút, `pending_confirmations` queue |
| G06 | AES-256 tùy chọn cho actuator commands — rủi ro replay attack | 🟠 Trung bình | Bắt buộc encrypt actuator commands (Flags Bit 1 = 1 bắt buộc) |
| G07 | OTA firmware chưa có thiết kế chi tiết | 🟠 Trung bình | Quy trình 6 bước mục 7.3, triển khai Phase 4 |
| G08 | Thiếu MCP tools: `get_node_status`, `get_system_summary`, `generate_daily_report` | 🟡 Thấp | Implement trong Phase 2–3 |
| G09 | Thiếu MCP tools: `schedule_actuator`, `compare_trend`, `register_device`, `decommission_device` | 🟡 Thấp | Implement trong Phase 3 |
| G10 | Thiếu rules R07 (battery low), R08 (actuator overtime), R09 (correlation) | 🟡 Thấp | Bổ sung vào Rule Engine config; R08 ưu tiên cao hơn |
| G11 | Chưa có deploy guide — dependency management, systemd services | 🟡 Thấp | Thêm `pyproject.toml`, systemd unit files, setup script |

---

### ML-specific Gaps

| # | Gap | Mức độ | Giải pháp | Liên quan |
|---|-----|--------|-----------|-----------|
| G11 | Univariate AD chưa có training baseline | Cao | Thu thập 7 ngày data đầu → tự động tính mean/std | Phase 2 |
| G12 | Isolation Forest chưa có data để train | Trung bình | Cần ~2 tháng data đa dạng; có thể dùng synthetic data hoặc NASA POWER | Phase 3 |
| G13 | Weather LSTM chưa có pipeline download NASA POWER | Cao | Xây script download + preprocess từ `pynasapower` | Phase 4 |
| G14 | LightGBM soil moisture chưa có huấn luyện | Trung bình | Train trên laptop với NASA POWER + fine-tune khi có sensor data | Phase 3 |
| G15 | ONNX export pipeline chưa thiết lập | Trung bình | Tham khảo `torch.onnx.export` + ONNX Runtime trên RPi | Phase 4 |
| G16 | Battery prediction cần 30 ngày data pin | Cao | Bắt đầu ghi pin ngay từ Phase 1 | Phase 1 |
| G17 | Chưa có cơ chế retrain model tự động | Trung bình | Script cron hàng tuần: collect data mới → retrain → replace model | Phase 4 |
| G18 | Chưa có evaluation metrics cho ML models | Thấp | Log RMSE, MAE, Precision, Recall mỗi lần inference | Phase 2 |

---

*Tài liệu này sẽ được cập nhật theo từng phase triển khai.*