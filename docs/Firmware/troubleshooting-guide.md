# Troubleshooting Guide

> **Phiên bản:** 2.0 | **Ngày:** 17/06/2026
> **Nhóm:** Operations — 🟢 Cần khi deploy

---

## 1. UART không có response

| Nguyên nhân | Kiểm tra | Fix |
|------------|---------|-----|
| Sai baud rate | `stty -F /dev/ttyUSB0` | `stty -F /dev/ttyUSB0 115200` |
| Sai port | `ls /dev/ttyUSB*` | Chọn đúng port |
| Gateway chưa boot | Đèn LED trên ESP32? | Chờ 5s, reset |
| USB disconnect | `dmesg &#124; grep tty` | Cắm lại USB |

## 2. Node không join mesh

| Nguyên nhân | Kiểm tra | Fix |
|------------|---------|-----|
| LoRaMesher chưa Start() | Serial log có "LoRa online"? | Check code |
| SX1262 sai wiring | SPI pins đúng? | CS=8, RST=12, IRQ=14, IO1=13 |
| TCXO voltage | Heltec V3 cần 1.8V | `radioConfig.setTcxoVoltage(1.8F)` |
| Sai frequency | Cả gateway và node phải cùng tần số | Check 868.0MHz |
| Antenna chưa gắn | Kiểm tra antenna SMA | Gắn antenna |

## 3. AT command lỗi

```
+ERR:1,node not found  → node_id sai hoặc node chưa join mesh
+ERR:2,timeout         → node không response trong 5s
+ERR:3,invalid params  → sai format AT command (cmd phải 0-2)
+ERR:4,uart buffer full→ gửi quá nhanh, cần giãn cách
+ERR:5,mesh not ready  → LoRaMesher chưa Start()
```

## 4. Actuator không hoạt động

| Nguyên nhân | Fix |
|------------|-----|
| Relay chưa cấp nguồn | Kiểm tra nguồn 12V |
| Sai relay_id (0-3) | `AT+SET_RELAY=1,0,1,10` (relay 0) |
| Duration sai | Kiểm tra: `at_parse_relay` đã nhân ×1000 |
| Node offline | Kiểm tra `AT+PING=<node_id>` |

## 5. Heartbeat không có response

```
Edge: AT+PING_ALL,SEQ=1\r\n
→ +HB:0/3,SEQ=1\r\n  (0/3 actuator responded)
→ Kiểm tra actuator nodes có online không?
→ Kiểm tra HEARTBEAT_INTERVAL_MS (120s)
```

## 6. Sensor không push dữ liệu

| Nguyên nhân | Fix |
|------------|-----|
| DHT22 lỗi | Log: `"DHT22 read failed"` — check wiring |
| DHT guard | 2.1s tối thiểu giữa 2 lần đọc |
| LoRa send fail | Kiểm tra retry queue log |
| Chưa join mesh | Chờ ANNOUNCE, kiểm tra `+NODE_JOIN` |
