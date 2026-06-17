# Node Provisioning Guide

> **Phiên bản:** 2.0 | **Ngày:** 17/06/2026
> **Nhóm:** Operations — 🟢 Cần khi deploy

---

## 1. Thêm node mới

### Hardware checklist

- [ ] ESP32-S3 đã hàn chân header
- [ ] SX1262 kết nối đúng SPI pins (CS=8, RST=12, IRQ=14, IO1=13, SCK=9, MISO=11, MOSI=10)
- [ ] DHT22 (sensor) hoặc Relay module 4 kênh (actuator) đã kết nối
- [ ] LED onboard GPIO 35

### Flash firmware

```bash
cd firmware/

# Build và upload sensor node
pio run -e sensor_node -t upload --upload-port /dev/ttyUSB1

# Build và upload actuator node
pio run -e actuator_node -t upload --upload-port /dev/ttyUSB1

# Build và upload LoRa Gateway
pio run -e lora_gateway -t upload --upload-port /dev/ttyUSB0
```

### Verify node join mesh

```bash
# Monitor LoRa Gateway
pio run -e lora_gateway -t monitor
# Expected output:
# === AgriMeshAI LoRa Gateway ===
# [LoRa] OK  addr=0x0001
# === Ready ===
# +NODE_JOIN:0xA1B2,0,1.0   ← node mới join
```

### Kiểm tra Python Gateway

```bash
python main.py status
# farm_sensor [mock] — healthy
# lora_gateway [serial_at] — healthy    ← kết nối LoRa Gateway OK
```

## 2. Add TOML profile cho device mới

```bash
cp gateway/device_manager/device_profiles/templates/serial_sensor.toml \
   gateway/device_manager/device_profiles/my_sensor.toml
# Sửa [device] name, port, tools
```

## 3. Gỡ node

```bash
# Xoá TOML profile hoặc mark inactive
# Edge tự động ignore khi reload catalog
```

## 4. Kiểm tra trạng thái

```bash
python main.py status
# Liệt kê tất cả device + trạng thái health
# → MCP tool: fleet.list_devices
```
