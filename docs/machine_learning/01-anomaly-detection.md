# Anomaly Detection — Phát hiện bất thường

## Vai trò trong hệ thống

Hệ thống có 3 tầng phát hiện bất thường, tăng dần độ phức tạp:

```
Tầng 1 — Rule Engine (đã có)
  Threshold cứng trên từng sensor riêng lẻ
  "Nhiệt > 40°C → cảnh báo"
  ✅ Đơn giản, tức thì
  ❌ Bỏ sót bất thường tinh vi

Tầng 2 — Univariate ML (sẽ xây)
  Phát hiện deviation trên từng sensor
  "Nhiệt 37°C (trong ngưỡng) nhưng tăng 5°C/giờ"
  ✅ Phát hiện sớm hơn rule
  ❌ Không thấy tương quan giữa các sensor

Tầng 3 — Multivariate ML (sẽ xây)
  Phát hiện bất thường trên tổ hợp nhiều sensor
  "Nhiệt 36°C + Ẩm 45% + Áp suất 980hPa: chưa từng xảy ra"
  ✅ Phát hiện bất thường hệ thống
```

---

## Phần 1: Univariate Anomaly Detection (Đơn chiều)

### Khái niệm

Phát hiện bất thường trên **từng sensor riêng lẻ**, dựa vào hành vi lịch sử của chính nó.

### Vấn đề với Rule Engine hiện tại

Rule engine dùng **ngưỡng cứng tuyệt đối**:

```
Rule:          nhiệt tank_02 > 40°C → cảnh báo
```

Các bất thường bị bỏ sót:

| Thời điểm | Nhiệt độ | Rule engine | Phân tích |
|-----------|---------|-------------|-----------|
| 00:00 | 32°C | ✅ OK | Baseline trung bình 33°C |
| 01:00 | 33°C | ✅ OK | Bình thường |
| 02:00 | 35°C | ✅ OK | Hơi cao |
| 03:00 | **37°C** | ✅ **OK (còn dưới 40)** | **Tăng 5°C trong 3h — bất thường!** |
| 04:00 | **39°C** | ✅ **OK** | **Sắp chạm ngưỡng, nhưng rule chưa báo** |
| 05:00 | **41°C** | ❌ **Cảnh báo** | **Quá trễ! Thiết bị có thể đã hỏng** |

**Rule engine phát hiện chậm 2 giờ so với ML.**

### Giải pháp Univariate ML

Học **baseline động** từ lịch sử, phát hiện độ lệch thống kê:

```
Baseline 7 ngày qua
  ─────────────────────────────────────
  Trung bình: 33°C
  Độ lệch chuẩn (σ): 1.5°C
  Ngưỡng động: 33°C ± 3σ = 28.5°C → 37.5°C

  Tại 03:00, nhiệt = 37°C
  → 37°C > 33°C + 3×1.5°C = 37.5°C ?
     KHÔNG, vẫn còn trong ngưỡng 3σ
  → Nhưng rate of change = (37-32)/3 = +1.67°C/h
  → Bình thường rate = ±0.3°C/h
  → Rate of change bất thường! → Cảnh báo sớm
```

### Các mô hình Univariate

| Model | RAM | Phát hiện | Đặc điểm |
|-------|-----|-----------|----------|
| **Moving Average ± 3σ** | ~5 MB | Deviation từ baseline | Rất nhẹ, realtime |
| **Exponential Weighted MA** | ~5 MB | Deviation (gần đây quan trọng hơn) | Nhạy với thay đổi gần |
| **Linear Regression slope** | ~5 MB | Rate of change bất thường | Phát hiện trend |
| **Variance threshold** | ~5 MB | Stuck sensor (phương sai ≈ 0) | Phát hiện cảm biến hỏng |

### Các luật Univariate ML

