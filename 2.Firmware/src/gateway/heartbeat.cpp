// heartbeat.cpp
#include "gateway/heartbeat.hpp"
#include "gateway/node_table.hpp"
#include "common/log.hpp"
#include <Arduino.h>

HBPending g_hb_pending;
uint32_t  g_last_hb_ms = 0;
uint16_t  g_hb_seq = 1;

void heartbeat_init() {
    g_last_hb_ms = millis();
    g_hb_pending.waiting = false;
}

void heartbeat_ping_all(void (*uart_send)(const char*)) {
    uint32_t now = millis();
    if (!mesh_is_ready() || (now - g_last_hb_ms < HEARTBEAT_INTERVAL_MS)) return;

    g_last_hb_ms = now;
    uint8_t cnt = 0;

    for (uint8_t i = 0; i < (uint8_t)table_get_count(); i++) {
        extern NodeEntry g_node_table[MAX_NODES];
        if (g_node_table[i].active && g_node_table[i].node_type == NODE_TYPE_ACTUATOR) {
            uint8_t ping = MSG_PING;
            (void)g_mesher->Send(g_node_table[i].lora_addr,
                                 std::vector<uint8_t>(&ping, &ping + 1));
            cnt++;
        }
    }
    if (cnt > 0) {
        g_hb_pending.seq       = g_hb_seq++;
        g_hb_pending.count     = cnt;
        g_hb_pending.responded = 0;
        g_hb_pending.started   = now;
        g_hb_pending.waiting   = true;
    }
}

void heartbeat_on_pong() {
    if (g_hb_pending.waiting) {
        g_hb_pending.responded++;
    }
}

void heartbeat_reap(void (*uart_send)(const char*)) {
    if (!g_hb_pending.waiting) return;
    uint32_t now = millis();
    if (now - g_hb_pending.started <= 5000) return;  // PEND_TIMEOUT_MS

    char buf[64];
    snprintf(buf, sizeof(buf), "+HB:%d/%d,SEQ=%d\r\n",
             g_hb_pending.responded, g_hb_pending.count, g_hb_pending.seq);
    uart_send(buf);
    g_hb_pending.waiting = false;
}
