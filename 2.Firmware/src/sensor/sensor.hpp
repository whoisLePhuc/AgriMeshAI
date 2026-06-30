// sensor.hpp — Sensor reading abstraction (DHT22, BH1750).
#pragma once

#include <Arduino.h>
#include <DHT.h>
#include "mesh_types.h"
#include "core/mesh.hpp"
#include "common/log.hpp"

#define PIN_DHT 6
#define DHT_MIN_INTERVAL_MS 2100

void    sensor_init();
float   sensor_read_temperature();
float   sensor_read_humidity();
uint8_t sensor_get_seq();
void    sensor_send_data();
