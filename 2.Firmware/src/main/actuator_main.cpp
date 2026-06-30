// actuator_main.cpp — AgriMeshAI Actuator Node
// Hardware: ESP32-S3 + SX1262 + 4-channel Relay
#include <Arduino.h>
#include "core/mesh.hpp"
#include "core/node.hpp"
#include "actuator/relay.hpp"
#include "actuator/safety.hpp"
#include "common/watchdog.hpp"
#include "common/blink.hpp"
#include "common/log.hpp"
#include "mesh_types.h"

static uint32_t last_announce_ms = 0;
static volatile uint8_t blink_request = 0;

static void send_ack(uint8_t relay_id, uint8_t state) {
    if (!mesh_is_ready()) return;
    RelayAck a; a.relay_id = relay_id; a.state = state;
    mesh_send(GATEWAY_LORA_ADDR,
              std::vector<uint8_t>((uint8_t*)&a, (uint8_t*)&a + sizeof(a)));
}

static void on_lora(AddressType src, const std::vector<uint8_t>& d) {
    if (d.empty() || src != GATEWAY_LORA_ADDR) return;
    switch (d[0]) {
    case MSG_RELAY_CMD: {
        if (d.size() < 7) break;
        uint8_t rid = d[1], cmd = d[2];
        if (rid >= 4) break;
        uint32_t dur = 0; memcpy(&dur, d.data() + 3, 4);
        if (dur > MAX_ON_DURATION_MS) dur = MAX_ON_DURATION_MS;
        switch (cmd) {
        case RELAY_ON:
            relay_set(rid, true);
            if (dur > 0) { relay_get(rid).auto_off_ms = dur; relay_get(rid).on_since = millis(); }
            break;
        case RELAY_OFF:
            relay_set(rid, false);
            relay_get(rid).auto_off_ms = 0;
            break;
        case RELAY_TOGGLE: {
            bool new_state = !relay_get_state(rid);
            relay_set(rid, new_state);
            if (new_state) {
                if (dur > 0) { relay_get(rid).auto_off_ms = dur; relay_get(rid).on_since = millis(); }
                else { relay_get(rid).auto_off_ms = 0; }
            } else { relay_get(rid).auto_off_ms = 0; }
        } break;
        }
        send_ack(rid, relay_get_state(rid) ? 1 : 0);
        blink_request = 1;
    } break;

    case MSG_PING: {
        if (!mesh_is_ready()) break;
        uint16_t uptime = (uint16_t)(millis() / 3600000);
        Pong pong; pong.uptime_hours = uptime;
        mesh_send(src, std::vector<uint8_t>((uint8_t*)&pong, (uint8_t*)&pong + sizeof(pong)));
    } break;
    }
}

void setup() {
    Serial.begin(115200); delay(1000);
    LOG_I("=== AgriMeshAI Actuator Node ===");
    blink_init(); blink(2, 100);
    watchdog_init();
    relay_init();
    mesh_init(on_lora);
    AddressType addr = node_init(mesh_get_address());
    LOG_I("Node addr=0x%04X", addr);
    last_announce_ms = millis();
    mesh_send_announce(NODE_TYPE_ACTUATOR);
}

void loop() {
    watchdog_reset();
    uint32_t now = millis();

    if (blink_request > 0) { blink_request = 0; blink(1, 20); }
    safety_check_all(send_ack);

    if ((int32_t)(now - last_announce_ms) >= (int32_t)ANNOUNCE_INTERVAL_MS) {
        mesh_send_announce(NODE_TYPE_ACTUATOR);
    }
    vTaskDelay(pdMS_TO_TICKS(100));
}
