// sensor_main.cpp — AgriMeshAI Sensor Node
// Hardware: ESP32-S3 + SX1262 + DHT22 (Heltec WiFi LoRa 32 V3)
//
// CHANGES vs original:
//   - ping_src protected by portENTER/EXIT_CRITICAL (was raw volatile — unsafe on dual-core)
//   - PendingPacket carries destination address; flush_retry_queue sends to correct dst
//   - Millis wrap-around guard fixed (the original double-assignment made it a no-op)
//   - ANNOUNCE_INTERVAL_MS sourced from mesh_types.h (removed local duplicate)
//   - Minor: LOG macros unchanged; WDT, NVS, DHT guard all preserved

#include <Arduino.h>
#include <memory>
#include <deque>
#include <DHT.h>
#include <Preferences.h>
#include "esp_task_wdt.h"
#include "loramesher.hpp"
#include "mesh_types.h"

using namespace loramesher;

// ── Config ───────────────────────────────────────────────────────
#define PIN_DHT              6
#define PIN_LED             35
#define WDT_TIMEOUT_S       30
#define DHT_MIN_INTERVAL_MS 2100
#define RETRY_QUEUE_MAX     20

// Log levels: 0=off 1=error 2=warn 3=info 4=debug
#ifndef LOG_LEVEL
  #define LOG_LEVEL 3
#endif
#define LOG_E(fmt,...) if(LOG_LEVEL>=1) Serial.printf("[ERR] " fmt "\n", ##__VA_ARGS__)
#define LOG_W(fmt,...) if(LOG_LEVEL>=2) Serial.printf("[WRN] " fmt "\n", ##__VA_ARGS__)
#define LOG_I(fmt,...) if(LOG_LEVEL>=3) Serial.printf("[INF] " fmt "\n", ##__VA_ARGS__)
#define LOG_D(fmt,...) if(LOG_LEVEL>=4) Serial.printf("[DBG] " fmt "\n", ##__VA_ARGS__)

// ── State ─────────────────────────────────────────────────────────
static std::unique_ptr<LoraMesher> mesher;
static Preferences                 prefs;
static DHT                         dht(PIN_DHT, DHT22);

static AddressType  my_addr          = 0;
static uint16_t     seq_num          = 0;

static uint32_t last_send_ms         = 0;
static uint32_t last_dht_read_ms     = 0;
static uint32_t last_announce_ms     = 0;

// Retry queue — each packet carries its destination address
struct PendingPacket {
    AddressType          dst;
    std::vector<uint8_t> payload;
    uint8_t              retries;
};
static std::deque<PendingPacket> retry_queue;
static portMUX_TYPE              queue_mux = portMUX_INITIALIZER_UNLOCKED;

// Ping flag — protected by a dedicated spinlock (volatile alone is not
// sufficient for atomic read-modify on ESP32 dual-core Xtensa).
static portMUX_TYPE          ping_mux       = portMUX_INITIALIZER_UNLOCKED;
static bool                  flag_ping_valid = false;
static AddressType           ping_src        = 0;

static volatile bool flag_send_now = false;

// ── Helpers ───────────────────────────────────────────────────────
static void blink(uint8_t times, uint16_t ms) {
    for (uint8_t i = 0; i < times; i++) {
        digitalWrite(PIN_LED, HIGH); delay(ms);
        digitalWrite(PIN_LED, LOW);
        if (i < times - 1) delay(ms);
    }
}

// safe_send — queues on failure; preserves destination address
static void safe_send(AddressType dst, std::vector<uint8_t> payload) {
    Result r = mesher->Send(dst, payload);
    if (!r) {
        LOG_W("Send failed (%s), queuing (size=%d)", r.GetErrorMessage(), (int)retry_queue.size());
        portENTER_CRITICAL(&queue_mux);
        if ((int)retry_queue.size() < RETRY_QUEUE_MAX) {
            retry_queue.push_back({dst, std::move(payload), 0});
        } else {
            LOG_E("Retry queue full, packet dropped");
        }
        portEXIT_CRITICAL(&queue_mux);
    }
}

// flush_retry_queue — sends to the original destination stored with each packet
static void flush_retry_queue() {
    portENTER_CRITICAL(&queue_mux);
    if (retry_queue.empty()) { portEXIT_CRITICAL(&queue_mux); return; }
    PendingPacket pkt = retry_queue.front();
    retry_queue.pop_front();
    portEXIT_CRITICAL(&queue_mux);

    Result r = mesher->Send(pkt.dst, pkt.payload);
    if (!r) {
        pkt.retries++;
        if (pkt.retries < (uint8_t)MAX_RETRIES) {
            portENTER_CRITICAL(&queue_mux);
            retry_queue.push_front(std::move(pkt));
            portEXIT_CRITICAL(&queue_mux);
            LOG_D("Retry #%d failed, re-queued", pkt.retries);
        } else {
            LOG_E("Packet dropped after %d retries", MAX_RETRIES);
        }
    } else {
        LOG_D("Queued packet delivered to 0x%04X", pkt.dst);
    }
}

// ── NVS: persist node address ─────────────────────────────────────
static AddressType load_or_save_addr(AddressType live_addr) {
    prefs.begin("mesh", false);
    uint16_t saved = prefs.getUShort("addr", 0);
    if (saved == 0) {
        prefs.putUShort("addr", (uint16_t)live_addr);
        saved = (uint16_t)live_addr;
        LOG_I("NVS: new addr saved 0x%04X", saved);
    } else if (saved != (uint16_t)live_addr) {
        LOG_W("NVS addr 0x%04X != live 0x%04X, using NVS", saved, (uint16_t)live_addr);
    }
    prefs.end();
    return (AddressType)saved;
}

