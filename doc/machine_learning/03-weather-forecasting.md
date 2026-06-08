# Weather Forecasting — Dự đoán thời tiết

## Vai trò trong hệ thống

Cung cấp dự báo thời tiết **cục bộ tại ruộng** mà không cần Internet. Dùng LSTM-TCN hybrid trained trên dữ liệu NASA POWER (1981-nay, toàn cầu, miễn phí).

## Input / Output

| | Mô tả |
|---|---|
| **Input** | 30 ngày gần nhất (nhiệt độ, độ ẩm, mưa, áp suất, bức xạ) |
| **Output** | Dự báo 24-48h tới (nhiệt độ, độ ẩm, xác suất mưa) |
| **Tần suất** | Mỗi 15 phút (hoặc khi AI Agent yêu cầu) |
| **Nơi chạy** | Edge gateway (Jetson Nano 4GB — GPU CUDA) |
| **Fallback** | Open-Meteo API khi có Internet |

## So sánh với API

| Tiêu chí | Open-Meteo API | LSTM-TCN local |
|----------|---------------|----------------|
| Cần Internet | ✅ Có | ❌ **Không** |
| Độ phân giải | ~11 km grid | ✅ **Chính xác tại ruộng** |
| Dự báo vi khí hậu | ❌ Không | ✅ Có (dưới tán, gần ao) |
| Chi phí | Miễn phí (giới hạn) | ✅ **0đ** |
| Cập nhật | 1 giờ/lần | ✅ **Mỗi 15 phút** |

## Pipeline dữ liệu

```
NASA POWER API (miễn phí)
  ├── 1981-01-01 đến nay
  ├── Toàn cầu, grid 0.5°
  ├── Daily: temp, humidity, rain, pressure, solar
  └── Không cần API key

                        ▼
              ┌────────────────────┐
              │ Download data cho  │
              │ toạ độ ruộng       │
              │ (12.5°N, 108.0°E)  │
              └─────────┬──────────┘
                        ▼
              ┌────────────────────┐
              │ Train LSTM-TCN     │
              │ trên laptop        │
              │ 5-10 năm data      │
              └─────────┬──────────┘
                        ▼
              ┌────────────────────┐
              │ Export ONNX        │
              │ Quantize → ~8 MB   │
              └─────────┬──────────┘
                        ▼
              ┌────────────────────┐
              │ Deploy xuống        │
              │ gateway            │
              │ (ONNX Runtime)     │
              └─────────┬──────────┘
                        │
          ┌─────────────┴─────────────┐
          │                           │
          ▼                           ▼
  ┌─────────────────┐    ┌──────────────────────┐
  │ Mới deploy      │    │ Có sensor data > 3   │
  │ Chỉ dùng NASA   │    │ tháng                 │
  │ Dự báo ±3°C     │    │ Fine-tune với data    │
  │                 │    │ thực tế tại ruộng      │
  │                 │    │ Dự báo ±1°C           │
  └─────────────────┘    └──────────────────────┘
```

## Model

```python
class LSTMTCN(nn.Module):
    """Input: 30 ngày × 5 features
       Output: 24h tới (temp, humidity, rain_prob)"""
    def __init__(self):
        self.lstm = nn.LSTM(5, 64, 2, batch_first=True)
        self.conv = nn.Conv1d(64, 64, kernel_size=3, padding=1)
        self.fc = nn.Linear(64, 3)
```

**Thông số:**
- Kích thước: ~8 MB (ONNX quantized)
- RAM inference: ~50-80 MB
- CPU: ~5% (Jetson Nano) mỗi 15 phút. GPU CUDA giúp tăng tốc ONNX inference.

## Tích hợp với MCP

```python
@mcp.tool
def get_weather_forecast_local(hours: int = 48) -> dict:
    """
    Dự báo thời tiết tại ruộng.
    ưu tiên model local, fallback Open-Meteo API.
    """
    if model_available:
        features = build_features_from_sqlite(days=30)
        pred = lstm_tcn.predict(features)
        return {
            "temp": pred[0], "humidity": pred[1],
            "rain_prob": pred[2], "source": "local_ml"
        }
    else:
        return get_weather_forecast_from_api(...)
```

## Kịch bản

```
Người dùng: "Mai có mưa không?"

AI Agent:
  ├── MCP: get_weather_forecast_local(hours=48)
  │   └── ML: inference, trả về dự báo
  │
  └── "Dự báo 48h tới: nắng, nhiệt 33-38°C,
       không mưa. Độ ẩm không khí 65%.
       Khuyến nghị tưới hôm nay."
```

## Các nguồn dữ liệu cho Việt Nam

| Nguồn | Phạm vi | Độ phân giải | Phí | Phù hợp |
|-------|---------|-------------|-----|---------|
| **NASA POWER** | Toàn cầu | 0.5° (~50km) | Miễn phí | ✅ Tốt nhất cho edge |
| Open-Meteo Historical | Toàn cầu | ~11km | Miễn phí | ✅ Chi tiết hơn |
| ERA5 (Copernicus) | Toàn cầu | 0.25° (~27km) | Miễn phí | ⚠️ Phức tạp |
| Đài KTTV VN | Việt Nam | Theo trạm | Miễn phí | ❌ Không có API |

