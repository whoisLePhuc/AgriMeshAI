# Weather Forecasting — Dự báo thời tiết (Tương lai)

> **Trạng thái:** ❌ Chưa triển khai — tài liệu tham khảo cho Phase sau

---

Dự báo thời tiết local dùng LSTM-TCN từ NASA POWER data sẽ được triển khai sau khi MLDetector v1 ổn định.

## Hiện trạng

| Model | Mục tiêu | RAM | Trạng thái |
|-------|----------|-----|-----------|
| LSTM-TCN | Dự báo nhiệt/ẩm/mưa 48h | ~80 MB | ❌ Chưa làm |
| Fallback | Open-Meteo API | — | Chưa tích hợp |

## Cần chuẩn bị

1. NASA POWER historical data (5-10 năm tại toạ độ ruộng)
2. Training: laptop → ONNX export → Jetson deploy
3. Tích hợp `get_weather_forecast_local()` MCP tool

Tham khảo: `docs/System/system-design.md` (section ML + Gap Analysis)
