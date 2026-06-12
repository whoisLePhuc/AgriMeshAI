// at_protocol.h — AT Command Protocol Constants & Helpers
// Used by LoRa Gateway firmware to parse/serialize UART messages.
#pragma once

#include <cstddef>
#include <cstdint>
#include <cstdio>

// ── AT Command strings (Edge → Gateway) ──────────────────────────

#define AT_GET_TEMP     "AT+GET_TEMP"
#define AT_SET_RELAY    "AT+SET_RELAY"
#define AT_PING         "AT+PING"
#define AT_PING_ALL     "AT+PING_ALL"
#define AT_LIST_NODES   "AT+LIST_NODES"
#define AT_NODE_INIT    "AT+NODE_INIT"
#define AT_NODE_ACK     "AT+NODE_ACK"

// ── Response prefixes (Gateway → Edge) ────────────────────────────

#define RESP_TEMP         "+TEMP"
#define RESP_TEMP_REPORT  "+TEMP_REPORT"
#define RESP_RELAY_ACK    "+RELAY_ACK"
#define RESP_RELAY_REPORT "+RELAY_REPORT"
#define RESP_PONG         "+PONG"
#define RESP_NODES        "+NODES"
#define RESP_NODE_JOIN    "+NODE_JOIN"
#define RESP_NODE_INIT_OK "+NODE_INIT:OK"
#define RESP_NODE_ACK_OK  "+NODE_ACK:OK"
#define RESP_ERR          "+ERR"

// ── Error codes ──────────────────────────────────────────────────

enum AtError : uint8_t {
    AT_ERR_NODE_NOT_FOUND = 1,
    AT_ERR_TIMEOUT        = 2,
    AT_ERR_INVALID_PARAMS = 3,
    AT_ERR_UART_BUF_FULL  = 4,
    AT_ERR_MESH_NOT_READY = 5,
};

// ── Parser helpers ───────────────────────────────────────────────

// Check if line starts with prefix. Returns true if match.
inline bool at_starts_with(const char* line, const char* prefix) {
    while (*prefix) {
        if (*line != *prefix) return false;
        line++; prefix++;
    }
    return true;
}

// Parse "AT+GET_TEMP=<id>,SEQ=<seq>" or "AT+PING=<id>,SEQ=<seq>"
// Returns parsed node_id (0 if error), seq output is optional.
inline int at_parse_id_seq(const char* line, uint8_t& node_id, uint8_t& seq) {
    // Move past command prefix to first '='
    const char* p = line;
    while (*p && *p != '=') p++;
    if (*p != '=') return -1;
    p++; // skip '='

    // Parse node_id
    node_id = 0;
    while (*p >= '0' && *p <= '9') {
        node_id = node_id * 10 + (uint8_t)(*p - '0');
        p++;
    }

    // Skip to SEQ=
    seq = 0;
    while (*p && *p != 'S') p++;
    if (*p == 'S' && p[1] == 'E' && p[2] == 'Q' && p[3] == '=') {
        p += 4; // skip "SEQ="
        while (*p >= '0' && *p <= '9') {
            seq = seq * 10 + (uint8_t)(*p - '0');
            p++;
        }
    }
    return 0;
}

// Parse "AT+SET_RELAY=<node>,<relay>,<cmd>,<dur>,SEQ=<seq>"
inline int at_parse_relay(const char* line, uint8_t& node_id, uint8_t& relay_id,
                           uint8_t& cmd, uint32_t& duration, uint8_t& seq) {
    const char* p = line;
    while (*p && *p != '=') p++;
    if (*p != '=') return -1;
    p++;

    // node_id
    node_id = 0;
    while (*p >= '0' && *p <= '9') { node_id = node_id * 10 + (uint8_t)(*p - '0'); p++; }
    if (*p != ',') return -1; p++;

    // relay_id
    relay_id = 0;
    while (*p >= '0' && *p <= '9') { relay_id = relay_id * 10 + (uint8_t)(*p - '0'); p++; }
    if (*p != ',') return -1; p++;

    // cmd (state: 0 or 1)
    cmd = (uint8_t)(*p - '0');
    p++;
    if (*p != ',') return -1; p++;

    // duration_s → ms
    duration = 0;
    while (*p >= '0' && *p <= '9') { duration = duration * 10 + (uint32_t)(*p - '0'); p++; }
    duration *= 1000; // convert seconds to ms

    // SEQ
    seq = 0;
    while (*p && *p != 'S') p++;
    if (*p == 'S' && p[1] == 'E' && p[2] == 'Q' && p[3] == '=') {
        p += 4;
        while (*p >= '0' && *p <= '9') { seq = seq * 10 + (uint8_t)(*p - '0'); p++; }
    }
    return 0;
}

