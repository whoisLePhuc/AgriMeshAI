# Anomaly Detection — MLDetector v1

> **Trạng thái:** ✅ Đã triển khai trên `feature/machine_learning`
> **Module:** `ml_detector/`
> **Branch:** `feature/machine_learning`

---

## Vai trò trong hệ thống

Hệ thống có 3 tầng phát hiện bất thường:

```
Tầng 1 — RuleEngine (cứng)        R01-R09  ✅ config/rules.yaml
  Threshold tuyệt đối: temp > 40°C → CRITICAL

Tầng 2 — MLDetector v1 (thống kê)  M01-M03  ✅ ml_detector/
  Baseline động, phát hiện deviation, stuck sensor

Tầng 3 — Phân tích sâu (tương lai)            ❌ Chưa triển khai
  Isolation Forest, LightGBM, Cross-correlation
```

## Kiến trúc module

```
EventBus("reading_recorded")
    │
    ▼
MLDetector.on_reading(device_id, sensor_id, value)
    │
    ├──► M01: MovingAverageDetector
    │       rolling window ±3σ → phát hiện deviation từ baseline
    │
    ├──► M02: RateOfChangeDetector  
    │       linear regression slope → phát hiện thay đổi đột ngột
    │
    └──► M03: StuckSensorDetector
            variance ≈ 0 → phát hiện cảm biến kẹt/hỏng
    │
    ▼
EventBus("alert_triggered") ──► Notifier (giống RuleEngine)
```

Cả 3 detector đều là **online** — chạy trên từng reading, tự duy trì state buffer, không cần training.

---

## M01: Moving Average ±3σ

### Cách hoạt động

Duy trì sliding window N giá trị gần nhất per (node_id, sensor_id).
Khi có reading mới:

1. Push vào buffer (size cấu hình, mặc định 200)
2. Tính **mean** và **stddev** của window
3. Nếu `|value - mean| / stddev > threshold_sigma` (mặc định 3.0) → anomaly

### Cấu hình

```python
MovingAverageDetector({
    "window_size": 200,      # số readings trong buffer
    "threshold_sigma": 3.0,  # độ lệch chuẩn tối đa
    "min_samples": 10,       # buffer tối thiểu trước khi check
})
```

### Ví dụ

```
Baseline: 25°C ± 0.3°C (200 readings)
Reading mới: 55°C
Sigma = |55-25| / 0.3 = 85σ >> 3.0 → M01 WARNING
```

### Edge cases

| Case | Xử lý |
|------|-------|
| stddev = 0 (all same values) | Bỏ qua — không thể tính sigma |
| Chưa đủ min_samples | Bỏ qua |
| Cooldown 5 phút | Ngăn trùng lặp alert trên cùng (node, sensor) |

---

## M02: Rate of Change

### Cách hoạt động

Duy trì sliding window theo thời gian (timestamp). Khi có reading mới:

1. Prune các entry cũ hơn `window_minutes`
2. Push (timestamp, value)
3. Fit **linear regression** (mean-normalized để tránh catastrophic cancellation)
4. Nếu `|slope_per_hour| > max_rate` → anomaly

### Cấu hình

```python
RateOfChangeDetector({
    "window_minutes": 60,   # lookback window
    "max_rate": 5.0,        # max change per hour (units/h)
    "min_samples": 5,       # minimum points for regression
})
```

### Numerical stability

Timestamps là unix epoch (~1.7e9), `t²` ≈ 3e18. Regression với unix timestamps raw gây **catastrophic cancellation** (mất precision khi trừ 2 số rất lớn). Fix: chuẩn hóa bằng cách subtract mean(t) trước khi tính.

```
Denom = Σ(t-mean_t)²   # ← ổn định, không catastrophic cancellation
Num   = Σ(t-mean_t)*(v-mean_v)
Slope = Num / Denom
```

---

## M03: Stuck Sensor

### Cách hoạt động

Phát hiện sensor bị kẹt (giá trị không đổi trong thời gian dài).

1. Duy trì buffer (timestamp, value) per (node, sensor)
2. Nếu variance của toàn bộ buffer < `threshold_var` → stuck
3. **Alert sau 2 tiếng** stuck liên tục (tránh false positive)
4. Khi giá trị thay đổi → tự động unstuck

### Cấu hình

```python
StuckSensorDetector({
    "window_hours": 6.0,        # lookback window
    "threshold_var": 0.005,     # max variance to consider stuck
    "min_samples": 10,          # minimum points before checking
})
```

### Ví dụ

```
Sensor đọc 25.0°C liên tục trong 150 phút
→ variance ≈ 0 < 0.005 → stuck
→ stuck_hours = 150/60 = 2.5h ≥ 2h → M03 WARNING
"Sensor 1 temperature: stuck for 2.5h (variance=0.0000)"
```

---

## Tích hợp

### Vào SystemManager

```python
# system/manager.py
from ml_detector import MLDetector

class SystemManager:
    def __init__(self, config):
        ...
        self.ml_detector = MLDetector(
            event_bus=self.event_bus,
            config={
                "MovingAverageDetector": {"threshold_sigma": 3.0},
                "RateOfChangeDetector": {"max_rate": 5.0},
                "StuckSensorDetector": {"window_hours": 6.0},
            },
        )

    async def start(self):
        ...
        self.ml_detector.start()

    async def stop(self):
        self.ml_detector.stop()
```

### Với Notifier

Module `ml_detector` emit `alert_triggered` y hệt RuleEngine — Notifier xử lý như nhau:
- `rule_id` prefix `M` (M01, M02, M03) phân biệt với Rule `R` (R01-R09)
- Không cần sửa Notifier

---

## Cấu trúc file

```
ml_detector/
├── __init__.py                 # Export MLDetector
├── detector.py                 # Orchestrator: subscribe → dispatch → emit
└── detectors/
    ├── __init__.py
    ├── base.py                 # BaseDetector ABC + AlertData + cooldown
    ├── moving_average.py       # M01: ±3σ adaptive baseline
    ├── rate_of_change.py       # M02: linear regression slope
    └── stuck_sensor.py         # M03: zero variance stuck detection
```

---

## Tổng hợp

| Rule | Detector | Input | Output | Training |
|------|----------|-------|--------|----------|
| M01 | MovingAverage ±3σ | 1 reading | σ deviation từ baseline | ❌ Online |
| M02 | RateOfChange | N readings | slope (units/h) | ❌ Online |
| M03 | StuckSensor | N readings | variance ≈ 0 | ❌ Online |

**Đặc điểm chung:**
- Online — không cần training, không cần dữ liệu lịch sử
- State per (node_id, sensor_id) — buffer riêng cho từng sensor
- Cooldown — alert suppression 5-30 phút (configurable per detector)
- Alert format — `alert_triggered` event tương thích Notifier
