# Protocol Specification — AT Commands & LoRa Mesh Packet

> **Phiên bản:** 1.1 | **Ngày:** 12/06/2026
> **Nhóm:** Architecture & Design — 🔴 Bắt buộc

---

## 1. UART Protocol (Edge ↔ LoRa Gateway)

Text-based AT commands, 115200 baud 8N1. Kết thúc bằng `\r\n`.

**Sequence number (SEQ):** Mọi command từ Edge đều kèm `,SEQ=<n>` (uint8_t, 0-255, wrap-around). Gateway echo SEQ vào response. Dùng để match response với request khi out-of-order.

**UART buffer:** 1024 bytes ring buffer, double-buffered. Đủ cho ~40 unsolicited messages trong burst (với 20 node trong mesh).

### 1.1 Edge → Gateway (Commands)

| Command | Mô tả | Response |
|---------|-------|----------|
| `AT+GET_TEMP=<node_id>,SEQ=<n>` | Query sensor temperature | `+TEMP:<node_id>,<sensor_id>,<value>,SEQ=<n>` |
| `AT+SET_RELAY=<node_id>,<channel>,<state>,<duration_s>,SEQ=<n>` | Control relay | `+RELAY_ACK:<node_id>,<channel>,<state>,SEQ=<n>` |
| `AT+PING=<node_id>,SEQ=<n>` | Check node alive | `+PONG:<node_id>,SEQ=<n>` |
| `AT+LIST_NODES,SEQ=<n>` | List all known nodes | `+NODES:<count>,<id1>,<type1>,...,SEQ=<n>` |
| `AT+NODE_INIT,SEQ=<n>` | Start auto-discovery | `+NODE_INIT:OK,SEQ=<n>` |
| `AT+NODE_ACK=<lora_addr>,<node_id>,SEQ=<n>` | Assign node_id to new node | `+NODE_ACK:OK,SEQ=<n>` |

### 1.2 Gateway → Edge (Responses & Unsolicited)

| Message | Loại | Mô tả |
|---------|------|-------|
| `+TEMP:<node_id>,<sensor_id>,<value>,SEQ=<n>` | Response | Trả lời GET_TEMP |
| `+RELAY_ACK:<node_id>,<channel>,<state>,SEQ=<n>` | Response | Xác nhận SET_RELAY |
| `+PONG:<node_id>,SEQ=<n>` | Response | Trả lời PING |
| `+NODES:<count>,<id1>,<t1>,...,SEQ=<n>` | Response | Trả lời LIST_NODES |
| `+TEMP_REPORT:<node_id>,<sensor_id>,<value>` | Unsolicited | Sensor push định kỳ _(không có SEQ)_ |
| `+RELAY_REPORT:<node_id>,<channel>,<state>` | Unsolicited | Actuator tự động báo _(không có SEQ)_ |
| `+NODE_JOIN:<lora_addr>,<type>,<fw_ver>` | Unsolicited | Node mới join mesh _(không có SEQ)_ |
| `+ERR:<code>,<message>,SEQ=<n>` | Error | Lỗi (SEQ echo từ request gây lỗi) |

### 1.3 Error Codes

| Code | Ý nghĩa |
|------|---------|
| 1 | Node not found (lora_addr không có trong routing table) |
| 2 | Timeout (không có response từ node sau 3 lần retry) |
| 3 | Invalid params (sai format, thiếu tham số) |
| 4 | UART buffer full |
| 5 | LoRa mesh not ready (chưa join network) |

### 1.4 Quy tắc Edge

- **SEQ:** Edge tự quản lý uint8_t counter (0-255, wrap-around). Mỗi request gửi `SEQ=<counter>`. Gateway chỉ echo SEQ vào response — không tự sinh. Unsolicited messages (TEMP_REPORT, RELAY_REPORT, NODE_JOIN) không có SEQ.
- **SEQ timestamp tracking:** Edge lưu `{seq: n, sent_at: timestamp}` trong pending map và auto-reject mọi response đến sau `sent_at + timeout * retries` (6s). Xử lý hoàn toàn vấn đề SEQ wrap-around và response zombie từ mesh.
- **Serialization:** Edge chỉ gửi tối đa 1 request pending đến Gateway tại một thời điểm. Gateway không cần command queue — Edge tự serialize AT commands qua asyncio.
- **Timeout:** 2s cho mỗi request
- **Retry:** 3 lần, backoff cố định (không exponential — do LoRa đã chậm). Mỗi retry dùng SEQ mới
- Nếu hết retry → `+ERR:2,timeout,SEQ=<last_seq>`
- Gateway response có thể out-of-order (do LoRa mesh latency) — SEQ + timestamp matching đảm bảo ghép đúng

