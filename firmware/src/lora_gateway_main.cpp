// lora_gateway_main.cpp — AgriMeshAI LoRa Gateway Phase 2 (v3)
// UART ↔ LoRa Mesh Bridge. Hardware: ESP32-S3 + SX1262
//
// Architecture:
//   UART (AT commands) ←→ AT dispatcher ←→ LoRa mesh bridge
//   ISR-based UART RX (ringbuf) + main-loop line reading
//   Pending request slots (4 max) with state+addr matching
//   Heartbeat sweep tracks per-actuator PONG responses

#include <Arduino.h>
#include <memory>
#include "loramesher.hpp"
#include "mesh_types.h"
#include "at_protocol.h"
#include "ringbuf.h"

using namespace loramesher;

// ── Constants ────────────────────────────────────────────────────

#define MAX_NODES        20
#define MAX_PENDING      4
#define UART_TX_GAP_MS   5
#define PEND_TIMEOUT_MS  5000

// ── Types ────────────────────────────────────────────────────────

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

struct HBPending {
    uint16_t seq;
    uint8_t  count;       // actuators pinged
    uint8_t  responded;   // PONGs received
    uint32_t started;
    bool     waiting;
};

struct NodeEntry {
    AddressType lora_addr;
    uint8_t     node_id;
    uint8_t     node_type;
    bool        active;
};

// ── Globals ──────────────────────────────────────────────────────

static RingBuf                     uart_rx;
static std::unique_ptr<LoraMesher> mesher;
static NodeEntry                   node_table[MAX_NODES];
static uint8_t                     node_count;
static PendingReq                  pending[MAX_PENDING];
static HBPending                   hb_pending;
static uint32_t                    last_hb_ms;
static uint32_t                    last_tx_ms;
static uint16_t                    hb_seq = 1;

// ── Forward declarations ─────────────────────────────────────────

static void setup_loRa();
static void handle_command(const char* line);
static void on_loRa_data(AddressType src, const std::vector<uint8_t>& d);
static int  find_or_add_node(AddressType addr, uint8_t ntype);
static int  find_node_by_id(uint8_t id);
static int  alloc_pending();
static void free_pending(int idx);
static int  find_pending(PendState state, AddressType addr);
static void uart_send(const char* buf);
static void reap_pending();
static void send_error(uint8_t code, const char* msg, uint16_t seq);
static uint16_t parse_seq(const char* line);

// ══════════════════════════════════════════════════════════════════
// Helpers
// ══════════════════════════════════════════════════════════════════

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

static void send_error(uint8_t code, const char* msg, uint16_t seq) {
    char buf[64];
    int n = at_fmt_error(buf, sizeof(buf), code, msg, seq);
    if (n > 0) {
        uart_send(buf);
    } else {
        // fallback: minimal guaranteed-valid response
        snprintf(buf, sizeof(buf), "+ERR:%d,SEQ=%d\r\n", code, seq);
        uart_send(buf);
    }
}

// ══════════════════════════════════════════════════════════════════
// LoRa receive — dispatch by message type
// ══════════════════════════════════════════════════════════════════