| Mã | Luật | Mô tả | So với Rule cũ |
|----|------|-------|----------------|
| `U01` | `\|value - mean\| > 3σ` | Deviation từ baseline 7 ngày | Phát hiện sớm hơn 1-3h |
| `U02` | `\|slope\| > 5× baseline_slope` | Rate of change bất thường | **Mới** — phát hiện trend |
| `U03` | `variance < ε` trong 12h | Stuck sensor (cảm biến hỏng) | Rule cũ 6h → giảm dương tính giả |
| `U04` | `value == value_previous` trong 24h | Sensor chết hẳn (giá trị không đổi) | Giống rule cũ nhưng chính xác hơn |
| `U05` | `seasonal_deviation > 3σ` | Lệch so cùng giờ các ngày trước | **Mới** — phát hiện theo chu kỳ |

---

## Phần 2: Multivariate Anomaly Detection (Đa chiều)

### Khái niệm

Phát hiện bất thường trên **tổ hợp nhiều sensor cùng lúc**.

> **Ý tưởng:** Từng sensor riêng lẻ có thể hoàn toàn bình thường, nhưng **kết hợp chúng lại** tạo thành một trạng thái chưa từng xuất hiện trong lịch sử → đó là bất thường.

### Vấn đề với Univariate

Univariate check từng cái một:

```
nhiệt tank_02: 36°C   (ngưỡng 40°C → OK)
độ ẩm:         45%    (ngưỡng 30% → OK)
áp suất:       980hPa (ngưỡng 950hPa → OK)
```

**Từng cái đều OK.** Nhưng bộ 3 số này chưa từng xuất hiện cùng nhau:

```
  ┌─────────────────────────────────────────┐
  │          2 NĂM DỮ LIỆU                   │
  │                                          │
  │  Khi nhiệt = 36°C:                       │
  │    • Ẩm thường = 60-70% (mùa mưa)        │
  │    • Ẩm thường = 50-55% (mùa khô)        │
  │    • Áp suất thường = 1005-1015hPa        │
  │                                          │
  │  → Ẩm = 45% + Áp suất = 980hPa           │
  │    tại nhiệt = 36°C: CHƯA BAO GIỜ XẢY RA │
  │                                          │
  │  → Isolation Forest: "Bất thường đa chiều!" │
  │  → Có thể là sắp có dông/mưa đá          │
  └──────────────────────────────────────────┘
```

### Trực quan hoá

**Không gian 1 chiều (1D) — Univariate:**

```
nhiệt độ
  ↑
  │     ngưỡng 40°C
42├───────┄┄┄┄┄┄┄┄┄┄┄┄  ← cảnh báo
  │
40├────────────────────
  │
38├           ●
  │● ● ● ● ●     ● ●     ● = reading bình thường
36├ ● ★ ●
  │
34├───┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄
  │     ★ = 36°C (trong ngưỡng → rule không báo)
  └──────────────────────────────► thời gian
```

**Không gian 2 chiều (2D) — Multivariate:**

```
nhiệt độ
  ↑
40├  ● ●     ● ●     ●
  │ ●     ● ●   ● ● ●      ● = (nhiệt, ẩm) bình thường
38├   ●   ● ●   ● ●         ★ = (nhiệt=36, ẩm=45)
  │ ●     ● ★ ●   ●           chưa từng xuất hiện
36├ ● ● ●   ●   ●
  │
34├────────────────────►
  └───┬────┬────┬────┬── độ ẩm
     30%  40%  50%  60%
```

**Không gian 3 chiều (3D) — Nhiệt + Ẩm + Áp suất:**

```
              ★ = bất thường đa chiều
              (nhiệt=36, ẩm=45, áp=980)

              Trong 2 năm, tại vùng không gian này
              chưa từng có điểm nào → bất thường

      áp suất
        ↑
    1015├──●────●──●──●
        │ ●  ● ●  ● ●
    1000├──●──●─★──●──●   ★
        │ ● ●  ● ●  ●
     980├──────────────●
        └───┬────┬────┬────► độ ẩm
           30%  45%  60%
          ──► nhiệt độ
          (mũi tên đi ra màn hình là chiều thứ 3)
```

