// actuator_main.cpp — AgriMeshAI Actuator Node
// Hardware: ESP32-S3 + SX1262 + Relay x4 (Heltec WiFi LoRa 32 V3)
//
// CHANGES vs original:
//   - Watchdog timer added (was missing, could leave relays stuck ON if firmware hangs)
//   - Periodic re-announce every ANNOUNCE_INTERVAL_MS (was single shot in setup())
//   - on_loRa no longer calls blink() — blink uses vTaskDelay which blocks the
//     LoRaMesher callback task; replaced with a volatile flag dispatched in loop()
//   - Wrap-around-safe interval arithmetic (int32_t cast) for all timers
//   - send_announce / send_ack guard against mesh_ready consistently

#include <Arduino.h>
#include <memory>
#include "esp_task_wdt.h"
#include "loramesher.hpp"
#include "mesh_types.h"

using namespace loramesher;

#define PIN_LED       35
#define WDT_TIMEOUT_S 30

static const int relay_pins[4] = {14, 15, 16, 17};

struct RelayState { bool on; uint32_t on_since; uint32_t auto_off_ms; };
static RelayState relays[4];

static std::unique_ptr<LoraMesher> mesher;
static AddressType my_addr   = 0;
static bool        mesh_ready = false;

static uint32_t last_announce_ms = 0;

// Blink request from callback: set flag, execute in loop()
static volatile uint8_t blink_request = 0;  // number of blinks pending

// ── Forward ───────────────────────────────────────────────────────
static void set_relay(uint8_t idx, bool state);
static void check_timers();
static void send_announce();
static void send_ack(uint8_t relay_id, uint8_t state);
static void blink(uint8_t times, uint16_t ms);

// ── LoRa receive — whitelist + dispatch ───────────────────────────
// No blocking calls here (no blink, no vTaskDelay).
static void on_loRa(AddressType src, const std::vector<uint8_t>& d) {
    if (d.empty() || src != GATEWAY_LORA_ADDR) return;

    switch (d[0]) {
    case MSG_RELAY_CMD: {
        if (d.size() < 7) break;
        uint8_t rid = d[1], cmd = d[2];
        if (rid >= 4) { Serial.printf("[Relay] ERROR: rid=%d out of bounds\n", rid); break; }
        uint32_t dur = 0; memcpy(&dur, d.data() + 3, 4);
        if (dur > MAX_ON_DURATION_MS) dur = MAX_ON_DURATION_MS;
        switch (cmd) {
        case RELAY_ON:
            set_relay(rid, true);
            if (dur > 0) { relays[rid].auto_off_ms = dur; relays[rid].on_since = millis(); }
            break;
        case RELAY_OFF:
            set_relay(rid, false);
            relays[rid].auto_off_ms = 0;
            break;
        case RELAY_TOGGLE: {
            bool new_state = !relays[rid].on;
            set_relay(rid, new_state);
            if (new_state) {
                if (dur > 0) { relays[rid].auto_off_ms = dur; relays[rid].on_since = millis(); }
                else          { relays[rid].auto_off_ms = 0; }
            } else {
                relays[rid].auto_off_ms = 0;
            }
        } break;
        }
        send_ack(rid, relays[rid].on ? 1 : 0);
        blink_request = 1;   // signal loop() to blink — no vTaskDelay in callback
    } break;

    case MSG_RELAY_SYNC:
        for (uint8_t i = 0; i < 4; i++) {
            send_ack(i, relays[i].on ? 1 : 0);
            vTaskDelay(pdMS_TO_TICKS(50));
        }
        break;

    case MSG_PING: {
        if (!mesh_ready) break;
        uint16_t uptime = (uint16_t)(millis() / 3600000);
        Pong pong; pong.uptime_hours = uptime;
        (void)mesher->Send(src,
            std::vector<uint8_t>((uint8_t*)&pong, (uint8_t*)&pong + sizeof(pong)));
    } break;
    }
}

