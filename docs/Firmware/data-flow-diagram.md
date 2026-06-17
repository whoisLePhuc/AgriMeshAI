# Data Flow Diagram — End-to-End Scenarios

> **Phiên bản:** 2.0 | **Ngày:** 17/06/2026
> **Nhóm:** Architecture & Design — 🔴 Bắt buộc

---

## Scenario 1: Sensor Periodic Push (định kỳ mỗi 60s)

```
┌──────────┐   LoRa    ┌────────────┐  UART    ┌────────────────────┐       ┌──────────────┐
│ Sensor   │──────────►│ LoRa       │─────────►│ Edge Gateway       │       │  User        │
│ Node     │ packet    │ Gateway    │ AT cmd   │ (Python)           │       │  (Telegram/  │
│          │ 12 bytes  │            │+TEMP_    │                    │       │   MCP)       │
│ DHT22    │ sensor_id │ parse      │ REPORT   │ SerialATAdapter    │       │              │
│ đọc temp │ seq       │→UART send  │:1,0,25.3 │ → _dispatch_line() │       │              │
│ + hum    │ timestamp │            │          │ → cache_set()      │       │              │
│ mỗi 60s  │ value f32 │            │          │ → on_temp_report   │       │              │
└──────────┘           └────────────┘          │ → SQLite store     │       │              │
                                               │ → EventBus emit    │       │              │
                                               │   → RuleEngine     │       │              │
                                               │   → Notifier       │       │              │
                                                └────────────────────┘       └──────────────┘
```

**Chi tiết:**

1. Sensor đọc DHT22 → `dht.readTemperature()` → 25.3°C
2. Tạo `SensorReading` struct (12 bytes packed): `[0x01][sensor_id][seq][timestamp][value_f32]`
3. `LoRaMesher::Send(0x0001, payload)` — gửi đến Gateway
4. LoRa Gateway nhận `on_loRa_data(source, data)` — switch case `MSG_SENSOR_DATA`
5. Kiểm tra pending `PEND_TEMP`:
   - Có → `+TEMP:node,sensor,val,SEQ=n` (solicited)
   - Không → `+TEMP_REPORT:node,sensor,val` (unsolicited)
6. Python SerialATAdapter nhận → `_dispatch_line()` parse → cache → callback
7. `DatabaseManager._handle_write()` → `ReadingStore.record()` → SQLite
8. EventBus emit `reading_recorded` → RuleEngine check rules

## Scenario 2: User Queries Temperature (cache-first + pull)

```
User ──► MCP ──► SystemManager
  "nhiệt độ
   khu A?"
            │
            ├── Check sensor cache: reading < 90s?
            │   ├── YES → trả cache ngay
            │   │   ◄── "Khu A: 25.3°C (cập nhật 20s trước)"
            │   │
            │   └── NO (cache stale) →
            │       SerialATAdapter.at_get_temp(node_id)
            │       ┌── AT+GET_TEMP=1,SEQ=42\r\n
            │       │   ──UART──► LoRa Gateway
            │       │            │
            │       │            ├── Gửi PING (0x20) → Sensor PONG
            │       │            ├── Gửi 0xFF → Sensor trả data
            │       │            └── +TEMP:1,0,25.3,SEQ=42\r\n
            │       │   ◄──UART──│
            │       │   parse SEQ=42 → resolve pending Future
            │       │   → cache_set() → trả user
            │ ◄─────│
◄─ trả lời──│
```

## Scenario 3: User Controls Actuator

```
User ──► Safety ──► SystemManager.call_tool("actuator.relay", {})
  "tưới khu B         Check:
   10 phút"           - node online (health check)
                      - duration ≤ 30 phút
                      - relay đang OFF

                      SerialATAdapter.at_set_relay(2, 0, 1, 600)
                      AT+SET_RELAY=2,0,1,600,SEQ=43\r\n
                      ──UART──► LoRa Gateway
                               │ handle_command → parse_relay
                               │ → alloc_pending(PEND_ACK)
                               │
                               │ LoRa: RELAY_CMD (0x10)
                               │ relay_id=0, cmd=1 ON, dur=600000ms
                               │ ──LoRa──► Actuator Node
                               │          │
                               │          │ set_relay(0, ON)
                               │          │ start auto-off timer (600s)
                               │          │ blink_request = 1
                               │          │ send_ack → RELAY_ACK (0x11)
                               │ ◄──LoRa──│
                               │ +RELAY_ACK:2,0,ON,SEQ=43
                      ◄──UART──│
                      parse SEQ=43 → resolve Future
                      → "Đã bật bơm khu B (tự động tắt sau 10 phút)"
```

**Safety checks (Edge thực hiện trước khi gửi):**
1. Node actuator có online không? (health check status)
2. Duration ≤ 30 phút? (clamp nếu quá)
3. Relay đang OFF? (nếu đang ON → báo user)

## Scenario 4: Node Auto-Discovery

```
Node mới boot
     │
     ├── LoRaMesher.Start()
     ├── Join mesh
     │
     ├── Gửi ANNOUNCE broadcast (0x02)
     │   └── node_type=0 (sensor), fw_ver=0x10
     │
     ▼
LoRa Gateway nhận on_loRa_data → MSG_ANNOUNCE
     │
     ├── find_or_add_node(src, ntype) → thêm vào node table
     ├── at_fmt_node_join → +NODE_JOIN:0xA1B2,0,1.0\r\n
     │
     ▼
Edge SerialATAdapter nhận +NODE_JOIN
     │
     ├── Auto-assign node_id (next_node_id++)
     ├── Lưu vào self.nodes dict
     ├── Fire on_node_join callback
     ├── Gửi AT+NODE_ACK=0xA1B2,5,SEQ=1\r\n
     │
     ▼
LoRa Gateway nhận AT+NODE_ACK
     │
     └── Cập nhật node_table[idx].node_id = new_id
         → mapping hoàn tất
```

## Scenario 5: Actuator Rejoin + State Sync

```
Actuator Node mất LoRa kết nối → tự động rejoin
     │
     ├── Gửi ANNOUNCE (0x02) với node_type=1 (actuator)
     │
     ▼
LoRa Gateway nhận ANNOUNCE
     │
     ├── Check: lora_addr đã có trong node table?
     │   │
     │   ├── YES (rejoin)
     │   │   ├── +NODE_JOIN:0xE5F6,1,1.0 (Edge update last_seen)
     │   │   └── RELAY_SYNC (0x12) → Actuator
     │   │       → Actuator trả RELAY_ACK cho từng channel (có vTaskDelay 50ms)
     │   │       → Gateway forward +RELAY_REPORT:id,ch,state
     │   │       → Edge biết trạng thái relay thực tế
     │   │
     │   └── NO (node mới) → Edge gán node_id mới
     │
     ▼
Edge update nodes dict
```

**Heartbeat (Gateway initiative — mỗi 2 phút):**
```
LoRa Gateway firmware:
  for each actuator node:
    Gửi PING (0x20) → chờ PONG 5s
  Sau 5s: at_fmt_hb(responded, total, seq)
  → +HB:2/3,SEQ=1\r\n  (2/3 actuator responded)
```

**Rule Engine missing data check (daemon loop — mỗi 5 phút):**
```
RuleEngine.check_missing(hours=1.0)
  → ReadingStore.get_all_latest()
  → Nếu last_seen > 1h → emit "alert_triggered" (R09)
```
