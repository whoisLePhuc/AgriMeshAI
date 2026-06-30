// lora_gateway_main.cpp — AgriMeshAI LoRa Gateway
// UART ↔ LoRa Mesh Bridge. Hardware: ESP32-S3 + SX1262
#include <Arduino.h>
#include "core/mesh.hpp"
#include "gateway/pending.hpp"
#include "gateway/node_table.hpp"
#include "gateway/heartbeat.hpp"
#include "ringbuf.h"
#include "at_protocol.h"
#include "common/log.hpp"

static RingBuf uart_rx;
static uint32_t last_tx_ms = 0;
static uint16_t node_id_counter = 1;
static char line_buf[256];

static void uart_send(const char* buf) {
    if (!buf || !*buf) return;
    uint32_t now = millis();
    if (now - last_tx_ms < 5) vTaskDelay(pdMS_TO_TICKS(5 - (now - last_tx_ms)));
    Serial.print(buf);
    last_tx_ms = millis();
}

static void send_error(uint8_t code, const char* msg, uint16_t seq) {
    char buf[64];
    int n = at_fmt_error(buf, sizeof(buf), code, msg, seq);
    if (n > 0) uart_send(buf);
}

static void on_table_full(AddressType addr) {
    char buf[64];
    snprintf(buf, sizeof(buf), "+ERR:4,table full addr=0x%04X\r\n", addr);
    uart_send(buf);
}

static void handle_command(const char*);
static void on_lora_data(AddressType src, const std::vector<uint8_t>& d);

static uint16_t parse_seq(const char* line) {
    const char* p = line;
    while (*p) {
        if (p[0] == 'S' && p[1] == 'E' && p[2] == 'Q' && p[3] == '=') {
            p += 4;
            if (*p < '0' || *p > '9') return 0;
            uint16_t seq = 0;
            while (*p >= '0' && *p <= '9') seq = (uint16_t)(seq * 10 + (*p++ - '0'));
            return seq;
        }
        p++;
    }
    return 0;
}

void setup() {
    Serial.begin(115200); delay(1000);
    LOG_I("=== AgriMeshAI LoRa Gateway ===");

    PinConfig pc(8, 12, 14, 13, 9, 11, 10);
    RadioConfig rc(RadioType::kSx1262, 868.0F, 12, 125.0F, 7, 14);
    rc.setTcxoVoltage(1.8F);
    LoRaMeshProtocolConfig mc;
    g_mesher = LoraMesher::Builder()
                   .withRadioConfig(rc)
                   .withPinConfig(pc)
                   .withLoRaMeshProtocol(mc)
                   .withNodeAddress(GATEWAY_LORA_ADDR)
                   .withNodeCapabilities(NodeCapabilities::GATEWAY)
                   .Build();
    g_mesher->SetDataCallback(on_lora_data);
    Result r = g_mesher->Start();
    if (r) LOG_I("LoRa OK addr=0x%04X", g_mesher->GetNodeAddress());
    else LOG_E("LoRa FAILED: %s", r.GetErrorMessage());

    g_mesh_ready = true;
    heartbeat_init();
    LOG_I("=== Ready ===");
}

void serialEvent() {
    while (Serial.available()) uart_rx.push((uint8_t)Serial.read());
}

void loop() {
    int len = uart_rx.read_line(line_buf, sizeof(line_buf));
    if (len > 0) handle_command(line_buf);

    pending_reap(send_error);
    heartbeat_ping_all(uart_send);
    heartbeat_reap(uart_send);
    vTaskDelay(pdMS_TO_TICKS(10));
}

