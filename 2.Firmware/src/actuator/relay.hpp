// relay.hpp — 4-channel relay control.
#pragma once

#include <Arduino.h>
#include <cstdint>
#include "common/log.hpp"

#define RELAY_CHANNELS 4

struct RelayState {
    bool     on;
    uint32_t on_since;
    uint32_t auto_off_ms;
};

void        relay_init();
void        relay_set(uint8_t idx, bool state);
bool        relay_get_state(uint8_t idx);
RelayState& relay_get(uint8_t idx);
