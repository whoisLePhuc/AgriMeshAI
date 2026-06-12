# Firmware Design — LoRa Mesh Nodes

> Phiên bản: 1.1 | Ngày: 12/06/2026
> Nhóm: Implementation Reference — 🟡 Quan trọng

---

## 1. LoRa Gateway — lora_gateway_main.cpp

### State Machine

```
┌──────────┐     UART nhận     ┌──────────────┐
│ IDLE     │──────────────────►│ CMD_PARSING  │
│ (chờ     │                   │ (parse AT     │
│  UART/   │◄──────────────────│  command)     │
│  LoRa)   │   parse xong      └──────┬───────┘
└──────────┘                          │
     ▲                          ┌──────┴───────┐
     │                   ┌──────┤ CMD_ROUTING  │──────┐
     │                   │      │ (dispatch)   │      │
     │                   │      └──────────────┘      │
     │                   │                           │
     │                   ▼                           ▼
     │          ┌──────────────┐            ┌──────────────┐
     │          │ SEND_LORA    │            │ SEND_UART    │
     │          │ (LoRaMesher  │            │ (gửi response │
     │          │  Send)       │            │  trực tiếp   │
     │          └──────┬───────┘            └──────┬───────┘
     │                 │                          │
     │                 ▼                          ▼
     │          ┌──────────────┐            ┌──────────────┐
     │          │ WAIT_ACK     │            │ IDLE         │
     │          │ (nếu cần)    │───────────►│              │
     │          └──────────────┘            └──────────────┘
     │
     │  UART RX Handler: ISR, double-buffered
     │  ┌─────────────────────────────────────────┐
     │  │ ISR: nhận byte → push vào ring buffer   │
     │  │ Main loop: \n → trích xuất full command  │
     │  └─────────────────────────────────────────┘
```

### UART Ring Buffer

```c
#define UART_RX_BUF_SIZE 1024  // up from 256 — đủ cho ~40 unsolicited msg
                                // với 20 node, mỗi ~23 bytes/frame

typedef struct {
    uint8_t data[UART_RX_BUF_SIZE];
    volatile size_t head;
    volatile size_t tail;
} ringbuf_t;

// ISR: ghi byte
void uart_isr() {
    ringbuf.data[ringbuf.head] = byte;
    ringbuf.head = (ringbuf.head + 1) % UART_RX_BUF_SIZE;
}

// TX flow control: khi burst unsolicited messages, delay 5-10ms
// giữa các frame để Edge có thời gian drain buffer
void uart_send_flow(const char* fmt, ...) {
    static uint32_t last_tx = 0;
    uint32_t now = millis();
    if (now - last_tx < 5) delay(5 - (now - last_tx));  // min 5ms gap
    // gửi frame qua UART
    last_tx = millis();
}
```

### AT Command → LoRa Dispatch

```
OnCommand(AT+GET_TEMP=<node_id>,SEQ=<seq>):
    lora_addr = lookup(node_id)
    Send(0x20, [])          // PING trước
    if PONG received:
        Send(lora_addr, [0xFF])  // request temp (type tùy chỉnh)
    else:
        uart_send("+ERR:2,timeout,SEQ=%d\r\n", seq)

OnCommand(AT+SET_RELAY=<node_id>,<relay>,<state>,<dur>,SEQ=<seq>):
    lora_addr = lookup(node_id)
    // clamp duration ≤ 1800s
    Send(lora_addr, [0x10, relay, state, dur_uint32_bytes])
    // đợi RELAY_ACK → echo SEQ back

OnCommand(AT+PING=<node_id>,SEQ=<seq>):
    lora_addr = lookup(node_id)
    Send(lora_addr, [0x20])
    // đợi PONG → echo SEQ back

OnCommand(AT+LIST_NODES,SEQ=<seq>):
    // trả routing table nội bộ
    uart_send("+NODES:%d,%d,%d,...,SEQ=%d\r\n", count, ...)
```

### LoRa → UART Bridge (SEQ echo cho responses)

```
OnDataReceived(source, data):
  parse messagetype
  case TEMP_READING:
    node_id = lookup_node_id(source)
    uart_send("+TEMP_REPORT:%d,%d,%.1f\r\n", node_id, data[1], *(float*)(data+2))
  case ANNOUNCE (0x02):
    uart_send("+NODE_JOIN:0x%04X,%d,%d.%d\r\n", source, data[1], data[2]>>4, data[2]&0x0F)
    // Nếu source đã có node_id (rejoin) → gửi RELAY_SYNC (0x12) đến actuator
    if node_type == ACTUATOR && is_rejoin(source):
        Send(source, [0x12])  // yêu cầu state sync
  case RELAY_ACK (0x11):
    node_id = lookup_node_id(source)
    uart_send("+RELAY_REPORT:%d,%d,%s\r\n", node_id, data[1], data[2]?"ON":"OFF")
    // echo SEQ nếu ACK này là response cho SET_RELAY trước đó
    if pending_seq:
        uart_send("+RELAY_ACK:%d,%d,%s,SEQ=%d\r\n", node_id, data[1], ..., pending_seq)
  case RELAY_ACK from RELAY_SYNC (0x12 response):
    node_id = lookup_node_id(source)
    uart_send("+RELAY_REPORT:%d,%d,%s\r\n", node_id, data[1], data[2]?"ON":"OFF")
  case PONG (0x21):
    uart_send("+PONG:%d\r\n", lookup_node_id(source))
```