## Files liên quan trong repo

| File | Mô tả |
|------|-------|
| `ai-agent/scripts/weather_lstm.py` | Model inference |
| `ai-agent/models/lstm_tcn.onnx` | Trained model |
| `ai-agent/scripts/download_nasa.py` | Download NASA POWER data |
| `ai-agent/scripts/train_weather.py` | Training script |

---

## Nguồn tham khảo

### Edge-Native Models (quan trọng cho RPi/Jetson)

| Nguồn | Mô tả |
|-------|-------|
| **[Muhtasim-Munif-Fahim/Green-NAS](https://github.com/Muhtasim-Munif-Fahim/Green-NAS)** | NAS-discovered: **Green-NAS-C: 1.1K params, 4KB, 0.33ms inference**. RMSE 0.1019. **Siêu nhẹ — phù hợp nhất cho edge.** |
| **[rotsl/weather-ml](https://github.com/rotsl/weather-ml)** | **Cloud-edge hybrid trên RPi**: GitHub Actions train → Pi git pull → local inference. 6h rainfall prediction. |
| **[PandoroML/LOAF](https://github.com/PandoroML/LOAF)** | Hyperlocal weather: fusion of gridded forecasts (GFS/HRRR) + local sensors. **Transformer trên RPi.** |

### Vietnam / Southeast Asia Specific (quan trọng)

| Nguồn | Mô tả |
|-------|-------|
| **TFT + IoT + ERA5 — Vietnam (2026)** — [paper](https://nguyentatthanh.edu.vn/) | **50 stations across An Giang, Quang Nam, Dak Lak**. TFT pretrained on ERA5 + fine-tuned on IoT. **2m temp RMSE 0.81°C.** Kafka/Flink pipeline. |
| **Hybrid RF-LSTM cho IoT — HCMUTE (2023)** | **Raspberry Pi**. RF-LSTM hybrid. Vietnamese research team. |
| **LSTM Seasonal Rainfall Vietnam — 142 stations** | LSTM, 1-6 month lead. Climate indices (ENSO, PDO, IOD) cải thiện long-term. |
| **ConvGRU Mekong Delta Drought (2025)** | ConvGRU (CNN+GRU). ~90% drought detection tại 3-month lead. |
| **LSTM Downscaling Mekong Delta (2019)** | LSTM + FFNN cho CMIP5 climate downscaling. |

### NASA POWER Tools

| Nguồn | Mô tả |
|-------|-------|
| **[alekfal/pynasapower ⭐20](https://github.com/alekfal/pynasapower)** | **Python client chính thức cho NASA POWER API.** CSV/NetCDF/JSON. `pip install pynasapower` |
| **[stavrostheocharis/weather_data_retriever](https://github.com/stavrostheocharis/weather_data_retriever)** | Aggregate NASA POWER + Open-Meteo. |
| **[nasapower-s3](https://pypi.org/project/nasapower-s3/)** | Direct S3 access (Zarr format). High-speed. |
| **[NASA POWER Tutorials](https://power.larc.nasa.gov/docs/tutorials/)** | Official API docs, parameters, multiprocessing. |

### ONNX Export cho Edge

| Nguồn | Mô tả |
|-------|-------|
| **[198808xc/Pangu-Weather](https://github.com/198808xc/Pangu-Weather)** | ONNX models cho weather forecasting. CPU inference pattern. |
| **[taohan10200/FengWu-GHR.onnx](https://github.com/taohan10200/FengWu-GHR.onnx) ⭐93** | Full fp16 ONNX models. 0.09° resolution. |
| **[ONNX Runtime IoT Tutorial](https://onnxruntime.ai/docs/tutorials/iot-edge/rasp-pi-cv.html)** | **Step-by-step RPi ONNX deployment.** INT8 quantization, 4× speedup. |
| **[Dataroots — Jetson Nano Nowcasting](https://dataroots.io/blog/weather-nowcasting)** | Cloud→Jetson pipeline. Azure ML → Docker → Jetson. |

### Academic Papers bổ sung

| Paper | Nội dung |
|-------|----------|
| **MM-LSTM Greenhouse (AgriEng 2024)** | Multivariate multistep LSTM. RMSE 0.49, R² 0.978. Tropical greenhouse. |
| **LSTM + 3D Kriging Greenhouse (Agriculture 2025)** | 24 sensors → 3D temp field. RMSE 0.46°C. |
| **NDMI + LSTM Soil Moisture (Sustainability 2025)** | ERA5 + NDMI. R² 0.991-0.998, NSE 0.996. |
| **A-CNN-LSTM Mushroom Greenhouse (Agronomy 2024)** | Attention CNN-LSTM. Temp RMSE 0.17°C, R² 0.974. |
| **BMAE-Net (MDPI Agriculture)** | Lightweight CNN cho smart agriculture weather prediction. |

### LSTM + NASA POWER cho Weather Forecasting

| Nguồn | Mô tả | Liên quan |
|-------|-------|-----------|
| **[namanxdev/AtmoPredict](https://github.com/namanxdev/AtmoPredict)** | ⭐ **LSTM trained on NASA POWER (2010-2024)**. Dự đoán temperature/precipitation anomalies. 18 input features. FastAPI + React UI. Model 3.79 MB. | ⭐ **Reference chính**: Kiến trúc LSTM + NASA POWER giống hệ thống đề xuất. Model nhẹ (3.79 MB) — deploy edge được. |
| **[LeoRigasaki/climate-change-impact-predictor](https://github.com/LeoRigasaki/climate-change-impact-predictor)** | LSTM time-series forecasting + multi-output deep learning. 150+ world capitals. Open-Meteo + NASA POWER + World Bank CCKP. | Kết hợp nhiều nguồn dữ liệu, bidirectional LSTM với attention |
| **[Moh97746/Physics-Guided-CNN-BiLSTM-Solar](https://github.com/Moh97746/Physics-Guided-CNN-BiLSTM-Solar)** | ⭐ **CNN-BiLSTM + NASA POWER (15 features)**. Physics-guided: clear-sky, SZA, KT. **RMSE 19.53 vs 30.64 (attention baseline).** arXiv:2604.13455. | **Kiến trúc hybrid CNN-BiLSTM** — tham khảo cho LSTM-TCN. Physics-informed cải thiện đáng kể accuracy. |
| **[dusty-nv/pytorch-timeseries](https://github.com/dusty-nv/pytorch-timeseries)** | ⭐ **PyTorch timeseries forecasting trên NVIDIA Jetson.** Weather, solar prediction. Multi-input/output: temperature, humidity, pressure. | ⭐ **Edge deployment reference**: Chạy trên Jetson, RPi. docker container cho l4t-ml. |
| **[itxtx/solar_prediction](https://github.com/itxtx/solar_prediction)** | LSTM dự đoán solar GHI từ weather data. PyTorch + Optuna hyperparameter tuning. Cosine annealing + early stopping. | Tham khảo training pipeline: Optuna tuning, learning rate schedule |
| **[Abhics8/WeatherNow](https://github.com/abhics8/WeatherNow)** | LSTM temperature prediction (PyTorch). Open-Meteo data. Streamlit dashboard. Monte Carlo Dropout cho uncertainty. | Tham khảo UI + uncertainty quantification |
| **[NVIDIA/DeepLearningExamples — TimeSeriesPredictionPlatform](https://github.com/NVIDIA/DeepLearningExamples/tree/master/Tools/PyTorch/TimeSeriesPredictionPlatform)** | Production-grade platform: LSTM, TFT, N-BEATS, DeepAR. **ONNX export + NVIDIA Triton deployment.** Multi-GPU training. | ⭐ **ONNX deployment reference**: Export PyTorch → ONNX → Triton inference. |

### NASA POWER Dataset & Tools

| Nguồn | Mô tả |
|-------|-------|
| **[marco-hening-tallarico/Nasa-Climate-Data](https://github.com/marco-hening-tallarico/Nasa-Climate-Data)** | Mẫu code download + parse NASA POWER data. Python script + curl example. |
| **[notadib/NASA-Power-Daily-Weather](https://huggingface.co/datasets/notadib/NASA-Power-Daily-Weather)** | HuggingFace dataset: NASA POWER 1984-2022, 28 variables. PyTorch TensorDataset ready-to-use. |
| **[NASA POWER API Docs](https://power.larc.nasa.gov/docs/services/api/)**, tham số, temporal resolutions. | Tài liệu chính thức của NASA POWER. |

### ONNX Export cho Edge Deployment

| Nguồn | Mô tả |
|-------|-------|
| **[PyTorch → ONNX Tutorial](https://pytorch.org/docs/stable/onnx.html)** | Official guide: export PyTorch model sang ONNX, optimize, quantize. |
| **[ONNX Runtime](https://github.com/microsoft/onnxruntime)** | Inference engine cho ONNX models. Hỗ trợ ARM (RPi), CUDA (Jetson). |
| **[Java + ONNX Deployment (JAEDS, 2025)](https://jaeds.uitm.edu.my/index.php/jaeds/article/download/144/91/1401)** | LSTM PyTorch → ONNX → DJL (Java). Tham khảo edge deployment không dùng Python. |

### Academic Papers

| Paper | Nội dung |
|-------|----------|
| **arXiv:2604.13455** — Physics-Guided CNN-BiLSTM for Solar Irradiance Forecasting | CNN-BiLSTM + NASA POWER. Physics-informed (clear-sky, SZA). Cải thiện 36% RMSE so với attention baseline. |
| **ACML 2019** — Functional Isolation Forest (Staerman et al.) | Extension của Isolation Forest cho time-series. |
| **IEEE Access 2022** — LoRa Mesh Library Implementation (LoRaMesher paper) | TDMA distance-vector routing cho LoRa mesh. |
