# Firmware Design — LoRa Mesh Nodes

> **Phiên bản:** 2.0 | **Ngày:** 17/06/2026
> **Nhóm:** Implementation Reference — 🟡 Quan trọng

---

> **Ghi chú:** Firmware hiện tại đã triển khai 3 node trong `2.Firmware/src/`: sensor, actuator, LoRa gateway. Phần mô tả dưới đây phản ánh code thực tế.

## 1. LoRa Gateway — `firmware/src/lora_gateway_main.cpp`

### UART ↔ LoRa Bridge

```
UART (AT commands) ←→ AT dispatcher ←→ LoRa mesh bridge
ISR-based UART RX (ringbuf) + main-loop line reading
Pending request slots (4 max) với state+addr matching
```

### AT Command Dispatch

```
UART line → handle_command(line)
  │
  ├── AT+GET_TEMP=<id>,SEQ=<n>
  │   → find_node_by_id → alloc_pending(PEND_PING)
  │   → Send PING (0x20) → nhận PONG → switch PEND_TEMP → Send 0xFF → nhận SensorReading → +TEMP
  │
  ├── AT+SET_RELAY=<id>,<relay>,<cmd>,<dur>,SEQ=<n>
  │   → parse_relay → find_node → alloc_pending(PEND_ACK)
  │   → Send RELAY_CMD (0x10) → nhận RELAY_ACK → +RELAY_ACK
  │
  ├── AT+PING=<id>,SEQ=<n>
  │   → alloc_pending(PEND_PONG) → Send PING → nhận PONG → +PONG
  │
  ├── AT+PING_ALL,SEQ=<n>
  │   → Ping tất cả actuator → chờ → +HB:responded/total,SEQ=n
  │
  ├── AT+LIST_NODES → +NODES:count,id,type,...
  ├── AT+NODE_INIT → +NODE_INIT:OK
  └── AT+NODE_ACK=<addr>,<id> → update node_id → +NODE_ACK:OK
```

### LoRa → UART Bridge

```
on_loRa_data(src, data):
  switch (data[0]):
    case MSG_SENSOR_DATA (0x01):
      → Kiểm tra pending PEND_TEMP → +TEMP:... hoặc +TEMP_REPORT:...
    case MSG_ANNOUNCE (0x02):
      → find_or_add_node → +NODE_JOIN...
      → Nếu actuator rejoin → RELAY_SYNC
    case MSG_RELAY_ACK (0x11):
      → Kiểm tra pending PEND_ACK → +RELAY_ACK hoặc +RELAY_REPORT
    case MSG_PONG (0x21):
      → Kiểm tra PEND_PING (GET_TEMP phase 1) → switch to PEND_TEMP
      → Kiểm tra PEND_PONG (AT+PING) → +PONG
      → Heartbeat tracking
```

### Heartbeat (background timer — mỗi 2 phút)

```
loop():
  if millis() - last_hb_ms >= HEARTBEAT_INTERVAL_MS (120s):
    for each actuator node:
      Send PING (0x20)
    hb_pending.waiting = true
  if hb_pending.waiting && timeout 5s:
    at_fmt_hb(responded, total, hb_seq) → +HB:2/3,SEQ=1\r\n
```

### Pending Request (4 slots, timeout 5s)

| State | Mục đích | Timeout |
|-------|----------|---------|
| `PEND_PING` | GET_TEMP phase 1: chờ PONG | 5s |
| `PEND_TEMP` | GET_TEMP phase 2: chờ sensor data | 5s |
| `PEND_ACK` | SET_RELAY: chờ RELAY_ACK | 5s |
| `PEND_PONG` | AT+PING: chờ PONG | 5s |

### Node Table

- Tối đa 20 nodes (`MAX_NODES`)
- Tự động thêm khi nhận ANNOUNCE
- `node_id` do Edge gán qua `AT+NODE_ACK`
- `lora_addr` (uint16_t) do LoRaMesher tự sinh

## 2. Sensor Node — `firmware/src/sensor_main.cpp`

### Initialization Flow

```
setup():
  ├── Watchdog (WDT 30s) — esp_task_wdt_init
  ├── DHT22.begin()
  ├── LoRaMesher.Build() + Start()
  ├── load_or_save_addr() — NVS persist
  └── send_announce() → MSG_ANNOUNCE (0x02)
```

### Loop (main) — Đọc temp + humidity

```
loop():
  ├── esp_task_wdt_reset()
  │
  ├── Xử lý ping flag (thread-safe via spinlock)
  │   portENTER_CRITICAL(&ping_mux)
  │   if flag_ping_valid → Pong → Send
  │   portEXIT_CRITICAL(&ping_mux)
  │
  ├── Xử lý 0xFF flag (on-demand sensor data)
  │   if flag_send_now → send_sensor_data()
  │
  ├── Periodic push (mỗi 60s)
  │   if (int32_t)(now - last_send_ms) >= SENSOR_PUSH_INTERVAL_MS
  │   → send_sensor_data()
  │
  ├── Periodic re-announce (mỗi 10 phút)
  │   if (int32_t)(now - last_announce_ms) >= ANNOUNCE_INTERVAL_MS
  │   → send_announce()
  │
  ├── Flush retry queue (1 packet mỗi loop)
  │   → flush_retry_queue()
  │
  └── vTaskDelay(100ms)
```