static void on_loRa_data(AddressType src, const std::vector<uint8_t>& d) {
    if (d.empty()) return;
    uint8_t type = d[0];
    char buf[128];

    switch (type) {

    // ── Sensor data ──────────────────────────────────────────────
    case MSG_SENSOR_DATA: {
        if (d.size() < sizeof(SensorReading)) break;
        const SensorReading* tr = (const SensorReading*)d.data();

        int pi = find_pending(PEND_TEMP, src);
        if (pi >= 0) {
            uint8_t nid = pending[pi].target_nid;
            int n = at_fmt_temp(buf, sizeof(buf), nid, tr->sensor_id, tr->value, pending[pi].seq);
            if (n > 0) uart_send(buf);
            free_pending(pi);
        } else {
            int idx = find_or_add_node(src, NODE_TYPE_SENSOR);
            if (idx >= 0) {
                int n = at_fmt_temp_report(buf, sizeof(buf), node_table[idx].node_id, tr->sensor_id, tr->value);
                if (n > 0) uart_send(buf);
            }
        }
        break;
    }

    // ── Announce (join / rejoin) ─────────────────────────────────
    case MSG_ANNOUNCE: {
        if (d.size() < 3) break;
        uint8_t ntype   = d[1];
        uint8_t fw_ver  = d[2];
        int idx = find_or_add_node(src, ntype);
        if (idx < 0) break;

        node_table[idx].active = true;
        int n = at_fmt_node_join(buf, sizeof(buf), src, ntype, fw_ver);
        if (n > 0) uart_send(buf);

        // RELAY_SYNC only on rejoin (node already known to Edge)
        bool is_rejoin = (ntype == NODE_TYPE_ACTUATOR && node_table[idx].node_id != 0);
        if (is_rejoin && mesher) {
            uint8_t sync = MSG_RELAY_SYNC;
            (void)mesher->Send(src, std::vector<uint8_t>(&sync, &sync + 1));
        }
        break;
    }

    // ── Relay ACK ────────────────────────────────────────────────
    case MSG_RELAY_ACK: {
        if (d.size() < 3) break;
        uint8_t relay_id = d[1];
        uint8_t state    = d[2];

        int pi = find_pending(PEND_ACK, src);
        if (pi >= 0) {
            uint8_t nid = pending[pi].target_nid;
            int n = at_fmt_relay_ack(buf, sizeof(buf), nid, relay_id, state, pending[pi].seq);
            if (n > 0) uart_send(buf);
            free_pending(pi);
        } else {
            int idx = find_or_add_node(src, NODE_TYPE_ACTUATOR);
            if (idx >= 0) {
                int n = at_fmt_relay_report(buf, sizeof(buf), node_table[idx].node_id, relay_id, state);
                if (n > 0) uart_send(buf);
            }
        }
        break;
    }

    // ── PONG ─────────────────────────────────────────────────────
    case MSG_PONG: {
        int pi;

        // Two-phase GET_TEMP: PONG → switch to temp-request phase
        if ((pi = find_pending(PEND_PING, src)) >= 0) {
            pending[pi].state   = PEND_TEMP;
            pending[pi].started = millis();
            uint8_t req = 0xFF;
            (void)mesher->Send(src, std::vector<uint8_t>(&req, &req + 1));
        }
        // Plain AT+PING: PONG completes the request
        else if ((pi = find_pending(PEND_PONG, src)) >= 0) {
            int idx = find_or_add_node(src, 0);
            if (idx >= 0) {
                int n = at_fmt_pong(buf, sizeof(buf), node_table[idx].node_id, pending[pi].seq);
                if (n > 0) uart_send(buf);
                free_pending(pi);
            }
        }
        // Heartbeat tracking
        else if (hb_pending.waiting) {
            hb_pending.responded++;
        }
        break;
    }
    }
}

// ══════════════════════════════════════════════════════════════════
// Arduino entry points
// ══════════════════════════════════════════════════════════════════

void setup() {
    Serial.begin(115200);
    delay(1000);
    Serial.println("\n=== AgriMeshAI LoRa Gateway ===");
    setup_loRa();
    Serial.println("=== Ready ===");
}

void serialEvent() {
    while (Serial.available()) {
        uart_rx.push((uint8_t)Serial.read());
    }
}

