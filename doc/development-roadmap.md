# Phát Triển Hệ Thống — Kế Hoạch Chi Tiết

> **Phiên bản:** 1.0 | **Ngày:** 08/06/2026
> **Mục tiêu:** Hoàn thiện edge gateway với khả năng hoạt động độc lập khi offline, an toàn khi điều khiển thiết bị, và tự động hóa thu thập dữ liệu.

---

## Mục lục

1. [Rule Engine — Phát Hiện Bất Thường](#1-rule-engine--phát-hiện-bất-thường)
2. [Safety Layer — An Toàn Khi Điều Khiển](#2-safety-layer--an-toàn-khi-điều-khiển)
3. [Background Recorder — Thu Thập Dữ Liệu 24/7](#3-background-recorder--thu-thập-dữ-liệu-247)
4. [Notifier — Thông Báo Đa Kênh](#4-notifier--thông-báo-đa-kênh)
5. [Audit Log — Ghi Lịch Sử Bất Biến](#5-audit-log--ghi-lịch-sử-bất-biến)
6. [Device Hotplug — Tự Động Phát Hiện Thiết Bị](#6-device-hotplug--tự-động-phát-hiện-thiết-bị)
7. [Scheduler — Lịch Tưới Tự Động](#7-scheduler--lịch-tưới-tự-động)
8. [Dashboard Web UI](#8-dashboard-web-ui)
9. [Lộ Trình Ưu Tiên](#9-lộ-trình-ưu-tiên)

---

## 1. Rule Engine — Phát Hiện Bất Thường

### 1.1. Mục đích

Khi edge gateway hoạt động **offline** (không kết nối được LLM Server), Rule Engine là thành phần duy nhất có thể tự động phát hiện bất thường và cảnh báo. Nó chạy 24/7, kiểm tra từng reading mới so với ngưỡng cấu hình.

### 1.2. Kiến trúc

```
Recorder nhận reading mới
    │
    ▼
Rule Engine kiểm tra tất cả rules
    │
    ├── OK → bỏ qua
    │
    └── Violation → ghi alert vào SQLite
                     │
                     ├── Nếu severity CRITICAL → gọi Notifier ngay
                     └── Nếu severity WARNING → gọi Notifier (nếu online)
```

### 1.3. Rules (R01–R09)

| ID | Tên | Điều kiện | Severity | Hành động | Ghi chú |
|----|-----|-----------|----------|-----------|---------|
| **R01** | `temperature_high` | `value > max_temp` | CRITICAL | alert + notifier | Tránh chết cây/cháy |
| **R02** | `temperature_low` | `value < min_temp` | CRITICAL | alert + notifier | Chống băng giá |
| **R03** | `soil_dry` | `moisture < min_moisture` | WARNING | alert | Gợi ý tưới |
| **R04** | `soil_wet` | `moisture > max_moisture` | WARNING | alert | Ngập úng |
| **R05** | `battery_low` | `battery_pct < 20` | WARNING | alert + notifier | Sạc pin/thay pin |
| **R06** | `node_offline` | `no_data > 1 hour` | WARNING | alert | Mất kết nối node |
| **R07** | `stuck_sensor` | `value không đổi > 6h` | WARNING | alert | Cảm biến hỏng |
| **R08** | `rate_of_change` | `|Δvalue/Δt| > threshold` | WARNING | alert | Thay đổi đột ngột |
| **R09** | `actuator_overtime` | `actuator ON > duration + 5 phút` | CRITICAL | alert + emergency_stop | An toàn |

### 1.4. Cấu hình (YAML)

```yaml
# config/rules.yaml
rules:
  - id: R01
    name: temperature_high
    sensor: temperature
    condition: value > 40
    severity: CRITICAL
    action: alert_telegram

  - id: R03
    name: soil_dry
    sensor: moisture
    condition: value < 20
    severity: WARNING
    action: alert_telegram
```

### 1.5. File đề xuất

```
mcp_server/
├── rules/
│   ├── __init__.py
│   ├── engine.py        # RuleEngine class
│   └── rules.yaml        # Rule definitions
```

### 1.6. API

```python
class RuleEngine:
    def __init__(self, rules_path: str, recorder: Recorder):
        """
        - Tải rules từ YAML
        - Đăng ký callback với Recorder (khi có reading mới)
        """

    async def evaluate(self, reading: Reading):
        """
        Kiểm tra reading với tất cả rules.
        Nếu vi phạm → ghi alert vào SQLite.
        """

    async def evaluate_all(self, readings: list[Reading]):
        """
        Batch evaluation khi startup hoặc periodic check.
        """
```

---

## 2. Safety Layer — An Toàn Khi Điều Khiển

### 2.1. Mục đích

Hiện tại chỉ có `asyncio.Lock` per device trong Aggregator. Cần thêm:

1. **Tool-level permission** — Read/Write separation
2. **Approval workflow** — Actuator commands cần human confirm
3. **Rate limiting** — Giới hạn số lần actuation trong khoảng thời gian

### 2.2. Kiến trúc

```
Agent → MCP Server → Safety Layer → Aggregator → Adapter
                          │
                    ┌─────┴──────┐
                    │            │
              Permission    Approval
                Check        Gate
                              │
                    (nếu cần confirm)
                    ┌─────────┴────────┐
                    │                  │
              User approve       User reject
                    │                  │
              execute            cancel
```

### 2.3. Permission Model

| Loại | Tool | Mặc định |
|------|------|---------|
| **Read** | `fleet.*`, `call_device` (get_*) | ✅ Cho phép |
| **Write** | `call_device` (set_*, control_*) | ❌ Cần policy |

### 2.4. Approval Workflow

Khi agent gọi write tool:

```
1. Safety Layer nhận request
2. Kiểm tra permission (tool-level)
3. Nếu là high-risk → tạo pending approval
4. Gửi notification: "Bạn có muốn bật bơm khu A 10 phút?"
5. User trả lời "Có" / "Không"
6. Nếu "Có" → execute; "Không" → cancel
```

### 2.5. Rate Limiting

```
- Tối đa 5 actuation command mỗi 10 phút
- Tối đa 2 emergency stop mỗi 30 phút
- Reset khi hết khoảng thời gian
```

### 2.6. File đề xuất

```
mcp_server/
├── safety/
│   ├── __init__.py
│   ├── permissions.py     # Read/write separation
│   ├── approval.py         # Approval gate
│   └── rate_limit.py       # Rate limiter
```

### 2.7. API

```python
class SafetyLayer:
    async def check_permission(self, tool_name: str, action: str) -> bool:
        """Kiểm tra tool có được phép gọi không."""

    async def request_approval(self, user_id: str, command: dict) -> str:
        """Tạo approval request, return approval_id."""

    async def confirm_approval(self, approval_id: str, approved: bool):
        """User xác nhận / từ chối."""

    async def check_rate_limit(self, tool_name: str) -> bool:
        """Kiểm tra rate limit."""
```

---

## 3. Background Recorder — Thu Thập Dữ Liệu 24/7

### 3.1. Mục đích

Hiện tại đã có code `background_recorder.py` nhưng tạm dừng sau FastMCP migration. Cần kích hoạt lại trong `agrimesh daemon`.

### 3.2. Kiến trúc

```
agrimesh daemon
    │
    ├── FastMCP server (HTTP :8374)
    │
    └── BackgroundRecorder
            │
            ├── farm_sensor: poll mỗi 300s
            │   ├── get_temperature → 32.5°C → SQLite
            │   └── get_humidity → 68.2% → SQLite
            │
            ├── serial_sensor: poll mỗi 300s
            │
            └── mqtt_sensor: poll mỗi 300s
```

### 3.3. Device Registration

Khi daemon start:
```
1. Đọc devices/*.toml
2. Parse → DeviceModel
3. Register vào SQLite devices table
4. Start polling loop per device
```

### 3.4. Tích hợp Rule Engine

Sau khi poll và ghi reading, gọi `RuleEngine.evaluate(reading)` để kiểm tra threshold.

### 3.5. API

```python
class BackgroundRecorder:
    async def start(self):
        """Start polling loops cho tất cả devices."""

    async def register_devices(self):
        """Đọc TOML profiles → register vào SQLite."""

    async def stop(self):
        """Cancel tất cả polling tasks."""
```

---

## 4. Notifier — Thông Báo Đa Kênh

### 4.1. Mục đích

Khi Rule Engine phát hiện bất thường hoặc Safety Layer cần confirm, cần gửi notification đến người dùng.

### 4.2. Các kênh

| Kênh | Ưu tiên | Khi nào dùng |
|------|---------|-------------|
| **Console** | ✅ Mặc định | Khi agent online, user đang chat |
| **Telegram Bot** | ✅ Nên triển khai | Khi offline, push notification 24/7 |
| **SMS** | ❌ Tùy chọn | Khi khẩn cấp, không internet |

### 4.3. Các loại notification

| Loại | Trigger | Nội dung |
|------|---------|----------|
| Alert | Rule Engine vi phạm | `[CRITICAL] Nhiệt độ khu A: 42°C (ngưỡng: 40°C)` |
| Approval | Safety Layer cần confirm | `[CONFIRM] Bật bơm khu A 10 phút? Yes/No` |
| Report | Định kỳ (sáng/tối) | `[BÁO CÁO] Nhiệt độ TB: 32°C, 4 thiết bị online` |
| Health | Device offline > 1h | `[WARNING] Node soil_01 mất kết nối` |

### 4.4. File đề xuất

```
mcp_server/
├── notifier/
│   ├── __init__.py
│   ├── base.py             # Base notifier interface
│   ├── console.py          # Console notifier (in ra terminal)
│   └── telegram.py         # Telegram bot
```

### 4.5. API

```python
class Notifier:
    async def send_alert(self, alert: Alert):
        """Gửi alert đến tất cả channels."""

    async def send_approval(self, approval: ApprovalRequest) -> str:
        """Gửi yêu cầu xác nhận, chờ response."""

    async def send_report(self, report: str):
        """Gửi báo cáo định kỳ."""
```

---

## 5. Audit Log — Ghi Lịch Sử Bất Biến

### 5.1. Mục đích

Mọi hành động trên hệ thống cần được ghi lại không thể thay đổi — ai đã gọi tool gì, khi nào, kết quả thế nào.

### 5.2. Schema

```sql
CREATE TABLE audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   INTEGER NOT NULL,
    actor       TEXT    NOT NULL,      -- 'user' / 'ai_agent' / 'rule_engine'
    action      TEXT    NOT NULL,      -- tool name
    target      TEXT,                  -- device_id
    params      TEXT,                  -- JSON arguments
    result      TEXT,                  -- 'success' / 'failed' / 'timeout'
    duration_ms INTEGER,
    sha256_prev TEXT,                  -- Previous log's SHA256 (hash chain)
    sha256_this TEXT                   -- Current log's SHA256
);
```

### 5.3. Hash Chain

```
Log 1: sha256_this = SHA256(data1)
Log 2: sha256_prev = Log1.sha256_this
       sha256_this = SHA256(data2 + Log1.sha256_this)
Log 3: sha256_prev = Log2.sha256_this
       ...
```

Không thể sửa log cũ mà không làm mất hash chain.

### 5.4. File đề xuất

```
mcp_server/
├── audit/
│   ├── __init__.py
│   └── logger.py           # Append-only audit logger
```

---

## 6. Device Hotplug — Tự Động Phát Hiện Thiết Bị

### 6.1. Mục đích

Hiện tại devices được định nghĩa tĩnh trong TOML files. Cần tự động phát hiện khi có thiết bị mới kết nối (MQTT connect, Serial plug).

### 6.2. Luồng

```
Adapter phát hiện thiết bị mới
    │
    ├── MQTT: thiết bị publish lên topic mới
    ├── Serial: thiết bị gửi "hello" message
    │
    ▼
Discovery nhận event
    │
    ├── Tự động tạo DeviceModel
    ├── Register vào SQLite
    ├── Generate MCP tools
    └── Gửi notification: "Thiết bị mới: soil_02"
```

### 6.3. File đề xuất

```
mcp_server/
├── discovery.py            # Mở rộng: thêm hotplug handler
```

---

## 7. Scheduler — Lịch Tưới Tự Động

### 7.1. Mục đích

Cho phép user đặt lịch tưới tự động — "Mỗi sáng 6h tưới khu A 10 phút". Hoạt động ngay cả khi offline.

### 7.2. Schema

```sql
CREATE TABLE schedules (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id      INTEGER NOT NULL,
    actuator_id  TEXT    NOT NULL,   -- pump, valve
    cron_expr    TEXT    NOT NULL,   -- "0 6 * * *" = 6h sáng mỗi ngày
    duration_sec INTEGER NOT NULL,
    enabled      INTEGER DEFAULT 1,
    created_by   TEXT,               -- 'user' / 'ai_agent'
    last_run     INTEGER
);
```

### 7.3. Service

```python
class SchedulerService:
    """Chạy background, kiểm tra mỗi phút, thực thi schedule đến hạn."""

    async def start(self):
        """Start cron checker."""

    async def add_schedule(self, schedule: Schedule):
        """Thêm lịch mới."""

    async def remove_schedule(self, schedule_id: int):
        """Xoá lịch."""
```

---

## 8. Dashboard Web UI

### 8.1. Mục đích

Giao diện web để:
- Xem devices + trạng thái
- Xem readings real-time (charts)
- Chat với agent
- Quản lý rules, schedules
- Xem audit log

### 8.2. Tech stack

```
Frontend: React + Vite
Backend:  FastAPI (trong agrimesh daemon)
Charts:   Chart.js / Recharts
```

### 8.3. API endpoints

```
GET  /api/devices            → Danh sách thiết bị
GET  /api/devices/{id}       → Chi tiết thiết bị
GET  /api/readings/{id}      → Lịch sử readings
GET  /api/alerts             → Alert gần đây
POST /api/chat               → Chat với agent
```

---

## 9. Lộ Trình Ưu Tiên

### Phase 1 — Nền Tảng Offline (Ưu tiên cao nhất)

| # | Tính năng | Phụ thuộc | Thời gian |
|---|-----------|-----------|-----------|
| 1 | **Rule Engine** (R01-R09) | Recorder | 3-5 ngày |
| 2 | **Background Recorder** (kích hoạt lại) | FastMCP lifespan | 1 ngày |
| 3 | **Console Notifier** (in alert ra terminal) | Rule Engine | 1 ngày |

**Kết quả Phase 1:** Khi offline, gateway tự động:
- Poll sensors + ghi SQLite
- Kiểm tra threshold 9 rules
- Ghi alert và in cảnh báo ra console

### Phase 2 — An Toàn & Thông Báo

| # | Tính năng | Phụ thuộc | Thời gian |
|---|-----------|-----------|-----------|
| 4 | **Safety Layer** (permission + approval) | Aggregator | 3-5 ngày |
| 5 | **Telegram Notifier** | Safety Layer | 2-3 ngày |
| 6 | **Audit Log** (hash chain) | Recorder | 2 ngày |

### Phase 3 — Tự Động Hóa

| # | Tính năng | Phụ thuộc | Thời gian |
|---|-----------|-----------|-----------|
| 7 | **Device Hotplug** (MQTT auto-detect) | Discovery | 2-3 ngày |
| 8 | **Scheduler Service** (lịch tưới) | Safety Layer | 3-4 ngày |
| 9 | **Báo cáo định kỳ** | Notifier + Rule Engine | 2 ngày |

### Phase 4 — Giao Diện

| # | Tính năng | Phụ thuộc | Thời gian |
|---|-----------|-----------|-----------|
| 10 | **Dashboard Web UI** | Tất cả Phase 1-3 | 5-7 ngày |
| 11 | **MCP Prompts** nâng cao | — | 1 ngày |

---

## Tóm Tắt File Structure

Sau khi hoàn thành, `mcp_server/` sẽ có thêm:

```
mcp_server/
├── rules/
│   ├── __init__.py
│   ├── engine.py              # Rule Engine
│   └── rules.yaml             # Rule definitions
├── safety/
│   ├── __init__.py
│   ├── permissions.py         # Read/write separation
│   ├── approval.py            # Approval gate
│   └── rate_limit.py          # Rate limiter
├── notifier/
│   ├── __init__.py
│   ├── base.py                # Base notifier
│   ├── console.py             # Console notifier
│   └── telegram.py            # Telegram bot
├── audit/
│   ├── __init__.py
│   └── logger.py              # Audit log + hash chain
└── scheduler/
    ├── __init__.py
    └── service.py             # Cron scheduler

(Thêm ~15 files, ~1500 dòng)
```
