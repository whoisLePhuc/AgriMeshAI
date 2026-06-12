# AgriMeshAI — ESP32 Firmware

LoRa Mesh firmware cho sensor và actuator nodes, dùng **LoRaMesher** library (branch `main`).

## Yêu cầu

- VS Code + PlatformIO IDE extension
- Hoặc CLI: `pip install platformio`

## Build & Upload

```bash
# Sensor node (Heltec WiFi LoRa 32 V3)
pio run -e sensor_node -t upload -t monitor

# Actuator node (LILYGO T3-S3)
pio run -e actuator_node -t upload -t monitor
```

## Wiring

### Sensor node (Heltec WiFi LoRa 32 V3)

| Pin | Kết nối |
|-----|---------|
| GPIO 6 | DHT22 data |
| I2C (SDA=5, SCL=6) | BH1750 |
| SPI: CS=8, RST=12, IRQ=14, IO1=13, SCK=9, MISO=11, MOSI=10 | SX1262 |
| GPIO 35 | LED (onboard) |

Lưu ý: Heltec V3 dùng TCXO 1.8V → `radioConfig.setTcxoVoltage(1.8F)`.

BH1750 (light sensor) là optional — uncomment `-DUSE_BH1750` trong `platformio.ini` và dòng BH1750 trong `lib_deps` nếu có sensor.

### Actuator node (LILYGO T3-S3)

| Pin | Kết nối |
|-----|---------|
| GPIO 14-17 | Relay module (4 kênh) |
| CS=7, RST=8, IRQ=9, IO1=33 | SX1262 |
| GPIO 35 | LED (onboard) |

## API LoRaMesher (main branch)

```cpp
#include "loramesher.hpp"
using namespace loramesher;

// 1. Config: pin + radio + protocol
PinConfig pinConfig(cs, rst, irq, io1, sck, miso, mosi);
RadioConfig radioConfig(RadioType::kSx1262, freq, sf, bw, cr, pwr);
LoRaMeshProtocolConfig meshConfig;

// 2. Build (Builder pattern — không phải new LoraMesher)
auto mesher = LoraMesher::Builder()
    .withRadioConfig(radioConfig)
    .withPinConfig(pinConfig)
    .withLoRaMeshProtocol(meshConfig)
    .Build();

// 3. Callback
mesher->SetDataCallback([](AddressType src, const std::vector<uint8_t>& data) {
    // data chứa payload byte
});

// 4. Start
Result r = mesher->Start();

// 5. Check ready & send
if (mesher->IsReadyToSend()) {
    mesher->Send(destination, std::vector<uint8_t>(payload, payload + len));
}

// 6. Địa chỉ node
AddressType my_addr = mesher->GetNodeAddress();
```

## Giao thức (byte payload)

### Sensor → Gateway
```
byte[0]: sensor_id (0=temp, 1=hum, 2=moisture, 3=light)
byte[1..4]: value (float32, little-endian)
```

### Gateway → Actuator
```
byte[0]: relay_id (0-3)
byte[1]: command (0=OFF, 1=ON, 2=TOGGLE)
byte[2..5]: duration_ms (uint32, little-endian, 0=indefinite)
```

### Actuator ACK → Gateway
```
byte[0]: relay_id
byte[1]: state (0=OFF, 1=ON)
byte[2..5]: reserved
```

Gateway address: `0` (tất cả node gửi về 0).
