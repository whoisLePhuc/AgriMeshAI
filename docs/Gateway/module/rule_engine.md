# Rule Engine — Thiết kế module

> **Module:** `rule_engine/`
> **Phiên bản:** 1.0
> **Ngày:** 17/06/2026

---

## 1. Tổng quan

### Mục đích

`rule_engine` là module phát hiện bất thường theo thời gian thực dựa trên luật. Module này đánh giá mọi sensor reading và kích hoạt cảnh báo nếu vi phạm điều kiện.

- Nhận sự kiện `reading_recorded` từ `EventBus`
- Đánh giá tất cả luật cho sensor tương ứng
- Emit `alert_triggered` nếu luật vi phạm (có deduplication 5 phút)
- Hỗ trợ 3 loại luật: threshold, rate-of-change, stuck sensor
- Chạy `check_missing()` định kỳ — phát hiện thiết bị mất tín hiệu

### Vị trí trong hệ thống

```
EventBus emit "reading_recorded" (từ DatabaseManager)
    │
    ▼
RuleEngine._on_reading(device_id, sensor_id, value, unit)
    │
    ├── Threshold: value > 40°C → alert
    ├── Rate: tăng > 5°C/h → alert
    └── Stuck: giá trị không đổi 6h → alert
    │
    ▼
EventBus emit "alert_triggered"
    │
    ▼
NotifierManager — telegram, console, webhook, sms
```

### Ràng buộc thiết kế

- Tất cả rule đánh giá đồng bộ trong handler `_on_reading()` — không queue riêng
- Alert deduplication — cùng rule + device + sensor không lặp lại trong 5 phút
- Rule config trong YAML — hot-reload qua `reload()`
- 3 loại luật: threshold, rate, stuck
- Timer-based missing data check — gọi từ daemon loop mỗi 5 phút

---

## 2. Kiến trúc

### Các thành phần

```
rule_engine/
├── __init__.py      Export: RuleEngine
└── engine.py        RuleEngine + Rule dataclass
```

### Luồng dữ liệu chi tiết

```
config/rules.yaml
    │
    ▼
RuleEngine.__init__(bus, store, "config/rules.yaml")
    │
    ├── Load rules từ YAML
    └── bus.on("reading_recorded", _on_reading)
    │
    ▼
_on_reading(device_id="s1", sensor_id="temp", value=38.5, unit="celsius")
    │
    ├── Rule R01 (threshold: temp > 35)
    │   └── Triggered → _fire()
    │       ├── Check cooldown (5 phút)
    │       ├── Format message
    │       └── bus.emit("alert_triggered", rule_id="R01", ...)
    │
    ├── Rule R02 (rate: temp thay đổi > 3°C/h)
    │   ├── _check_rate() → query store.get_history()
    │   └── Không trigger → skip
    │
    └── Rule R03 (stuck: humidity không đổi)
        ├── _check_stuck() → query store.get_history()
        └── Không trigger → skip
```

---

## 3. Các thành phần

### 3.1 engine.py — Rule

```python
class Rule:
    id: str                 # "R01"
    name: str               # "Temperature too high"
    type: str               # "threshold" | "rate" | "stuck"
    sensor_type: str        # "temp" | "humidity" | "*" (tất cả)
    operator: str | None    # ">" | "<" | ">=" | "<=" | "=="
    value: float | None     # ngưỡng so sánh
    severity: str           # "INFO" | "WARNING" | "CRITICAL"
    message: str            # template message {device_id} {sensor_id} {value} {rate}
    window_minutes: int     # lookback window cho rate check
    hours: int              # lookback window cho stuck check
```

### 3.2 engine.py — RuleEngine

