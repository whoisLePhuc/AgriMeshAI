# Predictive Analytics — Dự đoán (Tương lai)

> **Trạng thái:** ❌ Chưa triển khai — tài liệu tham khảo cho Phase sau

---

Dự đoán sensor (độ ẩm đất, nhiệt độ, pin) và dự báo thời tiết local sẽ được triển khai sau khi MLDetector v1 (M01-M03) ổn định trên production.

## Hiện trạng

| Model | Mục tiêu | RAM | Trạng thái |
|-------|----------|-----|-----------|
| LightGBM | Dự đoán độ ẩm đất 6-24h | ~50 MB | ❌ Chưa làm |
| Linear Regression | Dự đoán pin (thời gian còn lại) | ~5 MB | ❌ Chưa làm |
| LSTM-TCN | Dự báo thời tiết local | ~80 MB | ❌ Chưa làm |

## Cần chuẩn bị

1. **30+ ngày dữ liệu** sensor để train model
2. **Thống kê baseline** từ MLDetector v1 trước khi tích hợp predictive
3. **Training script** chạy trên PC, onnx export deploy xuống Jetson

Tham khảo: `docs/System/system-design.md` (section ML + Gap Analysis)
