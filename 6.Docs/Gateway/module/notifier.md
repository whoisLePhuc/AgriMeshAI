# Notifier — Thiết kế module

> **Module:** `notifier/`
> **Phiên bản:** 1.0
> **Ngày:** 17/06/2026

---

## 1. Tổng quan

### Mục đích

`notifier` là module chịu trách nhiệm gửi cảnh báo từ Rule Engine đến người dùng qua nhiều kênh khác nhau.

- Nhận sự kiện `alert_triggered` từ `EventBus`
- Phân phối đến tất cả kênh đã bật (console, telegram, webhook, sms)
- Mỗi kênh hoạt động độc lập — một kênh fail không ảnh hưởng kênh khác
- Cấu hình qua file YAML, cho phép bật/tắt từng kênh

### Vị trí trong hệ thống

```
RuleEngine — emit "alert_triggered" via EventBus
    │
    ▼
NotifierManager._on_alert()
    │
    ├── ConsoleNotifier.send_alert()     → stderr
    ├── TelegramNotifier.send_alert()    → Telegram Bot API
    ├── WebhookNotifier.send_alert()     → HTTP POST JSON
    └── SMSNotifier.send_alert()         → GSM module AT commands
```

### Ràng buộc thiết kế

- Zero dependency chính — Telegram với `httpx` (đã có sẵn)
- Một kênh fail không ảnh hưởng kênh khác — mỗi kênh bọc trong try/except riêng
- Cấu hình qua YAML — không hardcode token, chat ID
- Hỗ trợ env var reference — `"${TELEGRAM_BOT_TOKEN}"` trong config
- Tất cả notifier đều là async

---

## 2. Kiến trúc

### Các thành phần

```
notifier/
├── __init__.py      Export: NotifierManager
├── base.py          BaseNotifier — abstract interface cho tất cả channel
├── manager.py       NotifierManager — subscribe event + dispatch
├── console.py       ConsoleNotifier — in ra stderr
├── telegram.py      TelegramNotifier — Telegram Bot HTTP API
├── webhook.py       WebhookNotifier — HTTP POST JSON
└── sms.py           SMSNotifier — GSM module serial AT commands
```

### Luồng dữ liệu chi tiết

```
config/notifiers.yaml
    │
    ▼
NotifierManager.__init__(bus, config_path)
    │
    ├── Đọc YAML, lọc channel enabled = true
    │
    ├── console: ConsoleNotifier(cfg)    ← luôn available
    ├── telegram: TelegramNotifier(cfg)  ← cần bot_token + chat_id
    ├── webhook: WebhookNotifier(cfg)    ← cần url
    └── sms: SMSNotifier(cfg)            ← cần port + to
    │
    └── bus.on("alert_triggered", _on_alert)
    │
    ▼
EventBus emit "alert_triggered"
    │
    ▼
_on_alert(rule_id, severity, message, device_id)
    │
    ├── console.send_alert() → stderr         ← không throw
    ├── telegram.send_alert() → Telegram API  ← log warning nếu fail
    ├── webhook.send_alert() → HTTP POST      ← log warning nếu fail
    └── sms.send_alert() → GSM module         ← log warning nếu fail
```

---

## 3. Các thành phần

### 3.1 base.py — BaseNotifier

```python
class BaseNotifier(ABC):
    async def send_alert(self, rule_id, severity, message, device_id=None, **kwargs)
    async def send_report(self, title, body)
    @property def name(self) -> str
```

**Công dụng:** Interface mà tất cả channel phải implement. `send_alert` cho cảnh báo tức thời, `send_report` cho báo cáo định kỳ.

### 3.2 manager.py — NotifierManager

```python
class NotifierManager:
    def __init__(self, bus: EventBus, config_path="config/notifiers.yaml")
        # Đọc YAML, instantiate enabled channels, subscribe alert_triggered

    async def _on_alert(self, rule_id=None, severity=None, message=None, device_id=None, **kwargs)
        # Dispatch đến tất cả channel, mỗi channel bọc trong try/except

    async def send_report(self, title, body)
        # Gửi báo cáo đến tất cả channel

    @property def channels(self) -> list[str]
        # Danh sách tên channel đang active
```

**Công dụng:** Điểm vào duy nhất. Load config, quản lý vòng đời channel, nhận và dispatch alert.

**Cấu hình YAML:**

```yaml
notifiers:
  console:
    enabled: true          # Luôn bật — không cần config thêm

  telegram:
    enabled: true
    bot_token: "${TELEGRAM_BOT_TOKEN}"   # hoặc token trực tiếp
    chat_id: "${TELEGRAM_CHAT_ID}"

  webhook:
    enabled: false
    url: "https://hooks.example.com/alert"
    headers:
      Authorization: "Bearer secret123"

  sms:
    enabled: false
    port: "/dev/ttyUSB2"
    baud_rate: 115200
    to: "+84901234567"
```

### 3.3 console.py — ConsoleNotifier

```python
class ConsoleNotifier(BaseNotifier):
    # In alert ra stderr với icon màu theo severity
    # CRITICAL → 🔴, WARNING → 🟡, INFO → 🔵
```

**Công dụng:** Luôn bật. In alert ra terminal để farmer thấy ngay khi SSH vào gateway.

### 3.4 telegram.py — TelegramNotifier

