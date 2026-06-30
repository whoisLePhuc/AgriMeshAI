// safety.hpp — Auto-off safety timer for relays.
// Prevents relays from staying ON indefinitely (max 30 min).
#pragma once

#include <Arduino.h>
#include "actuator/relay.hpp"
#include "mesh_types.h"
#include "common/log.hpp"

inline void safety_check_all(void (*send_ack)(uint8_t, uint8_t)) {
    uint32_t now = millis();
    for (int i = 0; i < RELAY_CHANNELS; i++) {
        RelayState& r = relay_get(i);
        if (!r.on || r.auto_off_ms == 0) continue;
        if ((int32_t)(now - r.on_since) >= (int32_t)r.auto_off_ms) {
            LOG_I("Safety: Relay %d auto-off after %lums", i, (unsigned long)r.auto_off_ms);
            relay_set(i, false);
            r.auto_off_ms = 0;
            if (send_ack) send_ack(i, 0);
        }
    }
}