## 2. LoRa Mesh Packet (giữa các LoRa node)

Payload bytes, gửi qua `LoRaMesher::Send()` dạng broadcast hoặc unicast.

### 2.1 Message Types

| Type Byte | Tên | Hướng | Mô tả |
|-----------|-----|-------|-------|
| `0x01` | `TEMP_READING` | Sensor → Gateway | Nhiệt độ / độ ẩm |
| `0x02` | `ANNOUNCE` | Node → all | Node mới join mesh hoặc rejoin |
| `0x10` | `RELAY_CMD` | Gateway → Actuator | Điều khiển relay |
| `0x11` | `RELAY_ACK` | Actuator → Gateway | Xác nhận relay |
| `0x12` | `RELAY_SYNC` | Gateway → Actuator | Yêu cầu state sync (sau rejoin) |
| `0x20` | `PING` | Gateway → Node | Kiểm tra alive |
| `0x21` | `PONG` | Node → Gateway | Response ping |

### 2.2 Packet Formats

**TEMP_READING** (0x01, 6 bytes):
```
[0x01][sensor_id(1B)][value(float32 LE)]
```
- `sensor_id`: 0=temp, 1=humidity
- `value`: IEEE 754 float, little-endian

**ANNOUNCE** (0x02, 3 bytes):
```
[0x02][node_type(1B)][fw_ver(1B)]
```
- `node_type`: 0=sensor, 1=actuator
- `fw_ver`: major.minor packed (0x10 = v1.0)

**RELAY_CMD** (0x10, 7 bytes):
```
[0x10][relay_id(1B)][cmd(1B)][duration_ms(uint32 LE)]
```
- `cmd`: 0=OFF, 1=ON, 2=TOGGLE
- `duration_ms`: 0=indefinite, >0=tự động OFF sau N ms

**RELAY_ACK** (0x11, 3 bytes):
```
[0x11][relay_id(1B)][state(1B)]
```
- `state`: 0=OFF, 1=ON

**RELAY_SYNC** (0x12, 1 byte):
```
[0x12]
```
- Actuator responds with RELAY_ACK for each relay channel
- Dùng bởi Gateway ngay sau khi Actuator rejoin mesh

**PING** (0x20, 1 byte):
```
[0x20]
```

**PONG** (0x21, 3 bytes):
```
[0x21][uptime_hours(2B LE)]
```

### 2.3 Addressing trong LoRa Mesh

- Gateway address: `0x0001` (set qua `withNodeAddress(0x0001)`)
- Sensor/Actuator: auto-generate từ MAC (LoRaMesher default)
- Tất cả gói tin sender dùng `GetNodeAddress()` làm source
- Gateway gửi `Send(dest_addr, payload)` đến node cụ thể
- Node gửi `Send(0x0001, payload)` đến Gateway

## 3. Discovery & Rejoin Sequence

### Initial Join
```
Node boot → LoRaMesher.Start() → tự động join mesh
     → gửi ANNOUNCE broadcast (type=0x02)
     → Gateway nhận → tạo +NODE_JOIN UART message
     → Edge nhận → gán node_id → gửi AT+NODE_ACK
     → Gateway internal register (node_id ↔ lora_addr mapping)
```

### Rejoin (after node lost LoRa connection)
```
Node detect mất mesh (LoRaMesher: DISCOVERY state)
     → Re-join tự động (LoRaMesher self-healing)
     → gửi ANNOUNCE broadcast (type=0x02, same format)
     → Gateway nhận → +NODE_JOIN (Edge xử lý: node đã có ID? → update last_seen)
     → Gateway gửi RELAY_SYNC (0x12) đến actuator để lấy trạng thái relay hiện tại
     → Actuator trả RELAY_ACK (0x11) cho mỗi channel → Gateway forward +RELAY_REPORT
```

### Heartbeat (Edge initiative — định kỳ mỗi 2 phút)
```
Edge → Gateway: AT+PING=<node_id>,SEQ=<n>   (PING tất cả actuator nodes)
Gateway → Actuator: PING (0x20) → chờ PONG (5s timeout)
     → Gateway → Edge: +PONG:<node_id>,SEQ=<n>
     Nếu không PONG → Gateway → Edge: +ERR:2,timeout,SEQ=<n>
     → Edge đánh dấu status='offline', log warning
