# Predictive — Dự đoán sensor

## Vai trò trong hệ thống

Chuyển từ **phản ứng → chủ động**. Thay vì chờ đến khi độ ẩm xuống ngưỡng mới tưới, hệ thống dự đoán trước và hành động sớm.

## Input / Output

| | Mô tả |
|---|---|
| **Input** | Time-series sensor 7-30 ngày + thời gian trong ngày |
| **Output** | Giá trị dự đoán (độ ẩm, nhiệt độ...) tại mốc thời gian |
| **Tần suất** | Mỗi 5-15 phút (hoặc khi AI Agent yêu cầu) |
| **Nơi chạy** | Edge gateway |

## Các dự đoán

### Dự đoán độ ẩm đất

**Mục tiêu:** Biết trước 6-24h để quyết định tưới chủ động.

```
Input (7 ngày):                   Output (24h):
  - Độ ẩm đất (đo thực tế)          - Độ ẩm dự đoán theo giờ
  - Nhiệt độ không khí
  - Lượng mưa (dự báo hoặc NASA)
  - Giờ trong ngày

Ví dụ:
  Hiện tại: 35%
  Dự báo 12h tới: 22% → dưới ngưỡng 25%
  → "Tưới ngay bây giờ, trước khi cây bị stress"
```

### Dự đoán nhiệt độ

**Mục tiêu:** Cảnh báo sớm quá nhiệt.

| Model | RAM | Dự đoán | Độ chính xác |
|-------|-----|---------|-------------|
| Linear Regression | ~5 MB | 2h tới | ±1°C |
| LightGBM | ~50 MB | 6h tới | ±1.5°C |

### Dự đoán pin

**Mục tiêu:** Biết khi nào node sắp hết pin để thay.

```
Input (30 ngày):
  - Pin % mỗi lần gửi
  - Số lần gửi/ngày
  - Nhiệt độ (pin yếu hơn khi lạnh)

Output: "Node soil_03 còn 12 ngày pin"
```

## Tích hợp với MCP

```python
@mcp.tool
def predict_soil_moisture(node_id: str, hours_ahead: int = 24) -> dict:
    """
    Dự đoán độ ẩm đất X giờ tới.
    Dùng LightGBM đã train với lịch sử sensor.
    AI Agent gọi để quyết định tưới.
    """
    features = build_features(node_id)
    prediction = model.predict(features)
    return {"hours": hours_ahead, "values": prediction}
```

## Kịch bản

```
Người dùng: "Hôm nay có cần tưới không?"

AI Agent:
  ├── MCP: predict_soil_moisture(node=soil_01, 24h)
  │   └── ML: dự đoán độ ẩm 24h tới
  │       → 18h tới sẽ xuống 22% (dưới ngưỡng 25%)
  │
  └── "Hiện tại 35%. Dự báo 6h tối nay sẽ xuống 22%.
       Khuyến nghị tưới 10 phút ngay bây giờ."
```

```
Rule engine (không ML):
  → 22:00 độ ẩm = 25% → tưới (giữa đêm, phản ứng)

Với ML:
  → 06:00 dự báo 22:00 sẽ xuống 22% → tưới lúc sáng
    (chủ động, đúng lúc, tiết kiệm điện)
```

## Files liên quan trong repo

| File | Mô tả |
|------|-------|
| `gateway/src/jeltz/storage/store.py` | SQLite lưu lịch sử |
| `ai-agent/scripts/predictive.py` | ML model inference |
| `ai-agent/models/lightgbm_soil.pkl` | Trained model |

---

## Nguồn tham khảo

### Edge Deployment + MLOps

