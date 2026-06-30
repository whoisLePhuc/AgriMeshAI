// sensor_main.cpp — AgriMeshAI Sensor Node
// Hardware: ESP32-S3 + SX1262 + DHT22
#include <Arduino.h>
#include "core/mesh.hpp"
#include "core/node.hpp"
#include "sensor/sensor.hpp"
#include "common/watchdog.hpp"
#include "common/blink.hpp"
#include "common/log.hpp"
#include "mesh_types.h"

static uint32_t last_send_ms = 0;
static uint32_t last_announce_ms = 0;

static void on_lora(AddressType src, const std::vector<uint8_t>& d) {
    if (d.empty()) return;
    switch (d[0]) {
    case MSG_PING:
        portENTER_CRITICAL_ISR(&g_ping_mux);
        g_ping_src = src;
        g_flag_ping_valid = true;
        portEXIT_CRITICAL_ISR(&g_ping_mux);
        break;
    }
}

void setup() {
    Serial.begin(115200); delay(1000);
    LOG_I("=== AgriMeshAI Sensor Node ===");
    blink_init(); blink(2, 100);
    watchdog_init();
    sensor_init();
    mesh_init(on_lora);
    AddressType addr = node_init(mesh_get_address());
    LOG_I("Node addr=0x%04X", addr);
    last_send_ms = millis();
    last_announce_ms = millis();
    mesh_send_announce(NODE_TYPE_SENSOR);
}

void loop() {
    watchdog_reset();
    uint32_t now = millis();

    // Pong handling
    AddressType pong_dst = 0;
    if (mesh_handle_ping(pong_dst)) {
        uint16_t uptime = (uint16_t)(now / 3600000);
        Pong pong; pong.uptime_hours = uptime;
        mesh_send(pong_dst, std::vector<uint8_t>((uint8_t*)&pong, (uint8_t*)&pong + sizeof(pong)));
    }

    // Periodic sensor push (60s)
    if ((int32_t)(now - last_send_ms) >= (int32_t)SENSOR_PUSH_INTERVAL_MS) {
        last_send_ms = now;
        sensor_send_data();
        blink(1, 20);
    }

    // Periodic re-announce (10 min)
    if ((int32_t)(now - last_announce_ms) >= (int32_t)ANNOUNCE_INTERVAL_MS) {
        mesh_send_announce(NODE_TYPE_SENSOR);
    }

    mesh_flush_retry_queue();
    vTaskDelay(pdMS_TO_TICKS(100));
}