// ── Response builders ────────────────────────────────────────────

// Build "+TEMP:1,0,25.3,SEQ=42\r\n"
inline int at_fmt_temp(char* buf, size_t sz, uint8_t node_id, uint8_t sensor_id,
                        float value, uint8_t seq) {
    return snprintf(buf, sz, "+TEMP:%d,%d,%.1f,SEQ=%d\r\n",
                    node_id, sensor_id, value, seq);
}

// Build "+TEMP_REPORT:1,0,25.3\r\n" (unsolicited — no SEQ)
inline int at_fmt_temp_report(char* buf, size_t sz, uint8_t node_id,
                               uint8_t sensor_id, float value) {
    return snprintf(buf, sz, "+TEMP_REPORT:%d,%d,%.1f\r\n",
                    node_id, sensor_id, value);
}

// Build "+RELAY_ACK:2,0,ON,SEQ=42\r\n"
inline int at_fmt_relay_ack(char* buf, size_t sz, uint8_t node_id,
                             uint8_t relay_id, uint8_t state, uint8_t seq) {
    return snprintf(buf, sz, "+RELAY_ACK:%d,%d,%s,SEQ=%d\r\n",
                    node_id, relay_id, state ? "ON" : "OFF", seq);
}

// Build "+RELAY_REPORT:2,0,ON\r\n" (unsolicited — no SEQ)
inline int at_fmt_relay_report(char* buf, size_t sz, uint8_t node_id,
                                uint8_t relay_id, uint8_t state) {
    return snprintf(buf, sz, "+RELAY_REPORT:%d,%d,%s\r\n",
                    node_id, relay_id, state ? "ON" : "OFF");
}

// Build "+PONG:2,SEQ=42\r\n"
inline int at_fmt_pong(char* buf, size_t sz, uint8_t node_id, uint8_t seq) {
    return snprintf(buf, sz, "+PONG:%d,SEQ=%d\r\n", node_id, seq);
}

// Build "+ERR:2,timeout,SEQ=42\r\n"
inline int at_fmt_error(char* buf, size_t sz, uint8_t code, const char* msg, uint8_t seq) {
    return snprintf(buf, sz, "+ERR:%d,%s,SEQ=%d\r\n", code, msg, seq);
}

// Build "+NODE_JOIN:0xABCD,0,1.0\r\n" (unsolicited)
inline int at_fmt_node_join(char* buf, size_t sz, uint16_t lora_addr,
                             uint8_t node_type, uint8_t fw_ver) {
    return snprintf(buf, sz, "+NODE_JOIN:0x%04X,%d,%d.%d\r\n",
                    lora_addr, node_type, fw_ver >> 4, fw_ver & 0x0F);
}

// Build "+NODES:3,1,0,2,1,3,0,SEQ=42\r\n"
// nodes[] = {id1, type1, id2, type2, ...}
inline int at_fmt_nodes(char* buf, size_t sz, const uint8_t* nodes, uint8_t count, uint8_t seq) {
    int pos = snprintf(buf, sz, "+NODES:%d", count);
    for (uint8_t i = 0; i < count; i++) {
        pos += snprintf(buf + pos, sz - pos, ",%d,%d", nodes[i*2], nodes[i*2+1]);
    }
    pos += snprintf(buf + pos, sz - pos, ",SEQ=%d\r\n", seq);
    return pos;
}
