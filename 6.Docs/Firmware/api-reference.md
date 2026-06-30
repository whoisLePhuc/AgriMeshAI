# API Reference — AT Commands & Response

> **Phiên bản:** 2.0 | **Ngày:** 17/06/2026
> **Nhóm:** Implementation Reference — 🟡 Quan trọng

---

## 1. AT Commands (Edge → Gateway → LoRa)

### AT+GET_TEMP

Query temperature từ sensor node. Edge ưu tiên SQLite cache nếu reading < 90s, chỉ query live khi cache stale.

**2-phase protocol:**
1. Gửi PING đến node → chờ PONG (xác nhận node alive)
2. Gửi 0xFF → sensor trả SensorReading
3. Parse response → trả user

```
Edge → Gateway: AT+GET_TEMP=<node_id>,SEQ=<n>\r\n
Gateway → Edge: +TEMP:<node_id>,<sensor_id>,<value>,SEQ=<n>\r\n
```

| Tham số | Kiểu | Mô tả |
|---------|------|-------|
| `node_id` | int | 1-255 |
| `SEQ` | int | 0-65535, sequence number (uint16_t) |
| `sensor_id` | int | 0=temp, 1=humidity, 2=moisture, 3=light |
| `value` | float | °C |

**Cache TTL:** 90 giây (push interval 60s + 50% buffer)

### AT+SET_RELAY

Điều khiển relay actuator.

```
Edge → Gateway: AT+SET_RELAY=<node_id>,<relay_id>,<state>,<duration_s>,SEQ=<n>\r\n
Gateway → Edge: +RELAY_ACK:<node_id>,<relay_id>,<state>,SEQ=<n>\r\n
```

| Tham số | Kiểu | Mô tả |
|---------|------|-------|
| `node_id` | int | 1-255 |
| `relay_id` | int | 0-3 |
| `state` | int | 0=OFF, 1=ON, 2=TOGGLE |
| `duration_s` | int | 0=indefinite, >0=auto-off sau N giây, max 1800 |

**Gateway behavior:**
1. Clamp duration ≤ 1800 (30 phút)
2. Gửi RELAY_CMD qua LoRa
3. Chờ RELAY_ACK (tối đa 5s)
4. Forward ACK hoặc timeout error

### AT+PING

Kiểm tra node còn sống không.

```
Edge → Gateway: AT+PING=<node_id>,SEQ=<n>\r\n
Gateway → Edge: +PONG:<node_id>,SEQ=<n>\r\n
```

### AT+PING_ALL

Ping tất cả actuator nodes (heartbeat).

```
Edge → Gateway: AT+PING_ALL,SEQ=<n>\r\n
Gateway → Edge: +HB:<responded>/<total>,SEQ=<n>\r\n
```

### AT+LIST_NODES

Danh sách tất cả node trong routing table.

```
Edge → Gateway: AT+LIST_NODES,SEQ=<n>\r\n
Gateway → Edge: +NODES:<count>,<id1>,<type1>,...,SEQ=<n>\r\n
```

### AT+NODE_INIT

Khởi tạo auto-discovery.

```
Edge → Gateway: AT+NODE_INIT,SEQ=<n>\r\n
Gateway → Edge: +NODE_INIT:OK\r\n
```

### AT+NODE_ACK

Edge gán node_id cho node mới join mesh.

```
Edge → Gateway: AT+NODE_ACK=<lora_addr>,<node_id>,SEQ=<n>\r\n
Gateway → Edge: +NODE_ACK:OK\r\n
```

## 2. Unsolicited Events (Gateway → Edge)

| Event | Khi nào | Mô tả |
|-------|---------|-------|
| `+TEMP_REPORT:<id>,<sensor>,<val>` | Sensor push định kỳ (60s) | Dữ liệu cảm biến |
| `+RELAY_REPORT:<id>,<relay>,<state>` | Actuator state change | Trạng thái relay |
| `+NODE_JOIN:<addr>,<type>,<ver>` | Node mới join mesh | Edge tự gán node_id |
| `+ERR:<code>,<msg>,SEQ=<n>` | Lỗi thực thi | Kèm mã lỗi |

## 3. Error Codes

| Code | Message | Mô tả |
|------|---------|-------|
| 1 | `node not found` | node_id không có trong routing table |
| 2 | `timeout` | Node không response sau timeout 5s |
| 3 | `invalid params` | Sai format AT command |
| 4 | `uart buffer full` | UART RX buffer overflow (1024B) |
| 5 | `mesh not ready` | LoRaMesher chưa Start() hoặc chưa join network |

## 4. Edge Timeout & Retry

| Tham số | Giá trị | Mô tả |
|---------|---------|-------|
| Timeout mỗi request | 2.0s | Chờ response từ Gateway |
| Retry tối đa | 3 lần | Mỗi lần SEQ mới |
| SEQ range | uint16_t (0-65535) | Wrap-around an toàn |
| Pending slots | 4 | Tối đa request chờ response trên LoRa Gateway (giới hạn firmware) |