### send_sensor_data()

```
send_sensor_data():
  ├── Guard: last DHT read > 2.1s ago? (DHT_MIN_INTERVAL_MS)
  ├── float temp = dht.readTemperature()
  ├── float hum = dht.readHumidity()
  │
  ├── seq_num++
  │
  ├── SensorReading tr (12 bytes packed)
  │   type=0x01, sensor_id, seq, timestamp, value
  │
  ├── if !isnan(temp): safe_send(GATEWAY_LORA_ADDR, SensorReading(temp))
  └── if !isnan(hum):  safe_send(GATEWAY_LORA_ADDR, SensorReading(hum))
```

### Retry Queue

```
safe_send(dst, payload):
  ├── mesher->Send(dst, payload)
  ├── Nếu fail → push vào retry_queue (tối đa 20)
  │   Mỗi packet giữ dst riêng (không hardcode GATEWAY)
  └── flush_retry_queue: retry tối đa 5 lần

PendingPacket { AddressType dst; vector<uint8_t> payload; uint8_t retries; }
```

### Dual-Core Safety (spinlock)

```cpp
// Callback (LoRaMesher RTOS task) — chỉ set flag
static portMUX_TYPE ping_mux = portMUX_INITIALIZER_UNLOCKED;

void on_loRa(AddressType src, ...) {
  portENTER_CRITICAL_ISR(&ping_mux);
  flag_ping_valid = true;
  ping_src = src;
  portEXIT_CRITICAL_ISR(&ping_mux);
}

// Loop() — đọc flag an toàn
portENTER_CRITICAL(&ping_mux);
bool do_ping = flag_ping_valid; flag_ping_valid = false;
portEXIT_CRITICAL(&ping_mux);
```

### Packet Format (SensorReading — 12 bytes packed)

```
[0x01][sensor_id(1B)][seq(2B LE)][timestamp(4B LE)][value(4B f32 LE)]
  type    sensor_id       seq           timestamp         value
```

## 3. Actuator Node — `firmware/src/actuator_main.cpp`

### Initialization

```
setup():
  ├── Watchdog (WDT 30s) — mới thêm, safety quan trọng
  ├── 4 relay: pinMode OUTPUT, default LOW (OFF)
  ├── LoRaMesher.Build() + Start()
  ├── send_announce() → MSG_ANNOUNCE
  └── last_announce_ms = millis()
```

### Loop (main)

```
loop():
  ├── esp_task_wdt_reset()
  ├── Xử lý blink_request từ callback (không vTaskDelay trong ISR)
  ├── check_timers() — auto-off safety
  ├── Periodic re-announce (mỗi 10 phút)
  └── vTaskDelay(100ms)
```

### LoRa Receive — Whitelist + Dispatch

```
on_loRa(src, data):
  if src != GATEWAY_LORA_ADDR → return (whitelist)
  switch (data[0]):
    case MSG_RELAY_CMD (0x10):
      → relay_id, cmd, duration
      → set_relay() + auto-off timer
      → send_ack() → blink_request = 1 (không gọi blink trực tiếp)
    case MSG_RELAY_SYNC (0x12):
      → send_ack cho từng channel (có vTaskDelay 50ms)
    case MSG_PING (0x20):
      → Pong
```

### Auto-off Timer

```cpp
check_timers():
  for each relay:
    if relay on && auto_off_ms > 0:
      if (int32_t)(now - on_since) >= (int32_t)auto_off_ms:
        set_relay(i, false)
        send_ack(i, 0)  // báo Edge
```

- Max ON duration: 30 phút (`MAX_ON_DURATION_MS`)
- Wrap-around safe: `(int32_t)` cast
- Safety: kể cả mất kết nối LoRa, auto-off vẫn chạy

### Relay Control

| GPIO | Relay | Thường dùng |
|------|-------|-------------|
| 14 | Relay 0 | Bơm nước |
| 15 | Relay 1 | Van solenoid |
| 16 | Relay 2 | Quạt |
| 17 | Relay 3 | Đèn |

---

## 6. Tính năng bổ sung (Cross-Cutting)

### 6.1. RingBuf — ISR-Safe UART RX Buffer

**File:** `lib/mesh_protocol/ringbuf.h`

Dùng trong LoRa Gateway (`lora_gateway_main.cpp`) để đọc UART từ Edge (Jetson Nano)
mà không block IRQ. Buffer vòng 1024 bytes, ISR-safe.

