# Node Provisioning Guide

> **Phiên bản:** 1.0 | **Ngày:** 12/06/2026
> **Nhóm:** Operations — 🟢 Cần khi deploy

---

## 1. Thêm node mới

### Hardware checklist

- [ ] ESP32-S3 đã hàn chân header
- [ ] SX1262 kết nối đúng SPI pins
- [ ] DHT22 (sensor) hoặc Relay module (actuator) đã kết nối
- [ ] Nguồn: LiPo 3.7V (sensor) hoặc 12V (actuator)

### Flash firmware

```bash
# Build và upload sensor node
pio run -e sensor_node -t upload --upload-port /dev/ttyUSB1

# Build và upload actuator node
pio run -e actuator_node -t upload --upload-port /dev/ttyUSB1
```

### Verify node join mesh

```bash
# Mở serial monitor
pio run -e sensor_node -t monitor

# Expected output:
# === AgriMeshAI Sensor Node ===
# [Sensor] DHT22 started
# [LoRa] Mesh started, address=0xA1B2

# Trên Edge:
# +NODE_JOIN:0xA1B2,0,1.0
# → node_id tự động gán
```

## 2. Gỡ node

```bash
# Trên Edge — MCP tool hoặc SQLite trực tiếp
UPDATE nodes SET status = 'inactive' WHERE node_id = 5;
```

## 3. Kiểm tra trạng thái

```bash
# Edge checks
python main.py status
# Liệt kê tất cả node + trạng thái

# Check qua AT command
AT+LIST_NODES\r\n
```