```python
class TelegramNotifier(BaseNotifier):
    # Gửi message qua Telegram Bot API (httpx)
    # Hỗ trợ Markdown formatting
    # Hỗ trợ ${ENV_VAR} reference trong config
```

**Công dụng:** Push notification đến điện thoại farmer. Kênh chính cho alert.

**Cơ chế:**
- Dùng `httpx.AsyncClient` với timeout 10s
- Parse mode Markdown cho message format
- Hỗ trợ lấy token/chat_id từ env var qua cú pháp `${VAR}`

### 3.5 webhook.py — WebhookNotifier

```python
class WebhookNotifier(BaseNotifier):
    # POST JSON đến URL cấu hình
    # Hỗ trợ custom headers (Authorization, ...)
```

**Công dụng:** Tích hợp với hệ thống ngoài: Blynk, IFTTT, Home Assistant, web dashboard.

**Payload mẫu:**

```json
{
    "event": "alert_triggered",
    "rule_id": "R01",
    "severity": "WARNING",
    "message": "Temperature high: 38.5°C",
    "device_id": "sensor_01"
}
```

### 3.6 sms.py — SMSNotifier

```python
class SMSNotifier(BaseNotifier):
    # Gửi SMS qua GSM module (SIM800/SIM7600) bằng AT commands
    # Mở kết nối serial, gửi text, đóng
```

**Công dụng:** Kênh dự phòng khi mất internet. GSM module gửi SMS trực tiếp.

**Cơ chế:**
- AT commands qua serial (`AT+CMGF=1`, `AT+CMGS="number"`)
- Giới hạn 120 ký tự mỗi SMS
- Kết nối/disconnect mỗi lần gửi (tiết kiệm pin)
- Yêu cầu `pyserial-asyncio`

---

## 4. So sánh các channel

| Channel | Ưu điểm | Nhược điểm | Phụ thuộc |
|---------|---------|------------|-----------|
| Console | Luôn sẵn sàng, không cấu hình | Chỉ thấy khi SSH | Không |
| Telegram | Push notification, mã hóa, miễn phí | Cần internet | `httpx` (có sẵn) |
| Webhook | Tích hợp mọi hệ thống | Cần server đích | `httpx` (có sẵn) |
| SMS | Hoạt động khi mất internet | Tốn phí, giới hạn ký tự | `pyserial-asyncio` |

---

## 5. Xử lý lỗi

```
_on_alert()
    │
    ├── ConsoleNotifier.send_alert() → ERROR: print to stderr
    │   └── Exception không xảy ra — print là blocking nhưng đơn giản
    │
    ├── TelegramNotifier.send_alert() → ERROR: network timeout
    │   └── Log warning → không ảnh hưởng channel khác
    │
    ├── WebhookNotifier.send_alert() → ERROR: HTTP 500
    │   └── Log warning → không ảnh hưởng channel khác
    │
    └── SMSNotifier.send_alert() → ERROR: GSM module offline
        └── Log warning → không ảnh hưởng channel khác
```

**Nguyên tắc:** Không retry. Alert đã được RuleEngine deduplicate (5 phút cooldown).
Nếu channel fail, alert tiếp theo sau cooldown sẽ được gửi lại.

---

## 6. Mở rộng: Thêm channel mới

```python
from notifier.base import BaseNotifier
from notifier.manager import register_notifier

class DiscordNotifier(BaseNotifier):
    @property
    def name(self) -> str:
        return "discord"

    async def send_alert(self, rule_id, severity, message, device_id=None, **kwargs):
        # POST to Discord webhook
        ...

    async def send_report(self, title, body):
        ...

register_notifier("discord", DiscordNotifier)
```

Sau đó thêm vào `config/notifiers.yaml`:
```yaml
discord:
  enabled: true
  url: "https://discord.com/api/webhooks/..."
```

---

## 7. Giới hạn

- **Không retry** — nếu channel fail, alert bị mất đến lần trigger tiếp theo
- **SMS chỉ 120 ký tự** — message dài bị cắt
- **Telegram không hỗ trợ file/photo** — chỉ text
- **Không có rate limiting riêng** — dựa vào cooldown 5 phút của RuleEngine
- **SMS mở/kết nối mỗi lần gửi** — chậm (~2s mỗi lần)
- **Console không có file log riêng** — chỉ stderr

---

## 8. Ví dụ

### Khởi tạo

```python
from notifier import NotifierManager
from event_bus import EventBus

bus = EventBus()
manager = NotifierManager(bus, "config/notifiers.yaml")
# Đã subscribe alert_triggered
```

### Gửi report định kỳ

```python
# Trong daemon loop, mỗi 24h
await manager.send_report(
    title="Daily Summary",
    body=f"Devices: 5\nActive alerts: 2\nReadings today: 1500",
)
```

### Config đầy đủ

```yaml
notifiers:
  console:
    enabled: true

  telegram:
    enabled: true
    bot_token: "${TELEGRAM_BOT_TOKEN}"
    chat_id: "${TELEGRAM_CHAT_ID}"

  webhook:
    enabled: true
    url: "https://hooks.example.com/alert"
    headers:
      X-Source: "agrimesh"

  sms:
    enabled: false
    port: "/dev/ttyUSB2"
    to: "+84901234567"
```

---

## Tham khảo

- Telegram Bot API — core.telegram.org/bots/api
- httpx — python-httpx.org
- GSM AT commands — 3gpp.org