void loop() {
    // Process UART input
    static char line_buf[256];
    int len = uart_rx.read_line(line_buf, sizeof(line_buf));
    if (len > 0) {
        handle_command(line_buf);
    }

    reap_pending();

    // Heartbeat: PING all actuator nodes every HEARTBEAT_INTERVAL_MS
    uint32_t now = millis();
    if (mesher && (now - last_hb_ms >= HEARTBEAT_INTERVAL_MS)) {
        last_hb_ms = now;
        uint8_t cnt = 0;
        for (uint8_t i = 0; i < node_count; i++) {
            if (node_table[i].active && node_table[i].node_type == NODE_TYPE_ACTUATOR) {
                uint8_t ping = MSG_PING;
                (void)mesher->Send(node_table[i].lora_addr, std::vector<uint8_t>(&ping, &ping + 1));
                cnt++;
            }
        }
        if (cnt > 0) {
            hb_pending.seq       = hb_seq++;
            hb_pending.count     = cnt;
            hb_pending.responded = 0;
            hb_pending.started   = now;
            hb_pending.waiting   = true;
        }
    }

    // Heartbeat reaper
    if (hb_pending.waiting && (now - hb_pending.started > PEND_TIMEOUT_MS)) {
        char buf[64];
        at_fmt_hb(buf, sizeof(buf), hb_pending.responded, hb_pending.count, hb_pending.seq);
        uart_send(buf);
        hb_pending.waiting = false;
    }

    vTaskDelay(pdMS_TO_TICKS(10));
}

// ══════════════════════════════════════════════════════════════════
// AT command dispatch
// ══════════════════════════════════════════════════════════════════