// ── Setup ─────────────────────────────────────────────────────────
void setup() {
    Serial.begin(115200); delay(1000);
    Serial.println("\n=== AgriMeshAI Actuator Node ===");
    pinMode(PIN_LED, OUTPUT);
    blink(2, 100);

    // Watchdog — essential: a stuck relay is a hardware hazard
    esp_task_wdt_init(WDT_TIMEOUT_S, true);
    esp_task_wdt_add(NULL);
    Serial.printf("[WDT] enabled (%ds)\n", WDT_TIMEOUT_S);

    for (int i = 0; i < 4; i++) {
        pinMode(relay_pins[i], OUTPUT);
        digitalWrite(relay_pins[i], LOW);
    }
    Serial.println("[Relay] 4 channels OFF");

    PinConfig pc(8, 12, 14, 13, 9, 11, 10);
    RadioConfig rc(RadioType::kSx1262, 868.0F, 12, 125.0F, 7, 14);
    rc.setTcxoVoltage(1.8F);
    LoRaMeshProtocolConfig mc;
    mesher = LoraMesher::Builder()
                 .withRadioConfig(rc)
                 .withPinConfig(pc)
                 .withLoRaMeshProtocol(mc)
                 .Build();
    mesher->SetDataCallback(on_loRa);

    Result r = mesher->Start();
    if (!r) {
        Serial.printf("[LoRa] FAILED: %s\n", r.GetErrorMessage());
        delay(5000); esp_restart();
    }
    mesh_ready = true;
    my_addr    = mesher->GetNodeAddress();
    Serial.printf("[LoRa] Online addr=0x%04X\n", my_addr);

    last_announce_ms = millis();
    send_announce();
}

// ── Loop ──────────────────────────────────────────────────────────
void loop() {
    esp_task_wdt_reset();

    uint32_t now = millis();

    // Blink requested by callback (avoids vTaskDelay inside ISR context)
    if (blink_request > 0) {
        uint8_t n = blink_request;
        blink_request = 0;
        blink(n, 20);
    }

    check_timers();

    // Periodic re-announce so Gateway can re-register after its own reboot
    if ((int32_t)(now - last_announce_ms) >= (int32_t)ANNOUNCE_INTERVAL_MS) {
        send_announce();
    }

    vTaskDelay(pdMS_TO_TICKS(100));
}

// ── Relay control ─────────────────────────────────────────────────
static void set_relay(uint8_t idx, bool state) {
    if (idx >= 4) {
        Serial.printf("[Relay] ERROR: idx=%d out of bounds\n", idx);
        return;
    }
    digitalWrite(relay_pins[idx], state ? HIGH : LOW);
    relays[idx].on = state;
    Serial.printf("[Relay %d] %s\n", idx, state ? "ON" : "OFF");
}

// ── Auto-off timer ────────────────────────────────────────────────
static void check_timers() {
    uint32_t now = millis();
    for (int i = 0; i < 4; i++) {
        if (!relays[i].on || relays[i].auto_off_ms == 0) continue;
        // Wrap-around safe: cast to int32_t
        if ((int32_t)(now - relays[i].on_since) >= (int32_t)relays[i].auto_off_ms) {
            Serial.printf("[Safety] Relay %d auto-off after %lums\n",
                          i, (unsigned long)relays[i].auto_off_ms);
            set_relay(i, false);
            relays[i].auto_off_ms = 0;
            send_ack(i, 0);
        }
    }
}

// ── Send ANNOUNCE ─────────────────────────────────────────────────
static void send_announce() {
    if (!mesh_ready) return;
    Announce a; a.node_type = NODE_TYPE_ACTUATOR; a.fw_ver = 0x10;
    (void)mesher->Send(GATEWAY_LORA_ADDR,
        std::vector<uint8_t>((uint8_t*)&a, (uint8_t*)&a + sizeof(a)));
    last_announce_ms = millis();
    Serial.println("[Mesh] ANNOUNCE sent");
}

// ── Send ACK ─────────────────────────────────────────────────────
static void send_ack(uint8_t relay_id, uint8_t state) {
    if (!mesh_ready) return;
    RelayAck a; a.relay_id = relay_id; a.state = state;
    (void)mesher->Send(GATEWAY_LORA_ADDR,
        std::vector<uint8_t>((uint8_t*)&a, (uint8_t*)&a + sizeof(a)));
}

// ── LED blink ─────────────────────────────────────────────────────
static void blink(uint8_t times, uint16_t ms) {
    for (uint8_t i = 0; i < times; i++) {
        digitalWrite(PIN_LED, HIGH); vTaskDelay(pdMS_TO_TICKS(ms));
        digitalWrite(PIN_LED, LOW);
        if (i < times - 1) vTaskDelay(pdMS_TO_TICKS(ms));
    }
}