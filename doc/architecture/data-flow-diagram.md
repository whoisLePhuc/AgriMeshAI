# Data Flow Diagram — End-to-End Scenarios

> **Phiên bản:** 1.1 | **Ngày:** 12/06/2026
> **Nhóm:** Architecture & Design — 🔴 Bắt buộc

---

## Scenario 1: Sensor Periodic Push (định kỳ mỗi 60s)

```
┌──────────┐   LoRa    ┌────────────┐  UART    ┌────────────┐       ┌──────────────┐
│ Sensor   │──────────►│ LoRa       │─────────►│ Edge       │       │  User        │
│ Node     │ packet    │ Gateway    │ AT cmd   │ Gateway    │       │  (Telegram/  │
│          │ 0x01      │            │+TEMP_    │            │       │   MCP)       │
│ DHT22    │ sensor_id │ parse      │ REPORT   │ SQLite     │       │              │
│ đọc temp │ value     │→UART send  │:1,0,25.3 │ store      │       │              │
│ mỗi 60s  │           │            │          │ EventBus   │       │              │
└──────────┘           └────────────┘          │→RuleEngine │       │              │
                                               │ →Notifier  │       │              │
```                                            └────────────┘       └──────────────┘

**Chi tiết:**

1. Sensor đọc DHT22 → `dht.readTemperature()` → 25.3°C
2. Tạo payload: `[0x01][0][25.3 as float32 LE]` (6 bytes)
3. `LoRaMesher::Send(GATEWAY_ADDR, payload)`
4. LoRa Gateway nhận `OnDataReceived(source, data)`
5. Parse type=0x01 → `+TEMP_REPORT:<node_id>,0,25.3\r\n` → UART send
6. Edge nhận → SerialATAdapter.parse → `TEMP_REPORT` event
7. SQLite: `INSERT INTO readings (...)`
8. EventBus: emit `reading_recorded` → RuleEngine check
9. Nếu >40°C → alert → Telegram

## Scenario 2: User Queries Temperature (cache-first + pull)

```
User ──► MCP ──► SystemManager
  "nhiệt độ
   khu A?"
            │
            ├── Check SQLite cache: reading cuối cùng < 90s?
            │   ├── YES → trả cache ngay (push interval = 60s, TTL = 90s)
            │   │   ◄── "Khu A: 25.3°C (cập nhật 20s trước)"
            │   │
            │   └── NO (cache stale or user yêu cầu real-time) →
            │       SerialATAdapter
            │       AT+GET_TEMP=1,SEQ=42\r\n
            │       ──UART──► LoRa Gateway
            │                │
            │                │ LoRa: PING (0x20)
            │                │ ──LoRa──► Sensor Node
            │                │          │
            │                │          │ ◄──PONG──
            │                │          │ LoRa: request temp
            │                │          │ ──LoRa──► Sensor Node
            │                │          │          │
            │                │          │ ◄──0x01──
            │                │          │ +TEMP:1,0,25.3,SEQ=42
            │                │ ◄──UART──│
            │                │ parse SEQ=42 → return to pending
            │ ◄──result──────│
◄─ trả lời──│
```

**Chi tiết:**

1. Edge nhận lệnh "nhiệt độ khu A?"
2. Lookup SQLite cache: `readings` WHERE `node_id=1 AND sensor_id='0' ORDER BY timestamp DESC LIMIT 1`
3. Nếu `timestamp > now - 90s` → trả cache, end
4. Nếu cache stale → gửi `AT+GET_TEMP=1,SEQ=42\r\n`
5. LoRa Gateway: PING → PONG → request temp → parse response
6. Gateway echo `+TEMP:1,0,25.3,SEQ=42\r\n`
7. Edge match SEQ=42 với pending request → parse → cache → trả user
8. Nếu timeout → `+ERR:2,timeout,SEQ=42` → trả cache cũ nếu có

## Scenario 3: User Controls Actuator

