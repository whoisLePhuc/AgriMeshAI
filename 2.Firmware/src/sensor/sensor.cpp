// sensor.cpp
#include "sensor/sensor.hpp"

static DHT      s_dht(PIN_DHT, DHT22);
static uint16_t s_seq = 0;
static uint32_t s_last_read_ms = 0;

void sensor_init() {
    s_dht.begin();
    s_seq = 0;
    LOG_I("DHT22 started");
}

float sensor_read_temperature() {
    return s_dht.readTemperature();
}

float sensor_read_humidity() {
    return s_dht.readHumidity();
}

uint8_t sensor_get_seq() { return s_seq; }

void sensor_send_data() {
    uint32_t now = millis();
    if (now - s_last_read_ms < DHT_MIN_INTERVAL_MS) {
        LOG_D("DHT22 read skipped (too soon)");
        return;
    }
    s_last_read_ms = now;

    float temp = s_dht.readTemperature();
    float hum  = s_dht.readHumidity();

    if (isnan(temp) && isnan(hum)) {
        LOG_E("DHT22 read failed");
        return;
    }

    s_seq++;

    if (!isnan(temp)) {
        SensorReading tr;
        tr.sensor_id = SENSOR_TEMPERATURE;
        tr.value     = temp;
        tr.seq       = s_seq;
        tr.timestamp = (uint32_t)(now / 1000);
        mesh_send(GATEWAY_LORA_ADDR,
                  std::vector<uint8_t>((uint8_t*)&tr, (uint8_t*)&tr + sizeof(tr)));
        LOG_I("Temp=%.1f°C seq=%d", temp, s_seq);
    }
    if (!isnan(hum)) {
        SensorReading tr;
        tr.sensor_id = SENSOR_HUMIDITY;
        tr.value     = hum;
        tr.seq       = s_seq;
        tr.timestamp = (uint32_t)(now / 1000);
        mesh_send(GATEWAY_LORA_ADDR,
                  std::vector<uint8_t>((uint8_t*)&tr, (uint8_t*)&tr + sizeof(tr)));
        LOG_I("Hum=%.0f%% seq=%d", hum, s_seq);
    }
}
