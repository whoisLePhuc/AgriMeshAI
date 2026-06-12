/**
 * AgriMeshAI — Actuator Node
 *
 * ESP32-S3 + SX1262 + Relay module
 * Nhận lệnh bật/tắt relay qua LoRa mesh, thực thi, trả ACK
 * Auto-off timer cho irrigation safety
 *
 * Pin mapping (LILYGO T3-S3):
 *   SX1262: CS=7, RST=8, IRQ=9, IO1=33
 *   Relay:  GPIO 14, 15, 16, 17 (4 channels)
 *   LED:    GPIO 35
 */

#include <Arduino.h>
#include "loramesher.hpp"

using namespace loramesher;

// ── Pin definitions ──────────────────────────────────────────────
#define LORA_CS     7
#define LORA_RST    8
#define LORA_IRQ    9
#define LORA_IO1    33

#define PIN_RELAY_0   14
#define PIN_RELAY_1   15
#define PIN_RELAY_2   16
#define PIN_RELAY_3   17
#define PIN_LED       35

// ── LoRa parameters ──────────────────────────────────────────────
#define LORA_FREQ     868.0F
#define LORA_SF       12
#define LORA_BW       125.0F
#define LORA_CR       7
#define LORA_TX_PWR   14

// ── Safety ───────────────────────────────────────────────────────
#define MAX_ON_DURATION_MS  1800000
#define STATUS_INTERVAL_MS  300000

// ── Globals ──────────────────────────────────────────────────────
std::unique_ptr<LoraMesher> mesher = nullptr;

struct RelayState {
    bool     on          = false;
    uint32_t on_since    = 0;
    uint32_t auto_off_ms = 0;
};

RelayState relays[4];
uint32_t last_status_ms = 0;

// ── Forward ──────────────────────────────────────────────────────
void setupLoRa();
void processCommand(uint8_t relay, uint8_t cmd, uint32_t duration);
void sendAck(uint8_t relay_id, uint8_t state);
void sendStatus();
void safetyCheck();
void setRelay(uint8_t idx, bool state);
void blink(uint8_t times, uint16_t ms);

// ── Callback: data received from mesh ────────────────────────────
void OnDataReceived(AddressType source, const std::vector<uint8_t>& data) {
    if (data.size() < 3) return;  // min: relay(1) + cmd(1) + duration(4) = 6

    uint8_t relay   = data[0];
    uint8_t command = data[1];
    uint32_t duration = 0;
    if (data.size() >= 6) {
        duration = (uint32_t)data[2] | ((uint32_t)data[3] << 8) |
                   ((uint32_t)data[4] << 16) | ((uint32_t)data[5] << 24);
    }

    Serial.printf("[CMD] Relay %d → %s (dur=%lu, from=0x%04X)\n",
                  relay,
                  command == 1 ? "ON" : command == 0 ? "OFF" : "TOGGLE",
                  duration, source);

    processCommand(relay, command, duration);
}

// ── Setup ────────────────────────────────────────────────────────
void setup() {
    Serial.begin(115200);
    delay(1000);
    Serial.println("\n\n=== AgriMeshAI Actuator Node ===");

    pinMode(PIN_LED, OUTPUT);
    blink(2, 200);

    for (int i = 0; i < 4; i++) {
        int pin = PIN_RELAY_0 + i;
        pinMode(pin, OUTPUT);
        digitalWrite(pin, LOW);
    }
    Serial.println("[Relay] 4 channels initialized, all OFF");

    setupLoRa();
}

// ── Loop ─────────────────────────────────────────────────────────
void loop() {
    safetyCheck();

    unsigned long now = millis();
    if (now - last_status_ms >= STATUS_INTERVAL_MS) {
        last_status_ms = now;
        sendStatus();
    }

    vTaskDelay(pdMS_TO_TICKS(10));
}

// ── LoRa setup ───────────────────────────────────────────────────
void setupLoRa() {
    PinConfig pinConfig(LORA_CS, LORA_RST, LORA_IRQ, LORA_IO1);

    RadioConfig radioConfig(RadioType::kSx1262, LORA_FREQ, LORA_SF,
                            LORA_BW, LORA_CR, LORA_TX_PWR);

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

    Serial.printf("[LoRa] Mesh started, address=0x%04X\n",
                  mesher->GetNodeAddress());
}

// ── Process command ──────────────────────────────────────────────
void processCommand(uint8_t relay, uint8_t cmd, uint32_t duration) {
    if (relay >= 4) {
        Serial.printf("[Safety] Invalid relay: %d\n", relay);
        return;
    }

    uint8_t new_state;
    switch (cmd) {
        case 1: setRelay(relay, true);  new_state = 1; break;
        case 0: setRelay(relay, false); new_state = 0; break;
        case 2: setRelay(relay, !relays[relay].on);
                new_state = relays[relay].on ? 1 : 0; break;
        default: return;
    }

    if (duration > 0) {
        uint32_t clamped = (duration > MAX_ON_DURATION_MS) ? MAX_ON_DURATION_MS : duration;
        relays[relay].auto_off_ms = clamped;
        relays[relay].on_since = millis();
    } else {
        relays[relay].auto_off_ms = 0;
    }

    sendAck(relay, new_state);
}

// ── Safety: auto-off ─────────────────────────────────────────────
void safetyCheck() {
    unsigned long now = millis();
    for (int i = 0; i < 4; i++) {
        if (!relays[i].on || relays[i].auto_off_ms == 0) continue;
        if (now - relays[i].on_since >= relays[i].auto_off_ms) {
            Serial.printf("[Safety] Auto-off relay %d\n", i);
            setRelay(i, false);
            sendAck(i, 0);
        }
    }
}

// ── Relay control ────────────────────────────────────────────────
void setRelay(uint8_t idx, bool state) {
    if (idx >= 4) return;
    digitalWrite(PIN_RELAY_0 + idx, state ? HIGH : LOW);
    relays[idx].on = state;
    if (state) relays[idx].on_since = millis();
    Serial.printf("[Relay %d] %s\n", idx, state ? "ON" : "OFF");
    blink(1, 50);
}

// ── Send ACK ─────────────────────────────────────────────────────
void sendAck(uint8_t relay_id, uint8_t state) {
    if (!mesher) return;
    uint8_t payload[6] = {relay_id, state, 0, 0, 0, 0};
    Result r = mesher->Send(0, std::vector<uint8_t>(payload, payload + 6));
    if (!r) Serial.printf("[LoRa] ACK FAILED: %s\n", r.GetErrorMessage());
}

// ── Send periodic status ─────────────────────────────────────────
void sendStatus() {
    if (!mesher) return;
    Result ready = mesher->IsReadyToSend();
    if (!ready) return;

    uint8_t payload[2] = {0xFF, 0};  // 0xFF = status report
    Result r = mesher->Send(0, std::vector<uint8_t>(payload, payload + 2));
    Serial.printf("[Status] Sent %s\n", r ? "OK" : r.GetErrorMessage());
}

// ── LED blink ────────────────────────────────────────────────────
void blink(uint8_t times, uint16_t ms) {
    for (uint8_t i = 0; i < times; i++) {
        digitalWrite(PIN_LED, HIGH);
        delay(ms);
        digitalWrite(PIN_LED, LOW);
        if (i < times - 1) delay(ms);
    }
}
