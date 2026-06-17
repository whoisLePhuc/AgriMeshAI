# System Architecture — AgriMeshAI LoRa Mesh

> **Phiên bản:** 1.0 | **Ngày:** 12/06/2026
> **Nhóm:** Architecture & Design — 🔴 Bắt buộc

---

## 1. Tổng quan

Hệ thống gồm 4 thành phần chính:

```
┌─────────────────────────────────────────────────────────────┐
│                    EDGE GATEWAY (Jetson Nano)               │
│  Python: MCP Server, SystemManager, RuleEngine, Notifier    │
│  SerialATAdapter — giao tiếp UART với LoRa Gateway          │
│                          │ UART 115200                      │
│                          │ AT commands text-based           │
└──────────────────────────┼──────────────────────────────────┘
                           │
┌──────────────────────────┼──────────────────────────────────┐
│                LoRa GATEWAY (ESP32-S3 + SX1262)             │
│  Firmware: lora_gateway                                     │
│  ┌──────────┐   ┌──────────────┐   ┌────────────────────┐   │
│  │UART      │   │AT Command    │   │LoRa Mesh Bridge    │   │
│  │Handler   │──►│Processor     │──►│(LoRaMesher)        │   │
│  │(ISR)     │   │(parse/route) │   │RadioLib → SX1262   │   │
│  └──────────┘   └──────────────┘   └────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                           │ LoRa (868/915 MHz)
                           │ TDMA mesh
             ┌─────────────┼─────────────┐
             │             │             │
   ┌─────────▼──┐   ┌──────▼──────┐  ┌───▼──────────┐
   │ SENSOR NODE│   │ SENSOR NODE │  │ ACTUATOR NODE│
   │ESP32-S3    │   │ ESP32-S3    │  │ ESP32-S3     │
   │+ DHT22     │   │  + DHT22    │  │ + Relay x4   │
   │Push temp   │   │ Push temp   │  │ auto-off     │
   │mỗi 60s     │   │ mỗi 60s     │  │ timer 30ph   │
   └────────────┘   └─────────────┘  └──────────────┘
```

## 2. Component Responsibilities

### 2.1 Edge Gateway (Python)

| Component | Vai trò |
|-----------|---------|
| **SerialATAdapter** | Mới — gửi AT commands, parse response, timeout/retry |
| **DeviceManager** | Quản lý node mapping table (node_id ↔ lora_addr), online/offline |
| **FleetTools** | Thêm tool `set_relay(node_id, duration)`, `get_node_list()` |
| **RuleEngine** | Không đổi — nhận sensor data từ EventBus |
| **Notifier** | Không đổi — console, telegram, webhook |

### 2.2 LoRa Gateway (ESP32)

| Module | Vai trò |
|--------|---------|
| **UART Handler** | ISR-based receive, 256B ring buffer, double-buffered send |
| **AT Command Processor** | Parse AT commands từ Edge, dispatch |
| **LoRa Mesh Bridge** | Message queue: UART → LoRa, LoRa → UART |
| **Node Discovery** | Detect node mới join mesh, báo `+NODE_JOIN` |
| **LoRaMesher** | `NodeCapabilities::GATEWAY` flag |

### 2.3 Sensor Node (ESP32)

| Module | Vai trò |
|--------|---------|
| **Sensor Reader** | Đọc DHT22 mỗi 60s |
| **LoRa Sender** | Gửi TEMP_READING đến Gateway |
| **Command Handler** | Nhận PING → PONG |

### 2.4 Actuator Node (ESP32)

| Module | Vai trò |
|--------|---------|
| **Relay Controller** | 4 relay, auto-off timer 30 phút |
| **Command Handler** | Parse RELAY_CMD → execute → RELAY_ACK |

## 3. Addressing Scheme

| Entity | Address Type | Value |
|--------|-------------|-------|
| LoRa Gateway | LoRaMesher `AddressType` | 0x0001 (cố định) |
| Sensor Node | LoRaMesher `AddressType` | Auto-generate từ MAC |
| Actuator Node | LoRaMesher `AddressType` | Auto-generate từ MAC |

`node_id` (user-facing) do Edge tự gán từ 1→N khi node join. `lora_addr` là uint16_t unique.

## 4. Safety & Reliability

- Actuator auto-off: max 30 phút hard timeout
- UART timeout: Edge retry 3 lần, 2s timeout
- Node offline detection: >5 phút không push → OFFLINE
- Node mapping: lưu trong SQLite (node_id, lora_addr, type, status, last_seen)

## 5. Deployment View

```
┌─────────────────────────────────────────────────────────────────┐
│  Edge Gateway (Jetson Nano)                                     │
│   ┌──────────────┐                                              │
│   │ /dev/ttyUSB0 │◄──── USB cable ────► LoRa Gateway (ESP32)    │
│   └──────────────┘                                              │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  LoRa Gateway (ESP32-S3) [trong nhà / gần Jetson]               │
│   SX1262 LoRa module (SPI: CS=8, RST=12, IRQ=14, IO1=13)        │
│   UART (TX=1, RX=2, baud=115200) → USB → Jetson                 │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  Sensor Node (ESP32-S3) [ngoài đồng]                            │
│   SX1262 LoRa module                                            │
│   DHT22 (GPIO 6)                                                │
│   Pin solar + LiPo                                              │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  Actuator Node (ESP32-S3) [gần bơm/van]                         │
│   SX1262 LoRa module                                            │
│   Relay module 4 kênh (GPIO 14-17, optocoupled)                 │
│   Nguồn lưới 12V                                                │
└─────────────────────────────────────────────────────────────────┘
```

## 6. Network Topology

```
                    ┌──────────────────┐
                    │  LoRa Gateway    │
                    │  (0x0001)        │
                    │  GATEWAY role    │
                    └────────┬─────────┘
                             │
          ┌──────────────────┼──────────────────┐
          │                  │                  │
    ┌─────▼─────┐      ┌────▼─────┐      ┌────▼─────┐
    │Sensor #1  │      │Sensor #2 │      │Actuator  │
    │NODE role  │      │ NODE role│      │NODE role │
    └───────────┘      └──────────┘      └──────────┘
```

- Tối đa 20 node trong mesh
- Gateway là Network Manager duy nhất (`NodeRole::NETWORK_MANAGER`)
- Sensor/Actuator là `NodeRole::NODE_ONLY`

## 7. Technology Stack

| Layer | Technology |
|-------|-----------|
| Edge Gateway | Python 3.10+, pyserial-asyncio |
| LoRa Gateway | ESP32-S3, Arduino framework |
| Sensor Node | ESP32-S3, Arduino framework |
| Actuator Node | ESP32-S3, Arduino framework |
| LoRa Radio | SX1262 @ 868MHz / 915MHz |
| UART | 115200 baud, 8N1, AT commands |
| Mesh Protocol | LoRaMesher Distance-Vector |