// ── AT command dispatch ──────────────────────────────────────────
static void handle_command(const char* line) {
    char buf[128];
    uint8_t node_id = 0, relay_id = 0, cmd = 0;
    uint16_t seq = 0;
    uint32_t duration = 0;

    if (at_starts_with(line, AT_GET_TEMP)) {
        if (at_parse_id_seq(line, node_id, seq) != 0) { send_error(3, "bad fmt", seq); return; }
        if (!g_mesh_ready) { send_error(5, "not ready", seq); return; }
        int idx = table_find_by_id(node_id);
        if (idx < 0) { send_error(1, "not found", seq); return; }
        int p = pending_alloc();
        if (p < 0) { send_error(4, "pending full", seq); return; }
        g_pending[p] = {seq, node_id, g_node_table[idx].lora_addr, PEND_PING, millis()};
        uint8_t ping = MSG_PING;
        (void)g_mesher->Send(g_node_table[idx].lora_addr, std::vector<uint8_t>(&ping, &ping + 1));
        return;
    }

    if (at_starts_with(line, AT_SET_RELAY)) {
        if (at_parse_relay(line, node_id, relay_id, cmd, duration, seq) != 0) { send_error(3, "bad fmt", seq); return; }
        if (relay_id >= 4) { send_error(3, "relay_id 0-3", seq); return; }
        if (!g_mesh_ready) { send_error(5, "not ready", seq); return; }
        int idx = table_find_by_id(node_id);
        if (idx < 0) { send_error(1, "not found", seq); return; }
        if (duration > MAX_ON_DURATION_MS) duration = MAX_ON_DURATION_MS;
        int p = pending_alloc();
        if (p < 0) { send_error(4, "pending full", seq); return; }
        g_pending[p] = {seq, node_id, g_node_table[idx].lora_addr, PEND_ACK, millis()};
        RelayCmdPacket rp; rp.relay_id = relay_id; rp.cmd = cmd; rp.duration_ms = duration;
        (void)g_mesher->Send(g_node_table[idx].lora_addr,
            std::vector<uint8_t>((uint8_t*)&rp, (uint8_t*)&rp + sizeof(rp)));
        return;
    }

    if (at_starts_with(line, AT_PING) && !at_starts_with(line, AT_PING_ALL)) {
        if (at_parse_id_seq(line, node_id, seq) != 0) { send_error(3, "bad fmt", seq); return; }
        if (!g_mesh_ready) { send_error(5, "not ready", seq); return; }
        int idx = table_find_by_id(node_id);
        if (idx < 0) { send_error(1, "not found", seq); return; }
        int p = pending_alloc();
        if (p < 0) { send_error(4, "pending full", seq); return; }
        g_pending[p] = {seq, node_id, g_node_table[idx].lora_addr, PEND_PONG, millis()};
        uint8_t ping = MSG_PING;
        (void)g_mesher->Send(g_node_table[idx].lora_addr, std::vector<uint8_t>(&ping, &ping + 1));
        return;
    }

    if (at_starts_with(line, AT_PING_ALL)) {
        seq = parse_seq(line);
        if (!g_mesh_ready) { send_error(5, "not ready", seq); return; }
        uint8_t cnt = 0;
        for (uint8_t i = 0; i < (uint8_t)table_get_count(); i++) {
            if (g_node_table[i].active && g_node_table[i].node_type == NODE_TYPE_ACTUATOR) {
                uint8_t ping = MSG_PING;
                (void)g_mesher->Send(g_node_table[i].lora_addr, std::vector<uint8_t>(&ping, &ping + 1));
                cnt++;
            }
        }
        if (cnt > 0) {
            g_hb_pending.seq = seq; g_hb_pending.count = cnt;
            g_hb_pending.responded = 0; g_hb_pending.started = millis();
            g_hb_pending.waiting = true;
        } else {
            snprintf(buf, sizeof(buf), "+HB:0/0,SEQ=%d\r\n", seq); uart_send(buf);
        }
        return;
    }

    if (at_starts_with(line, AT_LIST_NODES)) {
        seq = parse_seq(line);
        int pos = snprintf(buf, sizeof(buf), "+NODES:%d", table_get_count());
        for (uint8_t i = 0; i < (uint8_t)table_get_count(); i++) {
            pos += snprintf(buf + pos, sizeof(buf) - pos, ",%d,%d",
                           g_node_table[i].node_id, g_node_table[i].node_type);
        }
        pos += snprintf(buf + pos, sizeof(buf) - pos, ",SEQ=%d\r\n", seq);
        uart_send(buf);
        return;
    }

    if (at_starts_with(line, AT_NODE_INIT)) {
        seq = parse_seq(line);
        snprintf(buf, sizeof(buf), "+NODE_INIT:OK,SEQ=%d\r\n", seq);
        uart_send(buf); return;
    }

    if (at_starts_with(line, AT_NODE_ACK)) {
        uint16_t lora_addr = 0; uint8_t new_id = 0;
        if (sscanf(line, "AT+NODE_ACK=%hx,%hhu", &lora_addr, &new_id) >= 2) {
            seq = parse_seq(line);
            bool found = false;
            for (uint8_t i = 0; i < (uint8_t)table_get_count(); i++) {
                if (g_node_table[i].lora_addr == lora_addr) {
                    g_node_table[i].node_id = new_id; found = true; break;
                }
            }
            if (found) { snprintf(buf, sizeof(buf), "+NODE_ACK:OK,SEQ=%d\r\n", seq); uart_send(buf); }
            else { send_error(1, "unknown addr", seq); }
        } else { send_error(3, "bad fmt", parse_seq(line)); }
        return;
    }
}