```python
class RuleEngine:
    def __init__(self, bus: EventBus, store: ReadingStore, rules_path="config/rules.yaml")
        # Load rules, subscribe reading_recorded

    # Event handler
    async def _on_reading(self, device_id=None, sensor_id=None, value=None, unit=None, **kwargs)
        # Evaluate tất cả rules cho sensor này

    # Rule evaluation
    async def _evaluate(self, rule, device_id, sensor_id, value) -> tuple[bool, dict]
        # Dispatch theo rule.type

    @staticmethod
    def _check_threshold(rule, value) -> bool
        # So sánh value với ngưỡng

    async def _check_rate(self, rule, device_id, sensor_id, value) -> tuple[bool, float]
        # Tính tốc độ thay đổi theo thời gian

    async def _check_stuck(self, rule, device_id, sensor_id) -> bool
        # Kiểm tra sensor bị kẹt

    # Alert
    async def _fire(self, rule, device_id, sensor_id, value, extra=None)
        # Emit alert_triggered nếu không trong cooldown

    # Timer-based
    async def check_missing(self, hours=1.0)
        # Kiểm tra thiết bị không gửi dữ liệu trong N giờ

    # Utilities
    @property def rules(self) -> list[Rule]
    def reload(self, rules_path="config/rules.yaml")
```

**Công dụng:** Điểm vào duy nhất. Subscribe event, đánh giá luật, emit alert.

---

## 4. Các loại luật

### 4.1 Threshold — vượt ngưỡng

```yaml
rules:
  - id: R01
    name: "Temperature too high"
    type: threshold
    sensor_type: temp
    operator: ">"
    value: 40
    severity: WARNING
    message: "{device_id}: {sensor_id} = {value:.1f}°C (threshold > 40)"
```

**Operator hỗ trợ:** `>`, `<`, `>=`, `<=`, `==`

### 4.2 Rate of change — tốc độ thay đổi

```yaml
rules:
  - id: R03
    name: "Rapid temperature rise"
    type: rate
    sensor_type: temp
    value: 5           # °C per hour
    severity: WARNING
    window_minutes: 60  # lookback window
    message: "{device_id}: temperature rising {rate:.1f}°C/h"
```

**Cơ chế:**
- Query history trong `window_minutes` từ store
- So sánh giá trị đầu và cuối window
- Nếu |rate| > threshold → trigger

### 4.3 Stuck sensor — cảm biến kẹt

```yaml
rules:
  - id: R05
    name: "Soil moisture stuck"
    type: stuck
    sensor_type: moisture
    severity: WARNING
    hours: 6           # lookback window
    message: "{device_id}: {sensor_id} stuck at {value}"
```

**Cơ chế:**
- Query history trong `hours` giờ
- Nếu tất cả giá trị đều giống nhau (≤ 1 unique value) → trigger
- Yêu cầu tối thiểu 5 readings trong window

### 4.4 Missing data — mất tín hiệu

```yaml
rules:
  - id: R09
    name: "Missing data"
    type: (timer-based, không config trong YAML)
    severity: WARNING
    message: "{device_id}: no data for N hours"
```

**Cơ chế:** Timer-based, gọi `check_missing(hours=1.0)` từ daemon loop mỗi 5 phút.
Dùng `store.get_all_latest()` — nếu latest reading quá cũ → trigger.

---

## 5. Alert deduplication

```
lần 1: value = 42 → fire R01 → cooldown key "R01:s1:temp" = now
lần 2: value = 43 (sau 1 phút) → không fire (trong cooldown 5 phút)
lần 3: value = 44 (sau 6 phút) → fire → reset cooldown
```

**Cơ chế:**
- Key: `{rule_id}:{device_id}:{sensor_id}`
- Cooldown: 300 giây (5 phút)
- Lưu trong memory dict (`_cooldowns`) — mất khi restart

**Tại sao 5 phút?**
- Tránh spam khi sensor dao động quanh ngưỡng
- Đủ ngắn để farmer không bỏ lỡ alert kéo dài
- Đủ dài để Telegram/webhook không bị rate limit

---

## 6. Cấu hình rules.yaml