### Giải pháp Multivariate ML

**Isolation Forest** — ý tưởng: bất thường là điểm "dễ bị cô lập" (dễ tách ra khỏi phần còn lại).

```
Bước 1: Chọn ngẫu nhiên 1 feature + 1 threshold
Bước 2: Chia không gian làm 2 nửa
Bước 3: Lặp lại → cô lập từng điểm

  Điểm bình thường: cần nhiều lần chia mới cô lập được
  Điểm bất thường: chỉ cần 1-2 lần chia là cô lập ngay

  → Anomaly score = độ sâu trung bình khi cô lập
  → Score càng cao (càng dễ cô lập) = càng bất thường
```

**Trực quan Isolation Forest:**

```
Không gian 2D (nhiệt + ẩm):

nhiệt
  ↑
  │  ● ● ● │ ● ● ●     Lần chia thứ nhất:
  │  ● ● ● │ ● ● ●       threshold = 37°C
  │  ● ● ● │ ● ● ●
  │  ● ● ● │ ● ★ ●     ★: bên phải, chỉ còn 3 điểm
  │  ● ● ● │   ●          trong đó có ★
  │────────┼──────        → lần chia thứ 2: tách ★ ngay
  │  ● ●   │   ●
  │  ●   ● │              → ★ bất thường (score cao)
  └────────────────► ẩm
```

### Các luật Multivariate ML

| Mã | Luật | Model | Phát hiện |
|----|------|-------|-----------|
| `M01` | Tổ hợp feature chưa từng xuất hiện | Isolation Forest | Bất thường hệ thống |
| `M02` | 2 sensor tương quan đảo chiều | Cross-correlation | Van đóng / sensor hỏng |
| `M03` | Tương quan nhiệt-ẩm bất thường | Mahalanobis Distance | Bất thường vi khí hậu |
| `M04` | Cả cụm sensor cùng lệch | DBSCAN clustering | Cluster bất thường |

### Ví dụ thực tế

**Ví dụ 1 — Sắp có dông bất thường:**

```
Sensor readings (từng cái OK):
  Nhiệt:   35°C  (bình thường cho mùa)
  Ẩm:      48%   (hơi thấp nhưng OK)
  Áp suất: 985hPa (hơi thấp nhưng OK)
  Gió:     2m/s  (bình thường)

  Từng cái: tất cả trong ngưỡng ✅

  Nhưng Isolation Forest:
    "Bộ (35, 48, 985, 2) chưa từng xuất hiện trong 2 năm
     vào thời điểm tháng 6. Bất thường đa chiều!"

  → AI Agent:
    "Phát hiện bất thường: nhiệt + ẩm + áp suất tạo
     tổ hợp chưa từng có. Có thể sắp có dông bất thường.
     Đề nghị kiểm tra chằng chống nhà kính."
```

**Ví dụ 2 — Cảm biến nhiệt bị hỏng (phát hiện nhờ tương quan):**

```
Bình thường:
  Khi nhiệt độ tăng → RH (độ ẩm tương đối) thường giảm
  Tương quan nghịch: R = -0.85

Bất thường:
  Nhiệt tăng 35→38°C, RH cũng tăng 50→55%
  → Tương quan = +0.3 (đảo chiều!)

  → Cross-correlation phát hiện: "cảm biến nhiệt đang lỗi"
  → Kiểm tra: cảm biến nhiệt bị ẩm, đọc sai
```

### So sánh 3 tầng