// ── LoRa receive ────────────────────────────────────────────────
static void on_lora_data(AddressType src, const std::vector<uint8_t>& d) {
    if (d.empty()) return;
    uint8_t type = d[0];
    char buf[128];

    switch (type) {
    case MSG_SENSOR_DATA: {
        if (d.size() < sizeof(SensorReading)) break;
        const SensorReading* tr = (const SensorReading*)d.data();
        int pi = pending_find(PEND_TEMP, src);
        if (pi >= 0) {
            uint8_t nid = g_pending[pi].target_nid;
            at_fmt_temp(buf, sizeof(buf), nid, tr->sensor_id, tr->value, g_pending[pi].seq);
            uart_send(buf); pending_free(pi);
        } else {
            int idx = table_find_or_add(src, NODE_TYPE_SENSOR, on_table_full);
            if (idx >= 0) {
                at_fmt_temp_report(buf, sizeof(buf), g_node_table[idx].node_id, tr->sensor_id, tr->value);
                uart_send(buf);
            }
        }
        break;
    }
    case MSG_ANNOUNCE: {
        if (d.size() < 3) break;
        uint8_t ntype = d[1], fw_ver = d[2];
        int idx = table_find_or_add(src, ntype, on_table_full);
        if (idx < 0) break;
        g_node_table[idx].active = true;
        if (g_node_table[idx].node_id == 0) g_node_table[idx].node_id = node_id_counter++;
        at_fmt_node_join(buf, sizeof(buf), src, ntype, fw_ver);
        uart_send(buf);
        bool is_rejoin = (ntype == NODE_TYPE_ACTUATOR);
        if (is_rejoin) {
            uint8_t sync = MSG_RELAY_SYNC;
            (void)g_mesher->Send(src, std::vector<uint8_t>(&sync, &sync + 1));
        }
        break;
    }
    case MSG_RELAY_ACK: {
        if (d.size() < 3) break;
        uint8_t relay_id = d[1], state = d[2];
        int pi = pending_find(PEND_ACK, src);
        if (pi >= 0) {
            at_fmt_relay_ack(buf, sizeof(buf), g_pending[pi].target_nid, relay_id, state, g_pending[pi].seq);
            uart_send(buf); pending_free(pi);
        } else {
            int idx = table_find_or_add(src, NODE_TYPE_ACTUATOR, on_table_full);
            if (idx >= 0) {
                at_fmt_relay_report(buf, sizeof(buf), g_node_table[idx].node_id, relay_id, state);
                uart_send(buf);
            }
        }
        break;
    }
    case MSG_PONG: {
        int pi;
        if ((pi = pending_find(PEND_PING, src)) >= 0) {
            g_pending[pi].state = PEND_TEMP;
            g_pending[pi].started = millis();
            uint8_t req = 0xFF;
            (void)g_mesher->Send(src, std::vector<uint8_t>(&req, &req + 1));
        } else if ((pi = pending_find(PEND_PONG, src)) >= 0) {
            int idx = table_find_or_add(src, 0, on_table_full);
            if (idx >= 0) {
                at_fmt_pong(buf, sizeof(buf), g_node_table[idx].node_id, g_pending[pi].seq);
                uart_send(buf); pending_free(pi);
            }
        } else if (g_hb_pending.waiting) {
            g_hb_pending.responded++;
        }
        break;
    }
    }
}