static void handle_command(const char* line) {
    char buf[128];
    uint8_t  node_id = 0, seq = 0, relay_id = 0, cmd = 0;
    uint32_t duration = 0;

    // ── AT+GET_TEMP=<id>,SEQ=<n> ────────────────────────────────
    if (at_starts_with(line, AT_GET_TEMP)) {
        if (at_parse_id_seq(line, node_id, seq) != 0) {
            send_error(AT_ERR_INVALID_PARAMS, "bad fmt", seq); return;
        }
        if (!mesher) {
            send_error(AT_ERR_MESH_NOT_READY, "not ready", seq); return;
        }
        int idx = find_node_by_id(node_id);
        if (idx < 0) {
            send_error(AT_ERR_NODE_NOT_FOUND, "not found", seq); return;
        }
        int p = alloc_pending();
        if (p < 0) {
            send_error(AT_ERR_UART_BUF_FULL, "pending full", seq); return;
        }
        pending[p] = {seq, node_id, node_table[idx].lora_addr, PEND_PING, millis()};
        uint8_t ping = MSG_PING;
        (void)mesher->Send(node_table[idx].lora_addr, std::vector<uint8_t>(&ping, &ping + 1));
        return;
    }

    // ── AT+SET_RELAY=<id>,<relay>,<state>,<dur_s>,SEQ=<n> ──────
    if (at_starts_with(line, AT_SET_RELAY)) {
        if (at_parse_relay(line, node_id, relay_id, cmd, duration, seq) != 0) {
            send_error(AT_ERR_INVALID_PARAMS, "bad fmt", seq); return;
        }
        if (relay_id >= 4) {
            send_error(AT_ERR_INVALID_PARAMS, "relay_id 0-3 only", seq); return;
        }
        if (!mesher) {
            send_error(AT_ERR_MESH_NOT_READY, "not ready", seq); return;
        }
        int idx = find_node_by_id(node_id);
        if (idx < 0) {
            send_error(AT_ERR_NODE_NOT_FOUND, "not found", seq); return;
        }
        if (duration > MAX_ON_DURATION_MS) duration = MAX_ON_DURATION_MS;

        int p = alloc_pending();
        if (p < 0) {
            send_error(AT_ERR_UART_BUF_FULL, "pending full", seq); return;
        }
        pending[p] = {seq, node_id, node_table[idx].lora_addr, PEND_ACK, millis()};

        RelayCmdPacket rp;
        rp.relay_id    = relay_id;
        rp.cmd         = cmd;
        rp.duration_ms = duration;
        (void)mesher->Send(node_table[idx].lora_addr,
                           std::vector<uint8_t>((uint8_t*)&rp, ((uint8_t*)&rp) + sizeof(rp)));
        return;
    }

    // ── AT+PING=<id>,SEQ=<n> ────────────────────────────────────
    if (at_starts_with(line, AT_PING) && !at_starts_with(line, AT_PING_ALL)) {
        if (at_parse_id_seq(line, node_id, seq) != 0) {
            send_error(AT_ERR_INVALID_PARAMS, "bad fmt", seq); return;
        }
        if (!mesher) {
            send_error(AT_ERR_MESH_NOT_READY, "not ready", seq); return;
        }
        int idx = find_node_by_id(node_id);
        if (idx < 0) {
            send_error(AT_ERR_NODE_NOT_FOUND, "not found", seq); return;
        }
        int p = alloc_pending();
        if (p < 0) {
            send_error(AT_ERR_UART_BUF_FULL, "pending full", seq); return;
        }
        pending[p] = {seq, node_id, node_table[idx].lora_addr, PEND_PONG, millis()};
        uint8_t ping = MSG_PING;
        (void)mesher->Send(node_table[idx].lora_addr, std::vector<uint8_t>(&ping, &ping + 1));
        return;
    }

    // ── AT+PING_ALL,SEQ=<n> ─────────────────────────────────────
    if (at_starts_with(line, AT_PING_ALL)) {
        seq = parse_seq(line);
        if (!mesher) {
            send_error(AT_ERR_MESH_NOT_READY, "not ready", seq); return;
        }
        uint8_t cnt = 0;
        for (uint8_t i = 0; i < node_count; i++) {
            if (node_table[i].active && node_table[i].node_type == NODE_TYPE_ACTUATOR) {
                uint8_t ping = MSG_PING;
                (void)mesher->Send(node_table[i].lora_addr, std::vector<uint8_t>(&ping, &ping + 1));
                cnt++;
            }
        }
        if (cnt > 0) {
            hb_pending.seq       = seq;
            hb_pending.count     = cnt;
            hb_pending.responded = 0;
            hb_pending.started   = millis();
            hb_pending.waiting   = true;
        } else {
            char ob[48];
            at_fmt_hb(ob, sizeof(ob), 0, 0, seq);
            uart_send(ob);
        }
        return;
    }

    // ── AT+LIST_NODES,SEQ=<n> ────────────────────────────────────
    if (at_starts_with(line, AT_LIST_NODES)) {
        seq = parse_seq(line);
        int pos = snprintf(buf, sizeof(buf), "+NODES:%d", node_count);
        for (uint8_t i = 0; i < node_count; i++) {
            pos += snprintf(buf + pos, sizeof(buf) - pos, ",%d,%d",
                           node_table[i].node_id, node_table[i].node_type);
        }
        pos += snprintf(buf + pos, sizeof(buf) - pos, ",SEQ=%d\r\n", seq);
        uart_send(buf);
        return;
    }

    // ── AT+NODE_INIT,SEQ=<n> ────────────────────────────────────
    if (at_starts_with(line, AT_NODE_INIT)) {
        seq = parse_seq(line);
        snprintf(buf, sizeof(buf), "+NODE_INIT:OK,SEQ=%d\r\n", seq);
        uart_send(buf);
        return;
    }

    // ── AT+NODE_ACK=<lora_addr>,<node_id>,SEQ=<n> ────────────────
    if (at_starts_with(line, AT_NODE_ACK)) {
        uint16_t lora_addr = 0;
        uint8_t  new_id = 0;
        if (sscanf(line, "AT+NODE_ACK=%hx,%hhu", &lora_addr, &new_id) >= 2) {
            seq = parse_seq(line);
            bool found = false;
            for (uint8_t i = 0; i < node_count; i++) {
                if (node_table[i].lora_addr == lora_addr) {
                    node_table[i].node_id = new_id;
                    found = true;
                    break;
                }
            }
            if (found) {
                snprintf(buf, sizeof(buf), "+NODE_ACK:OK,SEQ=%d\r\n", seq);
                uart_send(buf);
            } else {
                send_error(AT_ERR_NODE_NOT_FOUND, "unknown addr", seq);
            }
        } else {
            send_error(AT_ERR_INVALID_PARAMS, "bad fmt", parse_seq(line));
        }
        return;
    }
}