```yaml
rules:
  # ── Nhiệt độ ──
  - id: R01
    name: "Temperature too high"
    type: threshold
    sensor_type: temp
    operator: ">"
    value: 40
    severity: WARNING
    message: "{device_id}: temperature {value:.1f}°C exceeds 40°C"

  - id: R02
    name: "Temperature too low"
    type: threshold
    sensor_type: temp
    operator: "<"
    value: 5
    severity: WARNING
    message: "{device_id}: temperature {value:.1f}°C below 5°C"

  - id: R03
    name: "Rapid temperature rise"
    type: rate
    sensor_type: temp
    value: 5
    severity: WARNING
    window_minutes: 60
    message: "{device_id}: temperature rising {rate:.1f}°C/h"

  # ── Độ ẩm ──
  - id: R04
    name: "Humidity too high"
    type: threshold
    sensor_type: humidity
    operator: ">"
    value: 90
    severity: WARNING
    message: "{device_id}: humidity {value:.0f}% > 90%"

  # ── Độ ẩm đất ──
  - id: R05
    name: "Soil moisture stuck"
    type: stuck
    sensor_type: moisture
    hours: 6
    severity: WARNING
    message: "{device_id}: moisture stuck at {value}"

  # ── Pin ──
  - id: R06
    name: "Battery low"
    type: threshold
    sensor_type: battery
    operator: "<"
    value: 20
    severity: CRITICAL
    message: "{device_id}: battery {value:.0f}% critically low"

  # ── Board ──
  - id: R09
    name: "Missing data"
    type: (timer-based)
    severity: WARNING
    message: "{device_id}: no data for {hours}h — device may be offline"
```

---

## 7. Xử lý lỗi

```
_on_reading()
    │
    ├── _check_threshold() → FAIL (lỗi toán học)
    │   └── Exception → log error → return False — không crash
    │
    ├── _check_rate() → FAIL (store query timeout)
    │   └── Exception → log error → return False — không crash
    │
    └── _check_stuck() → FAIL (history empty)
        └── return False — an toàn
```

**Nguyên tắc:** Không rule nào được làm crash engine. Mọi exception đều được catch trong `_evaluate()`.

---

## 8. So sánh loại luật

| Loại | Kích hoạt | Dữ liệu cần | Query store? |
|------|-----------|-------------|--------------|
| Threshold | Event: `reading_recorded` | Chỉ giá trị hiện tại | Không |
| Rate | Event: `reading_recorded` | History N phút | Có |
| Stuck | Event: `reading_recorded` | History N giờ | Có |
| Missing | Timer: mỗi 5 phút | Latest reading | Có |

---

## 9. Giới hạn

- **Chỉ 3 loại luật cơ bản** — không có ML-based, không có forecast
- **Cooldown in-memory** — mất khi gateway restart, alert có thể lặp lại
- **Rate check đơn giản** — chỉ so sánh 2 điểm đầu/cuối, không regression
- **Stuck check đơn giản** — chỉ check unique values, không check variance
- **Không có rule ưu tiên** — tất cả rule chạy tuần tự, không có priority
- **Hot-reload không ảnh hưởng subscription** — rule mới có hiệu lực ngay, nhưng không cần re-subscribe

---

## 10. Ví dụ

### Khởi tạo

```python
from rule_engine import RuleEngine
from event_bus import EventBus
from database_manager.store import ReadingStore

bus = EventBus()
store = ReadingStore("data/agrimesh.db")
await store.init()

engine = RuleEngine(bus, store, "config/rules.yaml")
# Đã subscribe reading_recorded
```

### Kiểm tra missing data (từ daemon loop)

```python
# Trong mcp_server.run_daemon_loops(), mỗi 5 phút
await system.rule_engine.check_missing(hours=1.0)
```

### Reload rules

```python
# Sau khi sửa rules.yaml
engine.reload()
print(f"Loaded {len(engine.rules)} rules")
```

### Xem danh sách rules

```python
for rule in engine.rules:
    print(f"{rule.id}: {rule.name} [{rule.type}]")
# R01: Temperature too high [threshold]
# R02: Temperature too low [threshold]
# R03: Rapid temperature rise [rate]
# R04: Humidity too high [threshold]
# R05: Soil moisture stuck [stuck]
# R06: Battery low [threshold]
```

---

## Tham khảo

- EventBus — doc/module/event_bus.md
- DatabaseManager — doc/module/database_manager.md
- Notifier — doc/module/notifier.md