| Nguồn | Mô tả |
|-------|-------|
| **[SAIFULLAH-SHARAFAT/iot-crop-prediction](https://github.com/SAIFULLAH-SHARAFAT/An-IoT-Enabled-AI-System-for-Real-Time-Crop-Prediction-Using-Soil-and-Weather-Data)** | **RPi 5** + 7-in-1 RS485 sensor. Random Forest 95.8% accuracy, 60.8ms inference. TensorFlow Lite. |
| **[Harshavardhan200/Smart-Farming-AI-System](https://github.com/Harshavardhan200/Smart-Farming-AI-System)** | **RPi 5 edge + MLOps**: CircleCI nightly retrain + rollback. SVM irrigation, FLAN-T5 offline LLM. |
| **[pronzzz/pest-prediction-detection](https://github.com/pronzzz/pest-prediction-detection)** | **RPi edge + SQLite + MQTT + FastAPI**. 1D CNN, offline-first. |
| **[ETCE-LAB/FarmInsight ⭐3](https://github.com/ETCE-LAB/FarmInsight)** | Farm platform: AI forecasting (water, energy), InfluxDB, SQLite, MQTT. **Production-grade.** |

### LightGBM trên Edge

| Nguồn | Mô tả |
|-------|-------|
| **[ETASR 2024 — LightGBM Optimization on RPi](https://etasr.com/index.php/ETASR/article/view/16433)** | Pruning + ONNX INT8 + Treelite. **40.66× speedup**, 96.98% accuracy. |
| **[ONNX Runtime trên Jetson (NVIDIA)](https://developer.nvidia.com/blog/stream-announcing-onnx-runtime-for-jetson/)** | CUDA/cuDNN acceleration, Docker deployment. |
| **[LightGBM on ARM](https://github.com/microsoft/LightGBM/issues/3456)** | Compile từ source trên ARM (RPi). |

### Battery Life Prediction cho IoT

| Nguồn | Mô tả |
|-------|-------|
| **[MetaStackD (Nature 2025)](https://www.nature.com/articles/s41598-025-97720-x)** | LightGBM + XGBoost + RF ensemble. **95.23% size reduction.** Model 5.3 MB. |
| **[TensorRT LSTM trên Jetson (MDPI 2024)](https://www.mdpi.com/1996-1073/17/12/2797)** | LSTM + TensorRT INT8 trên Jetson Xavier NX. **~50× speedup, 2.87ms inference.** |
| **[RF + PCA Battery (IET 2020)](https://ietresearch.onlinelibrary.wiley.com/doi/10.1049/iet-its.2020.0009)** | Random Forest regression, 97% accuracy. |

### Academic Papers bổ sung

| Paper | Nội dung |
|-------|----------|
| **Frontiers in Soil Science 2025** — [link](https://www.frontiersin.org/journals/soil-science/articles/10.3389/fsoil.2025.1612908/full) | XGBoost, LSTM, RF cho soil moisture ở 5-80cm. RMSE 0.24-0.9%. IoT sensors (LoRaWAN, NB-IoT). |
| **MDPI Information 2024 — ML for Smart Irrigation** — [link](https://www.mdpi.com/2078-2489/15/6/306) | Comprehensive survey: RF, LSTM, TFT. Dataset discussion. |
| **MDPI Algorithms 2025 — Satellite + ML Irrigation** — [link](https://www.mdpi.com/1999-4893/18/12/740) | RF (R²>0.92). Satellite + weather + GIS. |

### Soil Moisture Prediction (Dự đoán độ ẩm đất)

| Nguồn | Mô tả | Liên quan |
|-------|-------|-----------|
| **[dineshraju147/soil-moisture-prediction-using-machine-learning](https://github.com/dineshraju147/soil-moisture-prediction-using-machine-learning)** | Dự đoán độ ẩm đất bằng Voting Regressor (Decision Tree + Random Forest + XGBoost). R² > 0.99. Mô phỏng dynamic moisture theo distance. | ⭐ **Reference chính**: Ensemble model cho độ ẩm, irrigation planning |
| **[nithu0035/smart-irrigation-system-with-weather-aware-crop-guidance](https://github.com/nithu0035/smart-irrigation-system-with-weather-aware-crop-guidance)** | Ensemble ML (XGBoost, LightGBM, RF, Gradient Boosting) cho irrigation decision. 20 sensors, 11 states India. FastAPI backend. ~78% accuracy. | ⭐ Kiến trúc tương tự: ML ensemble + weather API + FastAPI. **Có LightGBM.** |
| **[Sly231/SoilWeatherPredictor](https://github.com/Sly231/SoilWeatherPredictor)** | LSTM neural network dự đoán độ ẩm đất từ weather data (nhiệt độ, lượng mưa). Dùng Meteomatics API. SHAP explainability. | Mô hình LSTM cho soil moisture + SHAP giải thích kết quả |
| **[google-research/soil_moisture_retrieval](https://github.com/google-research/google-research/tree/master/soil_moisture_retrieval)** | Deep learning fusion model: Sentinel-1/2 + SoilGrids + SMAP + GLDAS. TensorFlow. ubRMSE: 0.055. | **State-of-the-art** từ Google Research. ubRMSE 0.055 — tham khảo feature fusion. |
| **[ingfercho03/soil_ETo_forecast_module](https://github.com/ingfercho03/soil_ETo_forecast_module)** | LightGBM + Random Forest dự đoán độ ẩm đất và evapotranspiration từ SMAP + ERA5-Land. Tích hợp ThingsBoard. | ⭐ **LightGBM cho soil moisture**, dùng satellite data thay vì IoT sensor |
| **[Harshavardhan200/Smart-Farming-AI-System](https://github.com/Harshavardhan200/Smart-Farming-AI-System)** | Smart farming trên Raspberry Pi 5: SVM irrigation prediction + automated MLOps (nightly retrain + rollback). Có CircleCI pipeline. | ⭐ **Edge deployment + MLOps** — tham khảo CI/CD pipeline cho ML trên gateway |

### Academic Papers

| Paper | Nội dung |
|-------|----------|
| **[Wang et al., 2023 — XGBoost for Soil Moisture Prediction (MDPI)](https://www.mdpi.com/2077-0472/13/5/927)** | XGBoost dự đoán độ ẩm đất tại Jiangsu, China. 70 trạm, 14 predictors. **R=0.69, RMSE=11.11, ACC=88%.** So sánh với ANN, RF, SVM — XGBoost tốt nhất. |
| **[Polepaka et al., 2023 — Soil Moisture Predictive Analysis using IoT and ML (E3S)](https://www.e3s-conferences.org/articles/e3sconf/pdf/2023/28/e3sconf_icmed-icmpc2023_01147.pdf)** | IoT + ML cho soil moisture. ESP32 + sensors. Phân tích các phương pháp đo độ ẩm. |
| **[Escuela Colombiana de Ingeniería — LightGBM for SM Forecasting (2024)](https://repositorio.escuelaing.edu.co/bitstreams/ae2750a1-3560-41c2-95da-8ce5b891aebf/download)** | LightGBM dự đoán soil moisture + ETo 3 ngày từ SMAP và ERA5-Land. Tích hợp ThingsBoard. Lags=30. |
