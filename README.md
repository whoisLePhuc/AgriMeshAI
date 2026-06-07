# AgriMeshAI

**Edge AI-Powered Smart Agriculture Platform** — LoRa mesh networking, on-device AI agents, MCP tool orchestration, and predictive analytics for autonomous farm monitoring and control.

---

## Overview

AgriMeshAI is an intelligent edge computing platform for smart agriculture. It connects farmers with distributed IoT devices through natural language, enabling autonomous monitoring, irrigation control, anomaly detection, and predictive decision-making — all operating offline at the edge.

The system integrates:

- **ESP32-based sensor and actuator nodes** deployed across the field
- **LoRa mesh communication network** with self-healing routing for long-range coverage
- **On-device AI Agent** powered by local LLM (Qwen2.5) for natural language interaction
- **MCP Gateway (Jeltz)** for unified tool orchestration between AI and hardware
- **24/7 Rule Engine & ML Inference** for real-time anomaly detection and prediction
- **Multi-channel user interface** (Web UI, Telegram Bot, SMS, BLE)
- **Local data storage** with SQLite and time-series analytics

---

## System Architecture

![System Architecture](doc/assets/system_architecture.png)

---

## Key Capabilities

### Natural Language Farm Control

Interact with your farm using everyday language:

- *"Turn on irrigation zone A for 10 minutes"*
- *"Show soil moisture trends from the last week"*
- *"Do I need irrigation tomorrow?"*
- *"What's the battery status of all sensors?"*

### Edge AI Agent

- Local LLM inference (Qwen2.5 via Ollama) — no internet required
- LangChain-based multi-step reasoning and tool calling
- Context-aware decision support with safety validation
- On-demand activation to minimize resource usage

### LoRa Mesh Networking

- Long-range communication (433/868/915 MHz) across the field
- Self-healing mesh routing with automatic node discovery
- Ultra-low-power sensor nodes with solar + LiPo battery
- Reliable actuator command delivery with acknowledgment

### Predictive Analytics & Anomaly Detection

- **Univariate anomaly detection** — deviation, rate-of-change, stuck sensor detection (±3σ moving average)
- **Multivariate anomaly detection** — Isolation Forest for cross-sensor correlation
- **Soil moisture prediction** (LightGBM) — proactive irrigation recommendations
- **Local weather forecasting** (LSTM-TCN) — microclimate predictions from NASA POWER data
- **Battery life prediction** — Linear regression for maintenance scheduling

### Real-Time Rule Engine

- 8 configurable threshold rules (temperature, humidity, battery, connectivity)
- Multi-tier alerts: INFO → WARNING → CRITICAL with push notifications
- Automated safety responses (emergency stop, actuator timeout)

### Safety & Reliability

- **3-layer safety architecture**: hardware watchdog → logic validator → semantic AI check
- Human-in-the-loop confirmation for all actuator commands
- ActuatorLock prevents concurrent conflicting commands
- Graceful offline fallback — all critical functions operate without internet

---

## Technology Stack

| Domain | Technologies |
|--------|-------------|
| **Embedded** | ESP32-S3, FreeRTOS, SX1262 LoRa transceiver, sensor drivers (DHT22, BH1750, capacitive soil) |
| **Edge Gateway** | Jetson Nano / Raspberry Pi, Python, SQLite (WAL mode), MQTT |
| **AI & ML** | Ollama, Qwen2.5, LangChain, MCP, LightGBM, Scikit-learn, ONNX Runtime |
| **Communication** | LoRa Mesh (LoRaMesher), UART (115200), SPI, REST API |
| **User Interface** | Web UI (port 8374), Telegram Bot, SMS, BLE |

---

## Project Structure

```
AgriMeshAI/
├── config/
│   └── models.yaml           
├── doc/
│   ├── assets/
│   │   └── system_architecture.png
│   └── system-design.md     
├── scripts/
│   └── setup.sh            
├── README.md
└── requirements.txt      
```

---

## Pre-prepare

### 1. Install Ollama & pull LLM model

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:1.5b
```
### 2. Run setup

```bash
./scripts/setup.sh
```

---

## License

This project is licensed under the terms included in the [LICENSE](LICENSE) file.