// ══════════════════════════════════════════════════════════════════
// Node table
// ══════════════════════════════════════════════════════════════════

static int find_or_add_node(AddressType addr, uint8_t ntype) {
    for (uint8_t i = 0; i < node_count; i++) {
        if (node_table[i].lora_addr == addr) return i;
    }
    if (node_count >= MAX_NODES) {
        char buf[64];
        snprintf(buf, sizeof(buf), "+ERR:4,table full addr=0x%04X\r\n", addr);
        uart_send(buf);
        return -1;
    }
    node_table[node_count] = {addr, 0, ntype, true};
    return node_count++;
}

static int find_node_by_id(uint8_t id) {
    for (uint8_t i = 0; i < node_count; i++) {
        if (node_table[i].node_id == id && node_table[i].active) return i;
    }
    return -1;
}

// ══════════════════════════════════════════════════════════════════
// Pending request slots (4 max)
// ══════════════════════════════════════════════════════════════════

static int alloc_pending() {
    for (int i = 0; i < MAX_PENDING; i++) {
        if (pending[i].state == PEND_IDLE) {
            pending[i] = {};
            return i;
        }
    }
    return -1;
}

static void free_pending(int idx) {
    if (idx >= 0 && idx < MAX_PENDING) pending[idx].state = PEND_IDLE;
}

// Finds a pending slot matching BOTH state AND source address.
// Needed because multiple concurrent requests to different nodes
// can share the same PendState (e.g. two pending GET_TEMPs).
static int find_pending(PendState state, AddressType addr) {
    for (int i = 0; i < MAX_PENDING; i++) {
        if (pending[i].state == state && pending[i].target_addr == addr) return i;
    }
    return -1;
}

static void reap_pending() {
    uint32_t now = millis();
    for (int i = 0; i < MAX_PENDING; i++) {
        if (pending[i].state != PEND_IDLE && (now - pending[i].started > PEND_TIMEOUT_MS)) {
            send_error(AT_ERR_TIMEOUT, "timeout", pending[i].seq);
            pending[i].state = PEND_IDLE;
        }
    }
}

// ══════════════════════════════════════════════════════════════════
// LoRa init
// ══════════════════════════════════════════════════════════════════

static void setup_loRa() {
    // Heltec WiFi LoRa 32 V3 pinout
    PinConfig pinConfig(8, 12, 14, 13, 9, 11, 10);

    RadioConfig radioConfig(RadioType::kSx1262, 868.0F, 12, 125.0F, 7, 14);
    radioConfig.setTcxoVoltage(1.8F);

    LoRaMeshProtocolConfig meshConfig;

    mesher = LoraMesher::Builder()
                 .withRadioConfig(radioConfig)
                 .withPinConfig(pinConfig)
                 .withLoRaMeshProtocol(meshConfig)
                 .withNodeAddress(GATEWAY_LORA_ADDR)
                 .withNodeCapabilities(NodeCapabilities::GATEWAY)
                 .Build();

    mesher->SetDataCallback(on_loRa_data);

    Result r = mesher->Start();
    if (r) {
        Serial.printf("[LoRa] OK  addr=0x%04X\n", mesher->GetNodeAddress());
    } else {
        Serial.printf("[LoRa] FAILED: %s\n", r.GetErrorMessage());
    }
}

// ══════════════════════════════════════════════════════════════════
// UART TX — flow control, non-blocking
// ══════════════════════════════════════════════════════════════════

static void uart_send(const char* buf) {
    if (!buf || !*buf) return;
    uint32_t now = millis();
    if (now - last_tx_ms < UART_TX_GAP_MS) {
        vTaskDelay(pdMS_TO_TICKS(UART_TX_GAP_MS - (now - last_tx_ms)));
    }
    Serial.print(buf);
    last_tx_ms = millis();
}