### Heartbeat (background timer — mỗi 2 phút)

```
loop():
    if millis() - last_heartbeat >= 120_000:
        for each actuator node_id in routing_table:
            Send(lora_addr, [0x20])  // PING
            if no PONG within 5s:
                uart_send("+ERR:2,timeout,SEQ=%d\r\n", heartbeat_seq)
                heartbeat_seq++
        last_heartbeat = millis()
```

## 2. Sensor Node — sensor_main.cpp

### Loop (main) — Đọc cả temp + humidity

```
setup(): 
  - init DHT22
  - init LoRaMesher (NODE_ONLY role)
  - register OnDataReceived
  - Start()
  
loop():
  if millis() - last_send >= 60_000:
    temp = dht.readTemperature()
    hum  = dht.readHumidity()
    
    if !isnan(temp):
      payload = [0x01, 0, temp_float32_bytes]   // sensor_id=0: temperature
      Send(0x0001, payload)
    if !isnan(hum):
      payload = [0x01, 1, hum_float32_bytes]    // sensor_id=1: humidity
      Send(0x0001, payload)
      
    last_send = millis()
  vTaskDelay(10)

OnDataReceived(source, data):
  case PING (0x20):
    Send(source, [0x21, uptime_2bytes])  // PONG
```

### Power Saving (tương lai)

- Giữa các lần gửi: light sleep (RTC memory, timer wake)
- Sau 60s không có lệnh: deep sleep (wake bởi timer)
- Cần thêm PrepareSleepCallback cho LoRaMesher

## 3. Actuator Node — actuator_main.cpp

### Relay State Machine

```
┌────────────┐
│ RELAY_OFF  │
│ (default)  │◄──────────────────────┐
└──────┬─────┘                       │
       │ RELAY_CMD(ON)               │ auto-off timer expired
       ▼                             │
┌────────────┐      RELAY_CMD(OFF)   │
│ RELAY_ON   │───────────────────────┘
│ (timer     │
│  đang đếm) │
│ auto-off   │
│ sau N ms   │
└────────────┘
```

### Loop (main)

```
setup():
  - init 4 relays (GPIO 14-17, default OFF)
  - init LoRaMesher (NODE_ONLY role)
  - register OnDataReceived
  - Start()
  - Gửi ANNOUNCE (0x02) ngay sau khi join mesh thành công
  
OnDataReceived(source, data):
  if source != 0x0001: return       // lora_addr whitelist — chỉ nhận lệnh từ Gateway

  case RELAY_CMD (0x10):
    relay_id = data[1], cmd = data[2], duration = *(uint32*)(data+3)
    if cmd == ON:  setRelay(relay_id, ON),  start timer(duration)
    if cmd == OFF: setRelay(relay_id, OFF), stop timer
    if cmd == TOGGLE: toggle relay
    Send(0x0001, [0x11, relay_id, state])     // gửi ACK
  case RELAY_SYNC (0x12):                      // state sync sau rejoin
    for each relay channel:
      Send(0x0001, [0x11, relay_id, state])    // gửi ACTUAL state
  case PING (0x20):
    Send(source, [0x21, uptime_2bytes])        // PONG
    
loop():
  for each relay:
    if relay ON && timer expired:
      setRelay(relay_id, OFF)
      Send(0x0001, [0x11, relay_id, 0])        // auto-off report
  vTaskDelay(100)
```

### Safety

- **lora_addr whitelist:** `if (source != 0x0001) return;` — actuator chỉ chấp nhận RELAY_CMD từ Gateway (0x0001). Chặn mọi packet giả mạo từ node không xác định
- Max ON duration: 30 phút (hard clamp, bất kể lệnh từ Edge)
- Nếu relay ON quá 30 phút → tự động OFF (watchdog safety)
- Mỗi relay độc lập
- Khi rejoin mesh: tự động gửi ANNOUNCE → Gateway sẽ gửi RELAY_SYNC → state sync
- Heartbeat từ Gateway 2 phút/lần: nếu mất kết nối, Edge đánh dấu OFFLINE nhưng relay auto-off vẫn chạy độc lập