| Tiêu chí | Rule Engine | Univariate ML | Multivariate ML |
|-----------|-------------|---------------|-----------------|
| **Phát hiện** | Ngưỡng tuyệt đối | Deviation từ baseline | Tổ hợp bất thường |
| **Số sensor/lần** | 1 | 1 | 2+ |
| **Phát hiện sớm** | ❌ Trễ | ✅ Sớm hơn 1-3h | ✅ Sớm hơn 3-12h |
| **False positive** | Thấp (cứng) | Trung bình | Trung bình |
| **RAM** | ~1 MB | ~5 MB | ~30 MB |
| **Tương quan sensor** | ❌ Không | ❌ Không | ✅ Có |
| **Phát hiện stuck sensor** | ⚠️ Thô | ✅ | ✅ |
| **Phát hiện hệ thống** | ❌ | ❌ | ✅ |

### Tích hợp MCP

```python
@mcp.tool
def search_anomalies(hours: int = 24, method: str = "all") -> list:
    """
    Query các bất thường.

    method:
      - "univariate":   Deviation từng sensor (Moving Avg ± 3σ)
      - "multivariate": Tổ hợp sensor (Isolation Forest)
      - "all":          Cả hai

    AI Agent gọi khi người dùng hỏi 'có gì lạ không?'
    """
    alerts = []
    if method in ("univariate", "all"):
        alerts += univariate_scan(hours)    # U01-U05
    if method in ("multivariate", "all"):
        alerts += multivariate_scan(hours)  # M01-M04
    return alerts
```

### Kịch bản tổng hợp

```
Người dùng: "Có gì bất thường không?"

AI Agent:
  ├── MCP: search_anomalies(hours=24, method="all")
  │
  ├── Kết quả:
  │
  │   Univariate (3 phát hiện):
  │   ├── [U01] tank_02: nhiệt 37°C, lệch 2.5σ so baseline
  │   │       → Tăng đều 5°C trong 3h
  │   ├── [U03] soil_03: variance = 0.01 trong 14h
  │   │       → Cảm biến hỏng (stuck)
  │   └── [U05] outdoor_temp: 34°C lúc 02:00, lệch 4σ so
  │           cùng giờ các ngày trước (bthường 26-28°C)
  │
  │   Multivariate (1 phát hiện):
  │   └── [M01] Tổ hợp (outdoor_temp=34, RH=48, pressure=985)
  │           chưa từng xuất hiện tháng 6
  │           → Isolation Forest score: 0.92 (cao)
  │
  └── "Phát hiện 4 vấn đề:

       ⚠️ Nhiệt tank_02 tăng bất thường (5°C/3h)
          → Có thể chiller sắp hỏng, kiểm tra gấp

       ⚠️ Cảm biến soil_03 stuck 14h
          → Cần thay pin hoặc kiểm tra kết nối

       ⚠️ Nhiệt ngoài trời lúc 2h sáng đạt 34°C
          → Bất thường theo mùa, cần theo dõi

       🔴 Cảnh báo đa chiều: Nhiệt cao + ẩm thấp + áp suất thấp
          → Tổ hợp này báo hiệu dông bất thường
          → Đề nghị kiểm tra nhà kính, mái che"
```

### Files liên quan

| File | Mô tả |
|------|-------|
| `ai-agent/scripts/anomaly.py` | Univariate + Multivariate detector |
| `ai-agent/scripts/anomaly_univariate.py` | Các luật U01-U05 |
| `ai-agent/scripts/anomaly_multivariate.py` | Isolation Forest + Cross-correlation |
| `ai-agent/models/isolation_forest.pkl` | Trained model |

---

## Nguồn tham khảo

### Streaming Univariate Detection

