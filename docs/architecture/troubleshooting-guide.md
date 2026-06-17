# Troubleshooting Guide

> **Phiên bản:** 1.0 | **Ngày:** 12/06/2026
> **Nhóm:** Operations — 🟢 Cần khi deploy

---

## 1. UART không có response

| Nguyên nhân | Kiểm tra | Fix |
|------------|---------|-----|
| Sai baud rate | `stty -F /dev/ttyUSB0` | `stty -F /dev/ttyUSB0 115200` |
| Sai port | `ls /dev/ttyUSB*` | Chọn đúng port |
| Gateway chưa boot | Đèn LED trên ESP32? | Chờ 5s, reset |
| USB disconnect | `dmesg | grep tty` | Cắm lại USB |

## 2. Node không join mesh

| Nguyên nhân | Kiểm tra | Fix |
|------------|---------|-----|
| LoRaMesher chưa Start() | Serial log có "Mesh started"? | Check code |
| SX1262 sai wiring | SPI pins đúng? | Check schematic |
| TCXO voltage | Heltec V3 | `radioConfig.setTcxoVoltage(1.8F)` |
| Sai frequency | `AT+LIST_NODES` rỗng | Check `LORA_FREQ` (868/915) |
| Antenna chưa gắn | Kiểm tra antenna | Gắn antenna SMA |

## 3. AT command lỗi

```
+ERR:1,node not found  → node_id sai hoặc node offline
+ERR:2,timeout         → node không response (retry hết)
+ERR:3,invalid params  → sai format AT command
+ERR:4,uart buffer full→ gửi quá nhanh, giãn cách 100ms
+ERR:5,mesh not ready  → LoRaMesher chưa sẵn sàng
```

## 4. Actuator không hoạt động

| Nguyên nhân | Fix |
|------------|-----|
| Relay chưa cấp nguồn | Kiểm tra nguồn 12V |
| Relay chập chờn | Kiểm tra optocoupler |
| Auto-off đã tắt trước đó | Kiểm tra duration_s trong log |
| Node offline | Kiểm tra `AT+PING=<node_id>` |