```
Hardware UART RX → ISR (serialEvent) → ringbuf.push(byte)
                        ↓
              main loop → ringbuf.read_line(buf, 256) → handle_command(buf)
```

Đặc điểm:
- `push()` gọi từ ISR context — không blocking, không malloc
- `read_line()` gọi từ main loop — trả về dòng kết thúc bằng `\r\n`
- Overflow: push trả về false nếu buffer đầy, không crash

### 6.2. Retry Queue — Sensor Node

**File:** `sensor_main.cpp:50-113`

Sensor node có cơ chế retry khi gửi LoRa thất bại:

```
safe_send(dst, payload):
    mesher->Send() → nếu fail → push vào retry_queue (max 20 items)

flush_retry_queue():
    pop front → mesher->Send() → nếu fail → retry (tối đa 3 lần)
                                  → nếu hết retry → drop packet
```

- Queue được bảo vệ bởi `queue_mux` spinlock (dual-core safety)
- Mỗi packet nhớ destination address riêng
- Một packet được retry mỗi vòng loop (100ms间隔)

### 6.3. Watchdog Timer (WDT)

**File:** Cả 3 firmware — `esp_task_wdt_init(WDT_TIMEOUT_S, true)`

| Node | Timeout | Hành vi khi timeout |
|------|---------|-------------------|
| Sensor | 30s | ESP restart |
| Actuator | 30s | ESP restart (quan trọng: tránh relay kẹt ON) |
| LoRa Gateway | Không có | (phụ thuộc UART từ Edge) |

WDT được reset mỗi vòng loop qua `esp_task_wdt_reset()`. Nếu loop treo
(deadlock, infinite loop, exception), WDT sẽ reset ESP trong 30s.

### 6.4. Heartbeat Sweep — LoRa Gateway

**File:** `lora_gateway_main.cpp:249-276`

Gateway tự động PING tất cả actuator nodes mỗi 120s (`HEARTBEAT_INTERVAL_MS`):

```
loop():
    if now - last_hb_ms >= 120s:
        for each actuator in node_table:
            mesher->Send(actuator, MSG_PING)
        hb_pending = {seq, count, responded=0, started=now}
    
    if hb_waiting && now - started > 5s:
        uart_send("+HB:responded/total,SEQ=seq")
```

Kết quả `+HB` được gửi lên Edge để monitoring — cho biết bao nhiêu actuator còn sống.

### 6.5. MAX_NODES = 20 — Node Table Limit

**File:** `lora_gateway_main.cpp:21`

LoRa Gateway giới hạn routing table ở 20 nodes:

```cpp
#define MAX_NODES 20
static NodeEntry node_table[MAX_NODES];
```

Khi table đầy, node mới join bị từ chối: `+ERR:4,table full addr=0x...`.
Nếu cần support >20 nodes, tăng hằng số này và kiểm tra RAM.

### 6.6. Pending Request Slots = 4

**File:** `lora_gateway_main.cpp:22`

Gateway chỉ xử lý tối đa 4 request đồng thời:

```cpp
#define MAX_PENDING 4
PendingReq pending[MAX_PENDING];
// States: PEND_IDLE, PEND_PING, PEND_TEMP, PEND_ACK, PEND_PONG
```

Mỗi slot có timeout 5s (`PEND_TIMEOUT_MS`). Hết hạn → `+ERR:2,timeout`.
Nếu cả 4 slot đều busy → `+ERR:4,pending full`.

### 6.7. Auto-Off Safety Timer — Actuator

**File:** `actuator_main.cpp:174-187`

Relay có auto-off timer để tránh kẹt ON vĩnh viễn:

```
check_timers():
    for each relay:
        if relay.on && auto_off_ms > 0:
            if now - on_since >= auto_off_ms:
                set_relay(i, OFF)
                send_ack(i, 0)
```

- Max duration: 30 phút (`MAX_ON_DURATION_MS`)
- Wrap-around an toàn: dùng `(int32_t)` cast
- Safety: hoạt động ngay cả khi mất kết nối LoRa

### 6.8. Dual-Core Spinlock Protection

**File:** `sensor_main.cpp:59-63` và `lora_gateway_main.cpp` (queue_mux)

ESP32-S3 là dual-core. Biến chia sẻ giữa LoRaMesher callback task
(chạy trên core khác) và main loop được bảo vệ bởi spinlock:

```cpp
// Sensor node — ping_src flag
static portMUX_TYPE ping_mux = portMUX_INITIALIZER_UNLOCKED;

// Callback (ISR context):
portENTER_CRITICAL_ISR(&ping_mux);
ping_src = src;
flag_ping_valid = true;
portEXIT_CRITICAL_ISR(&ping_mux);

// Main loop:
portENTER_CRITICAL(&ping_mux);
if (flag_ping_valid) { ... flag_ping_valid = false; }
portEXIT_CRITICAL(&ping_mux);
```

Spinlock đảm bảo atomic read-modify-write — `volatile` không đủ an toàn
trên Xtensa dual-core.
