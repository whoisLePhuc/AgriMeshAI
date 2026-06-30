// relay.cpp
#include "actuator/relay.hpp"

static const int s_relay_pins[RELAY_CHANNELS] = {14, 15, 16, 17};
static RelayState s_relays[RELAY_CHANNELS];

void relay_init() {
    for (int i = 0; i < RELAY_CHANNELS; i++) {
        pinMode(s_relay_pins[i], OUTPUT);
        digitalWrite(s_relay_pins[i], LOW);
        s_relays[i] = {false, 0, 0};
    }
    LOG_I("Relay: 4 channels OFF");
}

void relay_set(uint8_t idx, bool state) {
    if (idx >= RELAY_CHANNELS) {
        LOG_E("Relay idx=%d out of bounds", idx);
        return;
    }
    digitalWrite(s_relay_pins[idx], state ? HIGH : LOW);
    s_relays[idx].on = state;
    LOG_I("Relay %d: %s", idx, state ? "ON" : "OFF");
}

bool relay_get_state(uint8_t idx) {
    if (idx >= RELAY_CHANNELS) return false;
    return s_relays[idx].on;
}

RelayState& relay_get(uint8_t idx) {
    return s_relays[idx];
}