// ── Announce ──────────────────────────────────────────────────────
static void send_announce() {
    Announce a; a.node_type = NODE_TYPE_SENSOR; a.fw_ver = 0x10;
    safe_send(GATEWAY_LORA_ADDR,
              std::vector<uint8_t>((uint8_t*)&a, (uint8_t*)&a + sizeof(a)));
    last_announce_ms = millis();
    LOG_I("ANNOUNCE sent");
}

// ── DHT22 + send ─────────────────────────────────────────────────
static void send_sensor_data() {
    uint32_t now = millis();

    if (now - last_dht_read_ms < DHT_MIN_INTERVAL_MS) {
        LOG_D("DHT22 read skipped (too soon)");
        return;
    }
    last_dht_read_ms = now;

    float temp = dht.readTemperature();
    float hum  = dht.readHumidity();

    if (isnan(temp) && isnan(hum)) {
        LOG_E("DHT22 read failed");
        return;
    }

    seq_num++;

    if (!isnan(temp)) {
        SensorReading tr;
        tr.sensor_id = SENSOR_TEMPERATURE;
        tr.value     = temp;
        tr.seq       = seq_num;
        tr.timestamp = (uint32_t)(now / 1000);
        safe_send(GATEWAY_LORA_ADDR,
                  std::vector<uint8_t>((uint8_t*)&tr, (uint8_t*)&tr + sizeof(tr)));
        LOG_I("Temp=%.1f°C seq=%d", temp, seq_num);
    }
    if (!isnan(hum)) {
        SensorReading tr;
        tr.sensor_id = SENSOR_HUMIDITY;
        tr.value     = hum;
        tr.seq       = seq_num;
        tr.timestamp = (uint32_t)(now / 1000);
        safe_send(GATEWAY_LORA_ADDR,
                  std::vector<uint8_t>((uint8_t*)&tr, (uint8_t*)&tr + sizeof(tr)));
        LOG_I("Hum=%.0f%% seq=%d", hum, seq_num);
    }
    blink(1, 20);
}

// ── LoRa receive callback ─────────────────────────────────────────
// Runs inside LoRaMesher RTOS task — only set flags, no blocking I/O.
static void on_loRa(AddressType src, const std::vector<uint8_t>& d) {
    if (d.empty()) return;
    switch (d[0]) {
    case MSG_PING:
        // Use spinlock to safely write both fields atomically
        portENTER_CRITICAL_ISR(&ping_mux);
        ping_src        = src;
        flag_ping_valid = true;
        portEXIT_CRITICAL_ISR(&ping_mux);
        break;
    case 0xFF:
        flag_send_now = true;
        break;
    default:
        break;
    }
}

// ── Setup ─────────────────────────────────────────────────────────
void setup() {
    Serial.begin(115200); delay(1000);
    LOG_I("=== AgriMeshAI Sensor Node ===");
    pinMode(PIN_LED, OUTPUT);
    blink(2, 100);

    esp_task_wdt_init(WDT_TIMEOUT_S, true);
    esp_task_wdt_add(NULL);
    LOG_I("WDT enabled (%ds)", WDT_TIMEOUT_S);

    dht.begin();
    LOG_I("DHT22 started");

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
        LOG_E("LoRa FAILED: %s", r.GetErrorMessage());
        delay(5000); esp_restart();
    }

    my_addr = load_or_save_addr(mesher->GetNodeAddress());
    LOG_I("LoRa online addr=0x%04X", my_addr);

    last_send_ms     = millis();
    last_announce_ms = millis();
    send_announce();
}

// ── Loop ──────────────────────────────────────────────────────────
void loop() {
    esp_task_wdt_reset();

    uint32_t now = millis();

    // Handle ping flag — read atomically with spinlock
    bool do_ping = false;
    AddressType pong_dst = 0;
    portENTER_CRITICAL(&ping_mux);
    if (flag_ping_valid) {
        do_ping         = true;
        pong_dst        = ping_src;
        flag_ping_valid = false;
    }
    portEXIT_CRITICAL(&ping_mux);

    if (do_ping) {
        uint16_t uptime = (uint16_t)(now / 3600000);
        Pong pong; pong.uptime_hours = uptime;
        safe_send(pong_dst,
                  std::vector<uint8_t>((uint8_t*)&pong, (uint8_t*)&pong + sizeof(pong)));
        LOG_D("PONG sent to 0x%04X uptime=%dh", pong_dst, uptime);
    }

    if (flag_send_now) {
        flag_send_now = false;
        send_sensor_data();
    }

    // Periodic sensor push — correct wrap-around handling:
    // Cast to int32_t so subtraction wraps naturally after ~49 days.
    if ((int32_t)(now - last_send_ms) >= (int32_t)SENSOR_PUSH_INTERVAL_MS) {
        last_send_ms = now;
        send_sensor_data();
    }

    // Periodic re-announce (gateway may have rebooted)
    if ((int32_t)(now - last_announce_ms) >= (int32_t)ANNOUNCE_INTERVAL_MS) {
        send_announce();
    }

    // Flush one retry packet per loop iteration
    flush_retry_queue();

    vTaskDelay(pdMS_TO_TICKS(100));
}