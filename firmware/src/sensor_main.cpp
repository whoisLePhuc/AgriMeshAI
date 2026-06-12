#include <Arduino.h>
#include <memory>
#include <DHT.h>
#include "loramesher.hpp"

#ifdef USE_BH1750
#include <hp_BH1750.h>
#endif

using namespace loramesher;

#define LORA_CS     8
#define LORA_RST    12
#define LORA_IRQ    14
#define LORA_IO1    13
#define LORA_SCK    9
#define LORA_MISO   11
#define LORA_MOSI   10

#define PIN_DHT       6
#define PIN_LED       35

#define LORA_FREQ     868.0F
#define LORA_SF       12
#define LORA_BW       125.0F
#define LORA_CR       7
#define LORA_TX_PWR   14

#define REPORT_INTERVAL_MS  60000

DHT dht(PIN_DHT, DHT22);
#ifdef USE_BH1750
hp_BH1750 bh1750;
#endif

std::unique_ptr<LoraMesher> mesher = nullptr;
AddressType node_addr = 0;
unsigned long last_report_ms = 0;
#ifdef USE_BH1750
bool bh1750_ok = false;
#endif

void setupLoRa();
void readSensors(float& temp, float& hum, float& lux);
void sendReading(uint8_t sensor_id, float value);
void blink(uint8_t times, uint16_t ms);

void OnDataReceived(AddressType source, const std::vector<uint8_t>& data) {
    Serial.printf("[LoRa] Received %zu bytes from 0x%04X\n", data.size(), source);
}

void setup() {
    Serial.begin(115200);
    delay(1000);
    Serial.println("\n\n=== AgriMeshAI Sensor Node ===");

    pinMode(PIN_LED, OUTPUT);
    blink(3, 100);

    dht.begin();
    Serial.println("[Sensor] DHT22 started");

#ifdef USE_BH1750
    Wire.begin(5, 6);
    bh1750.begin();
    bh1750_ok = true;
    Serial.println("[Sensor] BH1750 started");
#endif

    setupLoRa();
}

void loop() {
    unsigned long now = millis();
    if (now - last_report_ms >= REPORT_INTERVAL_MS) {
        last_report_ms = now;

        float temp = NAN, hum = NAN, lux = NAN;
        readSensors(temp, hum, lux);

        if (!isnan(temp)) sendReading(0, temp);
        if (!isnan(hum))  sendReading(1, hum);
        if (!isnan(lux))  sendReading(3, lux);
    }
    vTaskDelay(pdMS_TO_TICKS(10));
}

void setupLoRa() {
    PinConfig pinConfig(LORA_CS, LORA_RST, LORA_IRQ, LORA_IO1,
                        LORA_SCK, LORA_MISO, LORA_MOSI);

    RadioConfig radioConfig(RadioType::kSx1262, LORA_FREQ, LORA_SF,
                            LORA_BW, LORA_CR, LORA_TX_PWR);
    radioConfig.setTcxoVoltage(1.8F);

    LoRaMeshProtocolConfig meshConfig;

    mesher = LoraMesher::Builder()
                 .withRadioConfig(radioConfig)
                 .withPinConfig(pinConfig)
                 .withLoRaMeshProtocol(meshConfig)
                 .Build();

    mesher->SetDataCallback(OnDataReceived);

    Result r = mesher->Start();
    if (!r) {
        Serial.printf("[LoRa] Start FAILED: %s\n", r.GetErrorMessage());
        return;
    }

    node_addr = mesher->GetNodeAddress();
    Serial.printf("[LoRa] Mesh started, address=0x%04X\n", node_addr);
}

void readSensors(float& temp, float& hum, float& lux) {
    float t = dht.readTemperature();
    float h = dht.readHumidity();

    if (isnan(t) || isnan(h)) {
        Serial.println("[Sensor] DHT22 FAILED");
    } else {
        temp = t; hum = h;
        Serial.printf("[Sensor] Temp=%.1fC  Humidity=%.0f%%\n", t, h);
    }

#ifdef USE_BH1750
    float lx = bh1750.getLux();
    if (lx >= 0) { lux = lx; Serial.printf("[Sensor] Light=%.0f lx\n", lx); }
#endif
}

void sendReading(uint8_t sensor_id, float value) {
    if (!mesher) return;

    uint8_t payload[5];
    payload[0] = sensor_id;
    memcpy(payload + 1, &value, sizeof(value));

    Result ready = mesher->IsReadyToSend();
    if (!ready) {
        Serial.printf("[LoRa] Not ready: %s\n", ready.GetErrorMessage());
        return;
    }

    Result r = mesher->Send(0, std::vector<uint8_t>(payload, payload + 5));
    if (r) {
        Serial.printf("[LoRa] Sensor %d=%.1f sent\n", sensor_id, value);
        blink(1, 30);
    } else {
        Serial.printf("[LoRa] Send FAILED: %s\n", r.GetErrorMessage());
    }
}

void blink(uint8_t times, uint16_t ms) {
    for (uint8_t i = 0; i < times; i++) {
        digitalWrite(PIN_LED, HIGH);
        delay(ms);
        digitalWrite(PIN_LED, LOW);
        if (i < times - 1) delay(ms);
    }
}
