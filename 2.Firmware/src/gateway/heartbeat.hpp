// heartbeat.hpp — Heartbeat sweep for LoRa Gateway.
// Pings all actuator nodes periodically and reports response count via +HB.
#pragma once

#include <cstdint>
#include "mesh_types.h"
#include "core/mesh.hpp"

struct HBPending {
    uint16_t seq;
    uint8_t  count;
    uint8_t  responded;
    uint32_t started;
    bool     waiting;
};

void heartbeat_init();
void heartbeat_ping_all(void (*uart_send)(const char*));
void heartbeat_on_pong();
void heartbeat_reap(void (*uart_send)(const char*));
extern HBPending g_hb_pending;
extern uint32_t  g_last_hb_ms;
extern uint16_t  g_hb_seq;
