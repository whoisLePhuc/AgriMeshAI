// pending.hpp — Pending request slots for LoRa Gateway (max 4 concurrent).
#pragma once

#include <cstdint>
#include "mesh_types.h"
#include "loramesher.hpp"

using namespace loramesher;

#define MAX_PENDING 4
#define PEND_TIMEOUT_MS 5000

enum PendState : uint8_t {
    PEND_IDLE,
    PEND_PING,    // waiting for PONG (two-phase GET_TEMP phase 1)
    PEND_TEMP,    // waiting for TEMP_READING (two-phase GET_TEMP phase 2)
    PEND_ACK,     // waiting for RELAY_ACK (SET_RELAY)
    PEND_PONG,    // waiting for PONG (AT+PING)
};

struct PendingReq {
    uint16_t    seq;
    uint8_t     target_nid;
    AddressType target_addr;
    PendState   state;
    uint32_t    started;
};

int  pending_alloc();
void pending_free(int idx);
int  pending_find(PendState state, AddressType addr);
void pending_reap(void (*send_error)(uint8_t code, const char* msg, uint16_t seq));
extern PendingReq g_pending[MAX_PENDING];
