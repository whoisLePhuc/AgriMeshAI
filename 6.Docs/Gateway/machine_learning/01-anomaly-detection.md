# Statistical Anomaly Detection — MLDetector v2

> **Trạng thái:** ✅ Đã triển khai
> **Module:** `ml_detector/` trong Gateway
> **Branch:** `develop`

---

## Vai trò trong hệ thống

Hệ thống có 2 tầng phát hiện bất thường đã triển khai:

```
Tầng 1 — RuleEngine (ngưỡng tuyệt đối)    R01-R08  ✅ config/rules.yaml
  Threshold cứng: temp > 40°C → CRITICAL, humidity < 30% → WARNING

Tầng 2 — MLDetector (thống kê động)        M01-M03  ✅ ml_detector/
  Baseline động, phát hiện deviation, stuck sensor

Tầng 3 — ML thực sự (tương lai)                       ❌ Chưa triển khai
  Isolation Forest, LightGBM, LSTM-TCN
```

**Lưu ý:** Các detector hiện tại là **statistical algorithms**, không phải ML.
Chạy online trên Gateway, không cần training, không có model file.

---

## Các Detector

### M01 — MovingAverageDetector

**File:** `ml_detector/detectors/moving_average.py`

Phát hiện độ lệch so với rolling baseline. Duy trì sliding window
per (node_id, sensor_id), tính mean và stddev, flag khi giá trị mới
vượt quá `threshold_sigma` lần stddev.

```
Ví dụ: Nhiệt độ ổn định ~30°C trong 200 readings
       → mean ≈ 30.0, stddev ≈ 0.5
       → Giá trị 35.0 → sigma = |35-30|/0.5 = 10.0 > 3.0 → ALERT
```

**Parameters:**

| Param | Default | Range | Ý nghĩa |
|-------|---------|-------|---------|
| `window_size` | 200 | 10–10000 | Số readings trong sliding window |
| `threshold_sigma` | 3.0 | 1.0–5.0 | Ngưỡng σ — thấp = nhạy hơn |
| `min_samples` | 10 | 3–100 | Số readings tối thiểu trước khi detect |

### M02 — RateOfChangeDetector

**File:** `ml_detector/detectors/rate_of_change.py`

Phát hiện thay đổi đột ngột qua linear regression slope.
Tính độ dốc (units/hour) trên sliding window, flag khi |slope| > max_rate.

```
Ví dụ: Nhiệt độ tăng từ 30→40°C trong 30 phút
       → slope = 20°C/h > max_rate = 5°C/h → ALERT
```

**Parameters:**

| Param | Default | Range | Ý nghĩa |
|-------|---------|-------|---------|
| `window_minutes` | 60 | 5–1440 | Cửa sổ thời gian (phút) |
| `max_rate` | 5.0 | 0.1–50 | Ngưỡng tốc độ thay đổi (units/h) |
| `min_samples` | 5 | 3–100 | Số điểm tối thiểu cho regression |

### M03 — StuckSensorDetector

**File:** `ml_detector/detectors/stuck_sensor.py`

Phát hiện cảm biến bị kẹt (giá trị không đổi trong thời gian dài).
Tính variance trên window, flag khi variance ≈ 0 trong >2 giờ.

```
Ví dụ: Cảm biến độ ẩm báo 65% liên tục trong 6 giờ
       → variance ≈ 0, stuck > 2h → ALERT
```

**Parameters:**

| Param | Default | Range | Ý nghĩa |
|-------|---------|-------|---------|
| `window_hours` | 6.0 | 1–48 | Cửa sổ thời gian (giờ) |
| `threshold_var` | 0.005 | 0.0001–1.0 | Ngưỡng variance để coi là stuck |
| `min_samples` | 10 | 3–1000 | Số điểm tối thiểu |
| `cooldown_s` | 1800 | 0–86400 | Thời gian chờ giữa các alert (giây, mặc định 30 phút) |

---

## Runtime Configuration

Tất cả parameters có thể thay đổi tại runtime qua EventBus `config_updated`:

```python
await event_bus.emit("config_updated", detector_name="moving_average",
                      params={"threshold_sigma": 2.5})
```

- **Enable/disable:** `detector.disable("stuck_sensor")` / `detector.enable("stuck_sensor")`
- **Health:** `detector.get_health()` → list[DetectorHealth] với name, status, alert_count
- **Không cần restart** gateway

---

## Enrichment Pipeline

Khi detector phát hiện bất thường, `EnrichmentPipeline` tự động:

1. Query SQLite 24h gần nhất cho sensor đó
2. Gắn historical context vào alert
3. Gọi Ollama (Qwen2.5 7B) để giải thích bằng tiếng Việt
4. Nếu Ollama offline → retry 3 lần (30s, 2min, 5min)

Alert **không bao giờ** bị chờ enrichment.

**File:** `ml_detector/enrichment.py`

---

## Event Flow

```
Sensor Reading
    │
    ▼
DatabaseManager ghi SQLite
    │
    ▼
EventBus.emit("reading_recorded", device_id, sensor_id, value)
    │
    ├──► RuleEngine (R01-R08)
    │
    └──► MLDetector._on_reading()
            │
            ├── MovingAverageDetector.on_reading()
            ├── RateOfChangeDetector.on_reading()
            └── StuckSensorDetector.on_reading()
                │
                ▼ (nếu phát hiện bất thường)
            EventBus.emit("alert_triggered", ...)
                │
                ├──► NotifierManager → Console / Telegram / Webhook / SMS
                │
                └──► EnrichmentPipeline.enqueue()
                        └── 24h context + LLM (best-effort)
```

---

## So sánh với RuleEngine

| Tiêu chí | RuleEngine (R01-R08) | MLDetector (M01-M03) |
|----------|---------------------|---------------------|
| Loại | Threshold tuyệt đối | Statistical động |
| Phát hiện | Nhiệt > 40°C | Tăng 5°C/h bất thường |
| Baseline | Cố định (config) | Tự động (sliding window) |
| Complexity | O(1) | O(window_size) |
| Dependencies | Không | Không (thuần Python) |
| Config | YAML file, cần restart | Runtime qua EventBus |

---

## Test Coverage

- Unit tests: `tests/test_moving_average.py`, `tests/test_rate_of_change.py`,
  `tests/test_stuck_sensor.py`, `tests/test_ml_detector.py`
- Integration tests: `tests/integration/test_e2e_detection.py`
- 44 unit + 11 integration tests — tất cả pass
