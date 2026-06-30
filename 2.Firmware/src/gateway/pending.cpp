// pending.cpp
#include "gateway/pending.hpp"
#include <Arduino.h>

PendingReq g_pending[MAX_PENDING];

int pending_alloc() {
    for (int i = 0; i < MAX_PENDING; i++) {
        if (g_pending[i].state == PEND_IDLE) {
            g_pending[i] = {};
            return i;
        }
    }
    return -1;
}

void pending_free(int idx) {
    if (idx >= 0 && idx < MAX_PENDING) g_pending[idx].state = PEND_IDLE;
}

int pending_find(PendState state, AddressType addr) {
    for (int i = 0; i < MAX_PENDING; i++) {
        if (g_pending[i].state == state && g_pending[i].target_addr == addr) return i;
    }
    return -1;
}

void pending_reap(void (*send_error)(uint8_t, const char*, uint16_t)) {
    uint32_t now = millis();
    for (int i = 0; i < MAX_PENDING; i++) {
        if (g_pending[i].state != PEND_IDLE && (now - g_pending[i].started > PEND_TIMEOUT_MS)) {
            send_error(2, "timeout", g_pending[i].seq);
            g_pending[i].state = PEND_IDLE;
        }
    }
}