```
User ──► Safety ──► MCP ──► SystemManager
  "tưới khu B         Check:
   10 phút"           - node online
                      - duration ≤ 30ph
                      - không actuator nào đang chạy xung đột
                      
                      SerialATAdapter
                      AT+SET_RELAY=2,0,1,600\r\n
                      ──UART──► LoRa Gateway
                               │
                               │ LoRa: RELAY_CMD (0x10)
                               │ relay_id=0, cmd=1 ON, dur=600s
                               │ ──LoRa──► Actuator Node
                               │          │
                               │          │ setRelay(0, ON)
                               │          │ start auto-off timer (600s)
                               │          │ gửi RELAY_ACK (0x11)
                               │ ◄──LoRa──│
                               │ +RELAY_ACK:2,0,ON
                      ◄──UART──│
                      parse response
                      
User ◄── "Đã bật bơm khu B (tự động tắt sau 10 phút)" ── MCP
```

**Safety checks (Edge thực hiện trước khi gửi):**

1. Node actuator có online không? (check last_seen)
2. Duration ≤ 30 phút? (clamp nếu quá)
3. Relay đang OFF? (nếu đang ON → báo user)
4. Ghi log vào `actuation_log` table

## Scenario 4: Node Auto-Discovery

```
Node mới boot
     │
     ├── LoRaMesher.Start()
     ├── Join mesh (sponsor-based join)
     │
     ├── Gửi ANNOUNCE broadcast (0x02)
     │   └── node_type=0 (sensor), fw_ver=0x10
     │
     ▼
LoRa Gateway nhận OnDataReceived
     │
     ├── Parse type=0x02 → ANNOUNCE
     ├── +NODE_JOIN:0xA1B2,0,1.0\r\n  (lora_addr, sensor, fw)
     │
     ▼
Edge nhận +NODE_JOIN
     │
     ├── Tạo node_id mới (max existing + 1)
     ├── SQLite: INSERT INTO nodes (node_id, lora_addr, type, status='active')
     ├── Gửi AT+NODE_ACK=0xA1B2,5\r\n
     │
     ▼
LoRa Gateway nhận AT+NODE_ACK (internal, không forward LoRa)
     │
     └── OK — mapping complete

## Scenario 5: Actuator Offline → Rejoin + State Sync

```
Actuator Node mất LoRa kết nối (interference, distance)
     │
     │  LoRaMesher tự detect DISCOVERY state
     │  (không nhận sync beacon > 5 superframes)
     │
     │  ┌──────────────────────────────────────────┐
     │  │ RELAY VẪN ON nếu timer chưa hết!         │
     │  │ (auto-off timer chạy độc lập trên node)  │
     │  └──────────────────────────────────────────┘
     │
     ▼
Actuator tự động rejoin (LoRaMesher self-healing)
     │
     ├── Gửi ANNOUNCE broadcast (0x02)
     │   └── node_type=1 (actuator), fw_ver=0x10
     │
     ▼
LoRa Gateway nhận ANNOUNCE
     │
     ├── Check: lora_addr này đã có node_id?
     │   ├── YES (đây là rejoin)
     │   │   ├── UART: +NODE_JOIN:0xE5F6,1,1.0  (Edge update last_seen)
     │   │   └── Gửi RELAY_SYNC (0x12) đến Actuator
     │   │       → Actuator trả RELAY_ACK cho từng channel
     │   │       → Gateway forward +RELAY_REPORT:<node_id>,<ch>,<state>
     │   │       → Edge biết ngay trạng thái relay thực tế
     │   │
     │   └── NO (node mới thật) → Edge gán node_id như scenario 4
     │
     ▼
Edge update status: 'active', last_seen = now()
```

**Heartbeat (Edge initiative — mỗi 2 phút):**

```
Edge ──► Gateway: AT+PING=<node_id>,SEQ=<n>   (PING tất cả actuator nodes)
     ──► Gateway ──► Actuator: PING (0x20)
          ◄── PONG OK → Gateway → Edge: +PONG:<node_id>,SEQ=<n>  → update last_seen
          ◄── timeout (5s) → Gateway → Edge: +ERR:2,timeout,SEQ=<n>
               → Edge: UPDATE nodes SET status='offline'
               → log warning vào event_log
```
```
