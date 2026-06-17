# API Reference — AT Commands & Response

> **Phiên bản:** 1.1 | **Ngày:** 12/06/2026
> **Nhóm:** Implementation Reference — 🟡 Quan trọng

---

## 1. AT Commands (Edge → Gateway → LoRa)

### AT+GET_TEMP

Query temperature từ sensor node. Edge ưu tiên SQLite cache nếu reading < 90s, chỉ query live khi cache stale.

```
Edge → Gateway: AT+GET_TEMP=<node_id>,SEQ=<n>\r\n
Gateway → Edge: +TEMP:<node_id>,<sensor_id>,<value>,SEQ=<n>\r\n
```

| Tham số | Kiểu | Mô tả |
|---------|------|-------|
| `node_id` | int | 1-255 |
| `SEQ` | int | 0-255, sequence number |
| `sensor_id` | int | 0=temp, 1=humidity |
| `value` | float | °C |

**Cache TTL:** 90 giây (push interval 60s + 50% buffer)

**Gateway behavior:**

1. Lookup `lora_addr` từ `node_id`
2. Gửi PING đến node
3. Chờ PONG (tối đa 5s)
4. Nếu PONG timeout → `+ERR:2,timeout,SEQ=<n>`
5. Nếu PONG ok → gửi request temp
6. Chờ response → forward `+TEMP:...,SEQ=<n>`

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
| `state` | int | 0=OFF, 1=ON |
| `duration_s` | int | 0=indefinite, >0=auto-off sau N giây, max 1800 |

**Gateway behavior:**

1. Clamp duration ≤ 1800
2. Gửi RELAY_CMD qua LoRa
3. Chờ RELAY_ACK (tối đa 5s)
4. Forward ACK hoặc timeout error

### AT+PING

Kiểm tra node còn sống không.

```
Edge → Gateway: AT+NODE_INIT,SEQ=<n>\r\n
Gateway → Edge: +NODE_INIT:OK,SEQ=<n>\r\n
```

### AT+NODE_ACK

```
Edge → Gateway: AT+NODE_ACK=<lora_addr>,<node_id>,SEQ=<n>\r\n
Gateway → Edge: +NODE_ACK:OK,SEQ=<n>\r\n
```

### AT+PING_ALL (heartbeat — định kỳ mỗi 2 phút)

```
Edge → Gateway: AT+PING_ALL,SEQ=<n>\r\n
Gateway → Edge (for each node): +PONG:<node_id>,SEQ=<n>\r\n
                              hoặc: +ERR:2,timeout,SEQ=<n>\r\n
```

### +ERR

```
+ERR:<code>,<message>,SEQ=<n>\r\n
```

| Code | Message | Mô tả |
|------|---------|-------|
| 1 | `node not found` | node_id không có trong routing table |
| 2 | `timeout` | Node không response sau 3 lần retry |
| 3 | `invalid params` | Sai format AT command |
| 4 | `uart buffer full` | UART RX buffer overflow (1024B) |
| 5 | `mesh not ready` | LoRaMesher chưa Start() hoặc chưa join network |