| Nguồn | Mô tả |
|-------|-------|
| **[Fengrui-Liu/StreamAD ⭐131](https://github.com/Fengrui-Liu/StreamAD)** | Online anomaly detection cho data streams. 8+ detectors: SPOT (Peaks Over Threshold), KNN, Z-score, One-class SVM, RRCF. Python 3. |
| **[naitikshah1008/Real-Time-System-Monitoring](https://github.com/naitikshah1008/Real-Time-System-Monitoring)** | Kafka + Flink + TimescaleDB pipeline. EWMA + 3σ adaptive thresholding. |
| **[Azure Anomaly Detector](https://github.com/Azure/azure-sdk-for-python/tree/main/sdk/anomalydetector/azure-ai-anomalydetector)** | Univariate & multivariate APIs. Graph Attention Network cho multivariate. Batch & streaming. |
| **[llama-farm/llamafarm](https://github.com/llama-farm/llamafarm)** | IoT sensor monitoring với ECOD backend. Rolling windows, concept drift handling. |
| **[MarekWadinger/adaptive-interpretable-ad](https://github.com/MarekWadinger/adaptive-interpretable-ad)** | Self-supervised adaptive AD với dynamic operating limits. MQTT-based. |

### Smart Farm + Multivariate Specific

| Nguồn | Mô tả |
|-------|-------|
| **[YasmineBenYamna/smart-farm-anomaly-monitoring](https://github.com/YasmineBenYamna/smart-farm-anomaly-monitoring)** | End-to-end smart farming: Django + Isolation Forest + AI recommendation agent. Soil moisture, temp, humidity. **Cùng sensor types với bạn.** |
| **[divyamohan1993/amdslingshot](https://github.com/divyamohan1993/amdslingshot)** (JalNetra) | Edge-AI water quality monitoring. **ESP32 + LoRa**, ONNX Runtime, AMD XDNA NPU. |
| **[alizangeneh/unsupervised-anomaly-detection-ml](https://github.com/alizangeneh/unsupervised-anomaly-detection-ml)** | So sánh KMeans, DBSCAN, Isolation Forest, Autoencoder trên IoT sensor data. |
| **[21lakshh/Kisaan-Saathi](https://github.com/21lakshh/Kisaan-Saathi)** | DBSCAN clustering cho hotspot mapping trong nông nghiệp. |
| **[cracketus/senior-pomidor](https://github.com/cracketus/senior-pomidor)** | Smart agriculture framework với confidence scoring, ring buffer smoothing, VPD calculation. |

### Academic Papers

| Paper | Nội dung |
|-------|----------|
| **AHE-FNUQ (Sensors 2025)** — [link](https://www.mdpi.com/1424-8220/25/22/6841) | 6-method ensemble (IF, ECOD, COPOD, HBOS, OC-SVM, KNN). **3-tier decision** trên Agri-IoT. F1 0.85-0.90, ROC AUC 0.93-0.99. |
| **CNN-LSTM-POT Greenhouse (Sensors 2026)** — [link](https://www.mdpi.com/1999-5903/18/4/205) | Adaptive thresholding via extreme value theory. Edge deployment. |
| **Smart Farm PHM (Applied Sci 2025)** — [link](https://www.mdpi.com/2076-3417/15/23/12843) | Hybrid IQR + Z-score + Isolation Forest. >90% accuracy trên commercial greenhouse. |
| **DL Anomaly Detection for Agriculture (Frontiers 2025)** — [link](https://www.frontiersin.org/journals/plant-science/articles/10.3389/fpls.2025.1576756/full) | IMSFNet: GNN + Transformers, multi-modal (satellite + ground sensors). Dùng NAB benchmark. |

### Isolation Forest cho IoT Multivariate Anomaly Detection

| Nguồn | Mô tả | Liên quan |
|-------|-------|-----------|
| **[Jacobventer/Model-to-Production-IoT-Anomaly-Detection](https://github.com/Jacobventer/Model-to-Production-IoT-Anomaly-Detection)** | Isolation Forest production pipeline với Flask REST API, sensor simulator, logging. Features: temperature, humidity, sound. | Kiến trúc pipeline tương tự: train → save model → REST API → live inference |
| **[ipsyume/iot-anomaly-detection](https://github.com/ipsyume/iot-anomaly-detection)** | IoT anomaly detection so sánh Isolation Forest vs Autoencoder trên multivariate sensor data. Có visual anomaly locations. | So sánh 2 approach — giúp chọn model phù hợp |
| **[CodingRaemajor/IoT_Anomaly_Detector](https://github.com/CodingRaemajor/IoT_Anomaly_Detector)** | Real-time dashboard (Streamlit) + Isolation Forest trên IoT sensor stream. Nhiệt độ, độ ẩm, chuyển động. | Tham khảo UI và real-time inference |
| **[lucaswjunges/industrial-anomaly-detection](https://github.com/lucaswjunges/industrial-anomaly-detection)** | 3 models: Isolation Forest, LOF, Autoencoder. Feature engineering từ raw sensor data. NASA bearing dataset. | **Benchmark số:** IF: Precision 84.2%, Recall 81.5%, F1 82.8% |
| **[GireeshRavula/Iot-Anomaly-Detection](https://github.com/GireeshRavula/Iot-Anomaly-Detection)** | Time series anomaly detection tổng hợp: Isolation Forest + Autoencoder. Synthetic multivariate IoT data. | Tham khảo data generation cho training |
| **[GuillaumeStaermanML/FIF](https://github.com/GuillaumeStaermanML/FIF)** | Functional Isolation Forest — ACML 2019 paper. Dùng cho functional data (time-series). | **Academic:** Nâng cấp Isolation Forest cho time-series |
| **[Microsoft Fabric — Multivariate Anomaly Detection with Isolation Forest](https://learn.microsoft.com/en-us/fabric/data-science/isolation-forest-multivariate-anomaly-detection)** | Isolation Forest trên Apache Spark với 3 IoT sensors. SynapseML implementation. | Enterprise-scale, tham khảo parameter tuning |
| **[aakrivenkovskaya/industrial-engine-anomaly-detection](https://github.com/aakrivenkovskaya/industrial-engine-anomaly-detection)** | Industrial multivariate: Isolation Forest + One-Class SVM + PCA diagnostics. Statistical baselines (Z-score, IQR). | Kiến trúc 3 tầng: statistical → ML → PCA tương tự hệ thống đề xuất |
| **[ali3brt/anomaly-detection-time-series](https://github.com/ali3brt/anomaly-detection-time-series)** | Isolation Forest + LSTM + Gradient Boosting cho multivariate time series. Có real-time simulation với sliding window. | Đa dạng model, LSTM validation accuracy >94% |
| **[rahulraimau/cyclone_-preheater_anamoly1](https://github.com/rahulraimau/cyclone_-preheater_anamoly1)** | 3 năm multivariate sensor data, 437 bất thường phát hiện bởi Isolation Forest + One-Class SVM + Z-score. Cross-model agreement. | Cross-model agreement: dùng nhiều model để confirm bất thường |

### Univariate Anomaly Detection

| Nguồn | Mô tả |
|-------|-------|
| **[Numenta Anomaly Benchmark (NAB)](https://github.com/numenta/NAB)** | Benchmark chuẩn cho real-time anomaly detection. Thuật toán: moving average, EWMA, Twitter ADVec. |
| **[skyline](https://github.com/etsy/skyline)** | Real-time anomaly detection cho time-series metrics. Dùng moving average, stddev từ Etsy production. |
| **[AnomalyDetection](https://github.com/twitter/AnomalyDetection)** | Twitter's open source R package. Seasonal decomposition + statistical testing. |

### Lý thuyết nền tảng

| Paper | Nội dung |
|-------|----------|
| **Liu et al., 2008** — Isolation Forest | Paper gốc về Isolation Forest. Cơ sở lý thuyết. |
| **Breunig et al., 2000** — LOF | Local Outlier Factor — density-based anomaly detection. |
| **Staerman et al., 2019** — Functional Isolation Forest (ACML) | Mở rộng Isolation Forest cho functional data / time-series. |
